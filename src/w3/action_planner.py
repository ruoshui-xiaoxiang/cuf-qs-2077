"""W3 动作规划器（S29：LLM 自主行动渐进部署——影子模式→自主模式）。

认知体自主行动：planner 依据当轮观测（实体/事件/公理状态/玩家位置）
输出动作建议（攻击哪个目标 / 移动到哪）。支持两种模式：

- shadow（旁观学习）：建议**只记录不执行**（proposed_action 归档 +
  可执行性校验标记），实际动作仍走默认编排。用于测验 LLM 建议的
  质量与安全性，达标后切换 active。
- active（自主行动）：建议经**白名单 + 位置约束**硬校验，通过则
  执行，被拒则回退默认编排。

反套壳对齐（S29 锁定）：
- planner 只决定"动作选择"（行动权下放），**真值判定仍归
  rule_validator**（反例/降级/QS/Γ脆 全在 W1/W2 硬编码）；
- 建议受双硬约束：动作类型白名单（attack/move + 参数本地校验）+
  固定实验区域位置约束（超出即拒绝，S7 教训）；
- plan_fn 可注入（真实 LLM 生成建议），默认离线 heuristic 确定性
  策略——零模型也能跑（顶层不 import llm/torch，由调用方注入）。
"""

import json
import math
import os
import re

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "experiment_config.json"
)

ALLOWED_ACTIONS = ("attack", "move", "pickup", "kill")

# kill（native 通道）护栏：半径制，无位置约束（作用域为玩家为中心的
# 圆形半径，不产生越界位移）；口径与 executor KILL_RADIUS_* 一致。
KILL_RADIUS_MAX_M = 100.0
KILL_RADIUS_DEFAULT_M = 50.0

# S29.7：move 2D 化——8 方向步进（dx, dz），东/西/北/南交替
MOVE_STEPS = {0: (10.0, 0.0), 1: (-10.0, 0.0),
              2: (0.0, 10.0), 3: (0.0, -10.0)}
MOVE_MAX_STEP = 20.0  # 单步最大距离（dx/dz 绝对值上限，双硬约束）


def _dist(a, b):
    return max(abs(a.get("x", 0) - b.get("x", 0)),
               abs(a.get("y", 0) - b.get("y", 0)),
               abs(a.get("z", 0) - b.get("z", 0)))


def _euclid(a, b):
    """水平面欧氏距离——区域约束的度量口径（radius_m 语义为平面圆形
    区域；实机坐标系 z=高度，高度差不计入区域距离——抬升上落到天桥/
    屋顶等地形层仍属区域内）。切比雪夫 L∞ 会放行欧氏最远 √2×radius
    的平面点，约束被削弱。"""
    return math.sqrt((a.get("x", 0) - b.get("x", 0)) ** 2
                     + (a.get("y", 0) - b.get("y", 0)) ** 2)


def _pairwise_extract(text, start):
    """字符串感知括号配对：从 start 的 { 提取到匹配的 }，失败返回 None。"""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_first_json(text):
    """定位可解析的动作建议 JSON 对象。

    遍历文本中所有 "action_type" 出现点，从每个点向前找所属 { 起点
    做括号配对提取，返回**第一个能成功 json.loads 的 JSON 对象**。
    比朴素 re / 单点定位健壮：LLM 常回显观测（含 pos={'x': ...} 等
    单引号 Python dict）或产生坏前缀片段（如 constrained prefix
    引导下偶发的 '{"action_type": ")'），第一个 "action_type" 不
    一定是合法 JSON——逐个尝试直到找到可解析对象。
    """
    import json as _json

    search_from = 0
    while True:
        idx = text.find('"action_type"', search_from)
        if idx < 0:
            return None
        start = text.rfind("{", 0, idx)
        if start >= 0:
            seg = _pairwise_extract(text, start)
            if seg is not None:
                try:
                    obj = _json.loads(seg)
                except (ValueError, _json.JSONDecodeError):
                    obj = None
                if isinstance(obj, dict):
                    return seg
        search_from = idx + 1


def _parse_fields(text):
    """字段级容错提取（解码后引导）：JSON 提取失败时，从模型文本中
    正则提取 action/target/dx。容忍半结构化输出（模型说了决策但 JSON
    不完整/被截断）。任何关键字段缺失即返回 None（调用方回退
    heuristic，不硬编码猜测决策）。"""
    m = re.search(r'"(?:action_type|action)"\s*:\s*"(attack|move|pickup|kill)"', text)
    if not m:
        # S29.17 F23：锚定行首——决策串格式为行级（`attack EID:x # 理由` /
        # `move dx dz`）；此前 \b 裸匹配可从回显/理由文本中误提取动作词，
        # 产出"合法但非意图"的建议。
        m = re.search(r"^\s*(attack|move|pickup|kill)\b", text, re.MULTILINE)
    if not m:
        return None
    action = m.group(1)
    target = None
    mt = re.search(r"EID:[A-Za-z0-9_]+", text)
    if mt:
        target = mt.group(0)
    dx = None
    md = re.search(r'"dx"\s*:\s*(-?\d+)', text)
    if md:
        dx = int(md.group(1))
    else:
        mm = re.search(r"^\s*move\s+(-?\d+)", text, re.MULTILINE)
        if mm:
            dx = int(mm.group(1))
    dz = 0
    mz = re.search(r'"dz"\s*:\s*(-?\d+)', text)
    if mz:
        dz = int(mz.group(1))
    else:
        mz2 = re.search(r"\bmove\s+-?\d+\s+(-?\d+)", text)
        if mz2:
            dz = int(mz2.group(1))
    if action in ("attack", "pickup") and not target:
        return None
    if action == "move" and dx is None:
        return None
    params = None
    if action == "kill":
        mr = re.search(r'"radius"\s*:\s*(\d+(?:\.\d+)?)', text)
        params = {"radius": float(mr.group(1))} if mr else None
    elif dx is not None:
        params = {"dx": dx, "dz": dz}
    reason = re.search(r'"reason"\s*:\s*"([^"]*)"', text)
    if not reason:
        mh = re.search(r"#\s*([^\n#]*)", text)
        if mh:
            return {"action_type": action,
                    "target_entity_id": target,
                    "params": params,
                    "reason": mh.group(1).strip(),
                    "advisory": True}
    return {"action_type": action,
            "target_entity_id": target,
            "params": params,
            "reason": reason.group(1) if reason else "",
            "advisory": True}


def parse_plan_json(text):
    """从 LLM 生成文本提取动作建议 JSON；失败返回 None（调用方回退
    heuristic）。容忍 ```json 包裹、回显噪声与多余文字——容错属于
    生产逻辑。提取顺序：完整 JSON 对象 → 字段级容错 → None。"""
    if not text:
        return None
    segment = _extract_first_json(text)
    if segment is not None:
        try:
            plan = json.loads(segment)
        except (json.JSONDecodeError, ValueError):
            plan = None
        if isinstance(plan, dict) and \
                plan.get("action_type") in ALLOWED_ACTIONS:
            if plan["action_type"] not in ("attack", "pickup") or \
                    plan.get("target_entity_id"):
                return {"action_type": plan["action_type"],
                        "target_entity_id": plan.get("target_entity_id"),
                        "params": plan.get("params") or None,
                        "reason": str(plan.get("reason", "")),
                        "advisory": True}
    return _parse_fields(text)


class ActionPlanner:
    """动作规划器：观测 → 动作建议（双模式渐进部署）。"""

    def __init__(self, mode="shadow", plan_fn=None,
                 allowed_actions=ALLOWED_ACTIONS,
                 area_center=None, area_radius=None):
        if mode not in ("shadow", "active"):
            raise ValueError(f"invalid planner mode: {mode}")
        self.mode = mode
        # plan_fn(planner, observation, round_id) -> plan dict 或 None
        self.plan_fn = plan_fn
        self.allowed_actions = tuple(allowed_actions)
        # 固定实验区域约束（None = 不启用位置约束）
        self.area_center = dict(area_center or {})
        self.area_radius = area_radius
        self._step = 0  # heuristic 移动步进（东/西交替）

    @classmethod
    def from_config(cls, config_path=None, plan_fn=None):
        path = config_path or DEFAULT_CONFIG_PATH
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        pl = cfg.get("planner", {}) or {}
        area = pl.get("area", {}) or {}
        return cls(
            mode=pl.get("mode", "shadow"),
            plan_fn=plan_fn,  # LLM 由调用方注入 plan_fn
            allowed_actions=pl.get("allowed_actions", ALLOWED_ACTIONS),
            area_center=area.get("center"),
            area_radius=area.get("radius_m"),
        )

    # ── 规划入口 ──────────────────────────────────────────────

    def plan(self, observation, round_id):
        """生成动作建议（结构化的 plan dict，advisory=True）。"""
        if self.plan_fn is not None:
            proposed = self.plan_fn(self, observation, round_id)
            if proposed is None:
                proposed = self._default_plan(observation)
            if isinstance(proposed, dict) and "action_type" in proposed:
                return proposed
        return self._default_plan(observation)

    # ── 默认策略（离线确定性 heuristic，零模型）──────────────

    def _default_plan(self, observation):
        """默认策略：存在可攻击实体 → 攻击最近；否则存在可拾取物品 →
        拾取最近；否则 → 2D 交替移动。确定性（无随机），可复现。"""
        entities = observation.get("entities", []) or []
        others = [e for e in entities
                  if e.get("kind") not in ("player", "item")
                  and e.get("entity_id") not in ("", None)]
        if others:
            # 取离玩家最近的非玩家实体
            player_pos = observation.get("player_position", {}) or {}
            target = min(others, key=lambda e: _dist(
                player_pos, e.get("position", {}) or {}))
            return {
                "action_type": "attack",
                "target_entity_id": target["entity_id"],
                "params": {},
                "reason": (f"heuristic: attack nearest non-player "
                           f"{target['entity_id']}"),
                "advisory": True,
            }
        items = [e for e in entities
                 if e.get("kind") == "item"
                 and e.get("entity_id") not in ("", None)]
        if items:
            player_pos = observation.get("player_position", {}) or {}
            target = min(items, key=lambda e: _dist(
                player_pos, e.get("position", {}) or {}))
            return {
                "action_type": "pickup",
                "target_entity_id": target["entity_id"],
                "params": {},
                "reason": f"heuristic: pickup nearest item {target['entity_id']}"
                          f"（=移出观测窗口；真拾取不可达，D3 构成①边界已落定）",
                "advisory": True,
            }
        sx, sz = MOVE_STEPS[self._step % len(MOVE_STEPS)]
        self._step += 1
        return {
            "action_type": "move",
            "target_entity_id": None,
            "params": {"dx": sx, "dz": sz},
            "reason": (f"heuristic: strafe dx={sx:+.0f} dz={sz:+.0f}m "
                       f"(no target)"),
            "advisory": True,
        }

    # ── 校验（白名单 + 位置约束，硬约束）────────────────────

    def validate(self, plan, observation, player_position=None):
        """校验建议是否可执行。返回 (ok, reason)。

        双硬约束：①动作类型白名单 + 参数检查；②attack 目标必须
        存在于当轮实体；③move 目标位置必须落在固定实验区域内
        （超出即拒绝——S7 固定区域教训）。
        """
        if not isinstance(plan, dict) or "action_type" not in plan:
            return False, "malformed plan"
        action_type = plan["action_type"]
        if action_type not in self.allowed_actions:
            return False, f"action not in whitelist: {action_type}"

        if action_type == "attack":
            tid = plan.get("target_entity_id")
            if not tid:
                return False, "attack missing target_entity_id"
            ents = observation.get("entities", []) or []
            ids = {e.get("entity_id") for e in ents}
            if tid not in ids:
                return False, f"attack target not in current scan: {tid}"
            # 硬约束：禁止攻击玩家自身（LLM 建议曾引用 EID:1 player，
            # 系统提示约束不够——白名单必须兜底）
            if any(e.get("entity_id") == tid and e.get("kind") == "player"
                   for e in ents):
                return False, f"attack target is player: {tid}"
            # S29.11 硬约束：禁止攻击 item（战利品容器/物品只能 pickup；
            # 实机 30 轮测试 LLM 连续 attack LootContainer 致 timeout 空转）
            if any(e.get("entity_id") == tid and e.get("kind") == "item"
                   for e in ents):
                return False, f"attack target is item (use pickup): {tid}"
            # S29.11 硬约束：禁止攻击 hp<=0 目标（已死/已毁实体反复攻击
            # 无事件 timeout 空转；实机 LLM 连续 attack hp=0 载具 13 轮）
            tgt = next((e for e in ents if e.get("entity_id") == tid), None)
            # S29.17 F23：排除 bool（True 会被 isinstance 当 1 漏检）；
            # 非数值 hp 无法判断 → 保守跳过检查（白名单其余约束兜底）。
            hp_val = tgt.get("hp") if tgt is not None else None
            if tgt is not None and isinstance(hp_val, (int, float)) \
                    and not isinstance(hp_val, bool) and hp_val <= 0:
                return False, f"attack target already destroyed (hp<=0): {tid}"
            return True, "whitelisted attack"

        if action_type == "pickup":
            tid = plan.get("target_entity_id")
            if not tid:
                return False, "pickup missing target_entity_id"
            ents = observation.get("entities", []) or []
            ids = {e.get("entity_id") for e in ents}
            if tid not in ids:
                return False, f"pickup target not in current scan: {tid}"
            # 硬约束：只允许拾取 item 类实体（不可拾取 NPC/player/设备）
            if not any(e.get("entity_id") == tid and e.get("kind") == "item"
                       for e in ents):
                return False, f"pickup target not item: {tid}"
            return True, "whitelisted pickup"

        if action_type == "kill":
            # kill 走 native 通道（kill_nearby 半径制）：无目标/无区域约束，
            # 参数本地校验兜底（位置/幅度护栏延续白名单精神）。
            params = plan.get("params") or {}
            r = params.get("radius", KILL_RADIUS_DEFAULT_M)
            if isinstance(r, bool) or not isinstance(r, (int, float)):
                return False, f"kill radius missing or non-numeric: {r!r}"
            if not (0.0 < float(r) <= KILL_RADIUS_MAX_M):
                return False, (f"kill radius out of range "
                               f"(0, {KILL_RADIUS_MAX_M:.0f}]: {r}")
            return True, "whitelisted kill"

        if action_type == "move":
            # S29.7：dx/dz 步长硬约束（|dx|,|dz| ≤ MOVE_MAX_STEP，
            # 防 LLM 建议超大瞬移破坏实验区域）。
            # S29.15：参数必须为数值——此前非数值/缺失能通过校验，
            # 到执行层 float() 崩溃、单条坏建议杀死整批实验；护栏
            # 必须在校验层拒绝并回退默认编排。
            params = plan.get("params") or {}
            move_args = {}
            for key in ("dx", "dz"):
                v = params.get(key)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return False, f"move {key} missing or non-numeric: {v!r}"
                if abs(float(v)) > MOVE_MAX_STEP:
                    return False, (f"move {key} out of range: "
                                   f"{v} (max ±{MOVE_MAX_STEP:.0f}m)")
                move_args[key] = float(v)
            # S29.17 F20：零位移不是有效移动建议——此前能通过校验且在
            # 执行层秒"完成"（arrival_eps 立即满足），虚增有效移动。
            if move_args["dx"] == 0.0 and move_args["dz"] == 0.0:
                return False, "move zero displacement (dx=dz=0)"
            # S29.15：区域约束校验的是**落点** = 当前位置 + (dx, dz)，
            # 而非当前位置（此前每步可合法越界 ≤MOVE_MAX_STEP）；
            # 实机坐标系 x=东 y=北 z=高度：落点映射 dx→x、dz→y，
            # 高度不参与区域距离（_euclid 水平面口径）。
            if self.area_center is not None and self.area_radius is not None:
                if not player_position:
                    return False, "move missing position for area check"
                goal = {
                    "x": player_position.get("x", 0.0) + move_args["dx"],
                    "y": player_position.get("y", 0.0) + move_args["dz"],
                    "z": player_position.get("z", 0.0),
                }
                d = _euclid(self.area_center, goal)
                if d > self.area_radius:
                    return False, (
                        f"move landing point outside fixed area "
                        f"(dist={d:.1f}m > {self.area_radius}m)")
            return True, "whitelisted move"

        return False, f"unsupported action: {action_type}"

    # ── 状态查询 ──────────────────────────────────────────────

    def get_status(self):
        return {
            "mode": self.mode,
            "plan_fn_injected": self.plan_fn is not None,
            "allowed_actions": list(self.allowed_actions),
            "area_constraint": bool(self.area_center and self.area_radius),
        }
