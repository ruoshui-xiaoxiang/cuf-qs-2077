import json
import logging
import time
from pathlib import Path

from common.utils import dist3, is_num
from w0.game_connector import GameConnectorError

log = logging.getLogger("action_executor")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "experiment_config.json"

ACTION_WHITELIST = ("attack", "move", "pickup", "kill")

ACTION_SPECS = {
    "attack": {"fn": "player_attack", "channel": "connector"},
    "move": {"fn": "player_move", "channel": "connector"},
    "pickup": {"fn": "player_pickup", "channel": "connector"},
    # kill 走 CufNative 通道（kill_nearby），不经 CufBridge；
    # CET 状态效果攻击不可靠（T1 燃烧实验 HP 不降），native 击杀已实机验证。
    "kill": {"fn": "kill_nearby", "channel": "native"},
}

# 控制面分层（2026-08-27 接手收口）：
# - stable: 可作为默认正式实验动作
# - conditional: 能执行，但结果语义必须结合独立观测解释
# - experimental: 实验性增强动作，默认不应混入正式批次主线
ACTION_CONTROL_TIERS = {
    "move": "stable",
    "pickup": "conditional",
    "attack": "conditional",
    "kill": "experimental",
}

ATTACK_EVIDENCE_KINDS = ("combat_attack", "hp_changed", "entity_death")

# pickup 完成证据（D3 构成①真拾取语义，CufBridge 0.6.8）：
# Lua 侧双路径——真拾取（读容器战利品清单 → AddToInventory →
# GetItemQuantity 数量增加验证通过）推 item_acquired；仅移出观测窗口
# （teleport_away，背包无变化）推 item_remove。账目口径按回执 reason
# 区分标注，两种证据 kind 均兼容历史 Lua（item_picked_up 回执按
# item_remove 记账）。
PICKUP_EVIDENCE_KINDS = ("item_acquired", "item_remove")
PICKUP_REMOVE_NOTE = ("item_remove: 物品从观测窗口消失（teleport_away 移出地下），"
                      "非背包获得；D3 构成①边界结论：真拾取在当前 CET 暴露面"
                      "不可达（2026-08-26 三轮实机考古钉死，口径永久落定）")
PICKUP_ACQUIRED_NOTE = ("item_acquired: 真拾取（背包数量增加经 Lua 侧验证），"
                        "容器同时移出观测窗口")


def _pickup_semantics(receipt):
    if isinstance(receipt, dict) and receipt.get("reason") == "item_acquired":
        return PICKUP_ACQUIRED_NOTE
    return PICKUP_REMOVE_NOTE


def _pickup_semantics_code(receipt):
    if isinstance(receipt, dict) and receipt.get("reason") == "item_acquired":
        return "item_acquired"
    return "item_remove"


def _attack_semantics_code(evidence):
    kinds = {ev.get("kind") for ev in (evidence or []) if isinstance(ev, dict)}
    if "entity_death" in kinds:
        return "death_observed"
    if "hp_changed" in kinds:
        return "damage_observed"
    if "combat_attack" in kinds:
        return "combat_signal_observed"
    return "attack_evidence_observed"

_CONFIG_KEYS = ("poll_interval_s", "attack_timeout_s", "move_timeout_s",
                "kill_timeout_s", "arrival_eps_m", "move_min_progress_m")

# kill 护栏（白名单精神延续）：半径上限与击杀后复检参数。
KILL_RADIUS_MAX_M = 100.0
KILL_RADIUS_DEFAULT_M = 50.0
# 击杀后独立观测取证（铁律 #1）：延迟首查 + 重试，目标从枚举消失即死亡成立。
KILL_EVIDENCE_DELAY_S = 3.0
KILL_EVIDENCE_RETRY = 3
KILL_EVIDENCE_RETRY_S = 2.0

# S29.11 水域防护：move 落点 z 与目标 z 偏差超过该值即视为落入
# 异常地形（水域/穿地），回退原位。实机溺水事件：z 8→-11（偏差 19m），
# 收窄到 6m 可捕获水域渐变落差。
# 地形感知移动（2026-08-26）：move 改为「小跳+沉降」协议——目标点
# 在当前位置水平偏移基础上抬升 MOVE_HOP_RISE_M 传送，由游戏物理把玩家
# 沉降到当地地面，落点 z 由实测读回而非标定（实机坐标系 z=高度，
# 地图起伏下任何固定高度标定都会进地下；CET 沙箱无射线/地形查询
# 接口，0.6.13/0.6.14 探针实机钉死，方案 3 工程化变体）。
# 守卫改为相对基线的深度带：落点跌到基线高度以下超过
# MOVE_GROUND_BAND_M 视为落入深坑/水域/虚空，回救基线位置。
# 抬升上落（高于基线）合法（天桥/屋顶等地形层）。
MOVE_HOP_RISE_M = 4.0
MOVE_GROUND_BAND_M = 12.0
MOVE_SETTLE_MIN_S = 1.2


def _validate_params(action_type, params):
    if action_type in ("attack", "pickup"):
        tid = params.get("target_id")
        if not isinstance(tid, str) or not tid:
            return "invalid_params:target_id must be non-empty str"
        return None
    if action_type == "kill":
        r = params.get("radius", KILL_RADIUS_DEFAULT_M)
        if isinstance(r, bool) or not is_num(r):
            return "invalid_params:radius must be number"
        if not (0.0 < float(r) <= KILL_RADIUS_MAX_M):
            return (f"invalid_params:radius out of range "
                    f"(0, {KILL_RADIUS_MAX_M:.0f}]: {r}")
        return None
    for k in ("x", "y", "z"):
        if not is_num(params.get(k)):
            return f"invalid_params:{k} must be number"
    return None


def _is_attack_evidence(ev, target_id, kinds=ATTACK_EVIDENCE_KINDS):
    if not isinstance(ev, dict) or ev.get("kind") not in kinds:
        return False
    return ev.get("source_entity") == target_id or ev.get("target_entity") == target_id


def _as_list(resp, key):
    raw = resp.get(key) if isinstance(resp, dict) else resp
    return raw if isinstance(raw, list) else []


class ActionExecutor:
    def __init__(self, connector, poll_interval_s=0.2, attack_timeout_s=20.0,
                 move_timeout_s=15.0, kill_timeout_s=15.0, arrival_eps_m=1.0,
                 move_min_progress_m=0.5, native_client=None):
        # 裸构造默认值与 config actions 段一致（S29.17 P2：此前 8.0 与
        # 配置 20.0 漂移）；pickup 超时复用 attack 值（同为"等事件回执"
        # 语义，配置未单列）。native_client：CufNative 通道（kill 专用）。
        self.connector = connector
        self.poll_interval_s = float(poll_interval_s)
        self.default_timeouts = {"attack": float(attack_timeout_s),
                                 "move": float(move_timeout_s),
                                 "pickup": float(attack_timeout_s),
                                 "kill": float(kill_timeout_s)}
        self.arrival_eps_m = float(arrival_eps_m)
        self.move_min_progress_m = float(move_min_progress_m)
        self.native_client = native_client
        self._stashed_events = []

    @classmethod
    def from_config(cls, connector, path=None, native_client=None):
        cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")).get("actions", {})
        kwargs = {k: cfg[k] for k in _CONFIG_KEYS if k in cfg}
        if native_client is None:
            from common.native_client import NativeClient
            native_client = NativeClient.from_config(cfg_path)
        return cls(connector, native_client=native_client, **kwargs)

    @staticmethod
    def allowed_actions():
        return tuple(ACTION_WHITELIST)

    def pop_stashed_events(self):
        """返回并清空动作观测期间消费掉但与本动作不匹配的事件。

        审计修复：get_events 为读后清空语义，观测轮询中取到的非匹配事件
        原先被直接丢弃，造成数据流空洞；现暂存于此，由采集方合并回数据流。
        """
        evs = self._stashed_events
        self._stashed_events = []
        return evs

    def execute(self, action_type, params=None, timeout_s=None):
        t0 = time.perf_counter()
        params = dict(params or {})
        spec = ACTION_SPECS.get(action_type)
        result = {
            "action_type": action_type,
            "fn": None,
            "channel": spec.get("channel") if spec else None,
            "control_tier": ACTION_CONTROL_TIERS.get(action_type, "experimental"),
            "params": params,
            "action_id": None,
            "status": "rejected",
            "accepted": False,
            "reject_reason": None,
            "receipt_reason": None,
            "outcome_semantics": None,
            "confirmation_status": "not_started",
            "evidence": [],
            "polls": 0,
            "issued_ts": None,
            "duration_s": None,
        }
        if spec is None:
            result["reject_reason"] = "not_in_whitelist"
            result["duration_s"] = time.perf_counter() - t0
            log.warning("action %r blocked by whitelist, nothing sent", action_type)
            return result
        result["fn"] = spec["fn"]
        param_err = _validate_params(action_type, params)
        if param_err is not None:
            result["reject_reason"] = param_err
            result["duration_s"] = time.perf_counter() - t0
            return result
        timeout = self.default_timeouts[action_type] if timeout_s is None else float(timeout_s)
        if spec.get("channel") == "native":
            self._observe_kill(result, params, timeout)
            result["duration_s"] = time.perf_counter() - t0
            return result
        baseline = self._player_position() if action_type == "move" else None
        try:
            receipt = self.connector.execute_action(spec["fn"], dict(params))
        except GameConnectorError as e:
            result["status"] = "error"
            result["reject_reason"] = f"call_failed:{e}"
            result["duration_s"] = time.perf_counter() - t0
            return result
        result["issued_ts"] = time.time()
        if not isinstance(receipt, dict) or not isinstance(receipt.get("accepted"), bool):
            result["status"] = "error"
            result["reject_reason"] = "malformed_receipt"
            result["outcome_semantics"] = "malformed_receipt"
            result["duration_s"] = time.perf_counter() - t0
            return result
        result["action_id"] = receipt.get("action_id")
        result["accepted"] = receipt["accepted"]
        result["receipt_reason"] = receipt.get("reason")
        if not receipt["accepted"]:
            result["reject_reason"] = receipt.get("reason") or "rejected_by_mod"
            result["outcome_semantics"] = "rejected_by_bridge"
            result["duration_s"] = time.perf_counter() - t0
            return result
        result["confirmation_status"] = "pending_observation"
        if action_type == "attack":
            self._observe_attack(result, params["target_id"], timeout)
        elif action_type == "pickup":
            self._observe_attack(result, params["target_id"], timeout,
                                 evidence_kinds=PICKUP_EVIDENCE_KINDS,
                                 semantics=_pickup_semantics(receipt),
                                 semantics_code=_pickup_semantics_code(receipt))
        else:
            goal = (float(params["x"]), float(params["y"]), float(params["z"]))
            self._observe_move(result, goal, baseline, timeout)
        result["duration_s"] = time.perf_counter() - t0
        return result

    def _observe_kill(self, result, params, timeout_s):
        """kill（native 通道）三段证据闭环（铁律 #1）：
        ① 回执：kill_nearby 的 npcCount/method/entityHash/killMethod；
        ② 独立观测：延迟后 list_npcs 只读复检，目标 entityHash 从枚举
           消失 → entity_death 证据成立（EID:<hash> 与 CufBridge 口径统一）；
        ③ 目标仍在（如中立平民不死）→ evidence unconfirmed，不伪造死亡。
        """
        from common.native_client import NativeUnavailableError
        if self.native_client is None:
            result["status"] = "error"
            result["reject_reason"] = "native_unavailable"
            result["outcome_semantics"] = "native_unavailable"
            return
        radius = float(params.get("radius", KILL_RADIUS_DEFAULT_M))
        try:
            receipt = self.native_client.kill_nearby(radius)
        except NativeUnavailableError as e:
            result["status"] = "error"
            result["reject_reason"] = f"native_unavailable:{e}"
            result["outcome_semantics"] = "native_unavailable"
            return
        result["issued_ts"] = time.time()
        if receipt.get("error"):
            result["status"] = "error"
            result["reject_reason"] = f"native_error:{receipt['error']}"
            result["outcome_semantics"] = "native_error"
            return
        entity_hash = receipt.get("entityHash")
        if not entity_hash or not receipt.get("killMethod"):
            result["status"] = "error"
            result["reject_reason"] = "no_target_found"
            result["outcome_semantics"] = "no_target_found"
            return
        result["accepted"] = True
        result["status"] = "completed"
        result["receipt_reason"] = "native_kill_receipt"
        result["confirmation_status"] = "pending_observation"
        result["outcome_semantics"] = "kill_receipt_observed"
        target_id = f"EID:{entity_hash}"
        result["target_entity"] = target_id
        receipt_ev = {
            "kind": "kill_receipt",
            "target_entity": target_id,
            "npc_count": receipt.get("npcCount"),
            "method": receipt.get("method"),
            "is_hostile": receipt.get("isHostile"),
            "kill_method": receipt.get("killMethod"),
            "ts": time.time(),
        }
        result["evidence"].append(receipt_ev)
        # 独立观测取证：同半径只读复检（不触发新击杀）
        deadline = time.perf_counter() + max(
            0.0, timeout_s - KILL_EVIDENCE_DELAY_S)
        time.sleep(KILL_EVIDENCE_DELAY_S)
        for attempt in range(KILL_EVIDENCE_RETRY):
            gone = None
            try:
                listing = self.native_client.list_npcs(radius)
                hashes = {n.get("entityHash")
                          for n in listing.get("npcs", [])
                          if isinstance(n, dict)}
                gone = entity_hash not in hashes
            except NativeUnavailableError as e:
                log.warning("kill evidence recheck failed: %s", e)
            if gone:
                result["evidence"].append({
                    "kind": "entity_death",
                    "target_entity": target_id,
                    "confirmed_by": "list_npcs",
                    "attempt": attempt,
                    "ts": time.time(),
                })
                result["confirmation_status"] = "confirmed"
                result["outcome_semantics"] = "death_confirmed"
                return
            if not gone:
                result["polls"] += 1
            if time.perf_counter() >= deadline:
                break
            time.sleep(KILL_EVIDENCE_RETRY_S)
        # 目标未消失（或复检通道不可用）：不伪造死亡，只标 unconfirmed
        result["evidence"].append({
            "kind": "entity_death",
            "target_entity": target_id,
            "confirmed_by": "list_npcs",
            "unconfirmed": True,
            "ts": time.time(),
        })
        result["confirmation_status"] = "unconfirmed"
        if receipt_ev.get("is_hostile") is False:
            result["outcome_semantics"] = "death_unconfirmed_non_hostile_target"
        else:
            result["outcome_semantics"] = "death_unconfirmed"

    def _observe_attack(self, result, target_id, timeout_s,
                        evidence_kinds=ATTACK_EVIDENCE_KINDS, semantics=None,
                        semantics_code=None):
        deadline = time.perf_counter() + timeout_s
        while True:
            for ev in self._fetch_events():
                if _is_attack_evidence(ev, target_id, evidence_kinds):
                    result["evidence"].append(ev)
                else:
                    self._stashed_events.append(ev)
            if result["evidence"]:
                result["status"] = "completed"
                result["confirmation_status"] = "confirmed"
                if semantics:
                    result["semantics"] = semantics
                result["outcome_semantics"] = (
                    semantics_code or _attack_semantics_code(result["evidence"]))
                return
            result["polls"] += 1
            if time.perf_counter() >= deadline:
                result["status"] = "timeout"
                result["confirmation_status"] = "unconfirmed"
                result["outcome_semantics"] = (
                    "pickup_unconfirmed"
                    if evidence_kinds == PICKUP_EVIDENCE_KINDS
                    else "attack_unconfirmed")
                return
            time.sleep(self.poll_interval_s)

    def _observe_move(self, result, goal, baseline, timeout_s):
        deadline = time.perf_counter() + timeout_s
        baseline_z = baseline[2] if baseline is not None else None
        start_ts = time.perf_counter()
        last_z = None
        z_stable = False
        while True:
            pos = self._player_position()
            if pos is not None:
                result["evidence"].append(
                    {"ts": time.time(), "position": {"x": pos[0], "y": pos[1], "z": pos[2]}})
                # 地形感知守卫：落点跌到基线高度以下超过深度带 =
                # 深坑/水域/虚空，回救基线（抬升传送，避免回救再入地）。
                # 高于基线的落点合法（天桥/屋顶），抬升上落不设上限。
                if baseline_z is not None and \
                        pos[2] < baseline_z - MOVE_GROUND_BAND_M:
                    result["z_safe"] = False
                    result["status"] = "rolled_back"
                    result["confirmation_status"] = "unconfirmed"
                    result["outcome_semantics"] = "rolled_back_z_unsafe"
                    result["z_fallback"] = True
                    try:
                        self.connector.execute_action(
                            "player_move",
                            {"x": float(baseline[0]),
                             "y": float(baseline[1]),
                             "z": float(baseline_z) + MOVE_HOP_RISE_M,
                             "yaw": 0.0})
                    except Exception as e:
                        log.warning("move z-fallback failed: %s", e)
                    return
                # 沉降确认：传送瞬间完成但落地需若干帧，完成判定须等
                # ①最短沉降时间 ②相邻两次读数 z 稳定（已着地）——
                # 否则读到的是悬空目标高度而非地面高度。
                if last_z is not None and abs(pos[2] - last_z) < 0.5:
                    z_stable = True
                last_z = pos[2]
                settled = z_stable and \
                    (time.perf_counter() - start_ts) >= MOVE_SETTLE_MIN_S
                if settled and baseline is not None and \
                        dist3(pos, baseline) >= self.move_min_progress_m:
                    result["z_safe"] = True
                    result["status"] = "completed"
                    result["confirmation_status"] = "confirmed"
                    result["outcome_semantics"] = "position_observed"
                    return
                horiz = ((pos[0] - goal[0]) ** 2 + (pos[1] - goal[1]) ** 2) ** 0.5
                if settled and horiz <= self.arrival_eps_m:
                    result["z_safe"] = True
                    result["status"] = "completed"
                    result["confirmation_status"] = "confirmed"
                    result["outcome_semantics"] = "position_observed"
                    return
            result["polls"] += 1
            if time.perf_counter() >= deadline:
                result["status"] = "timeout"
                result["confirmation_status"] = "unconfirmed"
                result["outcome_semantics"] = "movement_unconfirmed"
                return
            time.sleep(self.poll_interval_s)

    def _fetch_events(self):
        try:
            resp = self.connector.execute_action("get_events")
        except GameConnectorError as e:
            log.warning("get_events failed during observation: %s", e)
            return []
        return _as_list(resp, "events")

    def _player_position(self):
        try:
            resp = self.connector.execute_action("scan_entities")
        except GameConnectorError as e:
            log.warning("scan_entities failed during observation: %s", e)
            return None
        for ent in _as_list(resp, "entities"):
            if not isinstance(ent, dict) or ent.get("kind") != "player":
                continue
            pos = ent.get("position")
            if not isinstance(pos, dict):
                continue
            vals = (pos.get("x"), pos.get("y"), pos.get("z"))
            if all(is_num(v) for v in vals):
                return tuple(float(v) for v in vals)
        return None
