"""S16 四层数据流串通——闭环实验运行器（W0→W1→W2→W3）。

完整闭环：公理注入 → W0 采集 → W3 动作 → 观测构造 → W1 反例判定
→ W2 降级（3 预警 / 5 触发 + 双门控保护，锁定决策 #4）→ g 重注入
→ QS 二次收敛 → Γ脆三判据轮末检查（外层 OR，锁定决策 #12）→ 虚拟寿命记录。

反套壳铁则对齐：
- 铁则 #1：全部裁决（反例判定 / 降级触发 / QS 收敛 / Γ脆判据）由硬编码
  模块完成；embedding_fn / state_fn 在 S13 模型对接前为确定性离线函数，
  实机路替换为 7B 隐状态读取，不改裁决路径。
- 铁则 #2：四层走结构化 JSON 链路，自然语言仅人类可读输出。
- 铁则 #4：每轮归档四层全量数据 + C_H 三项，因果链可追溯。

轮内时序（每轮）：
 1. dm.begin_round(round_id)                    轮内快照冻结（锁定决策 #13）
 2. g 注入（若 qs 无目标向量，用活跃公理集嵌入生成）
 3. W0 采集 collect_round
 4. 本轮全部实体标签并入 label_history（建观测前，供降级保护多样性闸门）
 5. W3 动作（奇数轮 attack / 偶数轮 move，白名单）
 6. 事件回流（executor 暂存事件 + 带 kind 的动作证据并入 W0 事件流）
 7. 活跃公理 × 实体 生成假设（W1 rule_validator 裁决反例）
 8. 反例 → dm.process_counterexample（计数 / 预警 / 降级 + 边界自动补全）
 9. 降级后 g 重注入（剩余活跃公理集嵌入；_trigger_g_recompute 只清历史）
10. 轮末对齐分 a_t（必须在重注入之后，保证 reset 轮也能计分）
11. dm.end_round()                              pending 状态轮间生效
12. recorder.record_round 四层归档 + C_H
13. Γ脆三判据检查（顺序 ①语义死亡 → ②功能死亡 → ③超预算，命中即终止）

S17 异常边界处理（故障分级，优雅降级不中断实验）：
- 剧情锁：动作回执 accepted=False → status=rejected，轮照常归档。
- 空实体：扫描为空 → 空轮照常归档（标 empty_scan），无假设/无反例。
- 加载异常（采集瞬时故障）：GameConnectorError → 重试 collect_retry_max
  次；若已断连则 wait_for_game(reconnect_wait_s) 等待游戏侧重连；
  持续不可用 → 抛 RoundUnavailable → 该轮优雅跳过（记 skipped_rounds）。
- 检查点：JSONL 已归档轮次即检查点；run(resume=True) 跳过已归档轮，
  从检查点后续轮恢复（崩溃重启场景）。
- 只有非游戏侧异常（程序错误）才置 interruption=True 中断实验。
"""

import json
import logging
import math
import sys
import time
import zlib
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from common.utils import player_pos_from, ContextOverflowError
from w0.game_connector import GameConnectorError
from w1.rule_validator import RuleValidator
from w2.axiom_activator import ActivationEngine
from w2.axiom_engine import AxiomEngine
from w2.downgrade_manager import DowngradeManager
from w2.icc_auditor import ICCAuditor
from w2.qs_kernel import QSKernel
from w3.action_planner import ActionPlanner
from w3.action_executor import MOVE_HOP_RISE_M

log = logging.getLogger("closed_loop_runner")

DEFAULT_CONFIG_PATH = _SRC.parent / "config" / "experiment_config.json"

_EMBED_DIM = 32
_LCG_A = 1664525
_LCG_C = 1013904223
_LCG_MOD = 2 ** 32


# ── 离线双路嵌入（锁定决策 #1 在 S13 前的注入口径）────────

def deterministic_embedding(axiom_ids):
    """确定性公理集嵌入（离线路）：顺序无关、跨进程可复现、32 维非零。

    每个公理 ID 以 crc32 为种子走 LCG 生成 32 维分量，逐项累加后 L2
    归一化。公理集不同 → 向量不同（降级后 g 重算的区分度来源）。
    """
    ids = sorted({str(x) for x in (axiom_ids or [])})
    vec = [0.0] * _EMBED_DIM
    for aid in ids:
        state = zlib.crc32(aid.encode("utf-8")) & 0xFFFFFFFF
        for i in range(_EMBED_DIM):
            state = (_LCG_A * state + _LCG_C) % _LCG_MOD
            vec[i] += (state / float(_LCG_MOD - 1)) * 2.0 - 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        state = 2077
        out = []
        for _ in range(_EMBED_DIM):
            state = (_LCG_A * state + _LCG_C) % _LCG_MOD
            out.append((state / float(_LCG_MOD - 1)) * 2.0 - 1.0)
        norm = math.sqrt(sum(v * v for v in out))
        vec = out
    return [v / norm for v in vec]


def default_state_fn(round_index, ctx):
    """默认隐状态函数：返回当前目标向量（对齐分恒 1.0 的退化解）。

    【审计 A3 警示】本函数是锁定决策 #1 的离线确定性注入口径：
    因 s_t ≡ g，对齐分结构性恒 1.0，收敛曲线无判别力。正式/实机
    批次必须注入 make_model_state_fn(adapter) 以 7B 隐状态替换，
    否则论文级结论不得引用该路对齐分。判据②（功能死亡）在离线路
    同样失去意义（对齐分永不 < τ）。
    """
    return list(ctx.get("target_g") or [])


def make_model_state_fn(adapter, text_builder=None):
    """构造 7B 隐状态函数（审计 A3 + 安全检查 F3）：s_t = extract_hidden(观测文本)。

    Args:
        adapter: ModelAdapter（须已 load，提供 extract_hidden(text)）。
        text_builder: callable(ctx) -> str，把轮上下文编译为观测文本；
            默认拼接轮号与公理集描述。

    Returns:
        state_fn(round_index, ctx) -> list[float] | None：实机路隐状态。
        F3 契约（安全检查）：模型不可用（未加载 / CUDA OOM / 任意异常）
        时返回 None，由 runner 将该轮对齐分置 None（不追加 QS 历史、
        detail.model_state_unavailable=True）——**绝不回退 target_g**，
        避免注入 cos(g,g)=1.0 伪高分污染收敛曲线（假状态兜底）。
    """

    def _default_text(ctx):
        axiom_ids = sorted(ctx.get("axiom_ids") or [])
        return (f"round {ctx.get('round_id')}; active axioms: "
                f"{','.join(axiom_ids) or 'none'}")

    tb = text_builder or _default_text

    def model_state_fn(round_index, ctx):
        try:
            return adapter.extract_hidden(tb(ctx))
        except ContextOverflowError:
            # 前置条件②破坏：上下文溢出 → 系统不可信，穿透给 runner 处理
            raise
        except Exception as e:
            log.warning("model state_fn unavailable at round %s: %s "
                        "(alignment None, no target_g fallback)",
                        ctx.get("round_id"), e)
            return None

    return model_state_fn


def make_model_embedding_fn(adapter, text_builder=None):
    """构造 7B 目标嵌入函数（审计 A3 + 安全检查 F3，与 state_fn 配套）。

    make_model_state_fn 使 s_t 为 3584 维 7B 隐状态；若 g 仍走离线
    32 维确定性嵌入，compute_alignment_score 会因维度不匹配 fail-loud
    （实测：hidden dim 3584 != target_g dim 32）。本函数把目标向量 g
    也改为 7B 自身嵌入——g 与 s_t 同空间，完整落地锁定决策 #1。

    F3 契约：模型不可用时返回 None（g 注入失败 → 轮末对齐分 None），
    不回退 deterministic_embedding（避免 32 维 g 与 3584 维 s_t 维度
    不匹配中断实验 / 伪对齐）。

    Args:
        adapter: ModelAdapter（须已 load，提供 compute_target_g(text)）。
        text_builder: callable(axiom_ids) -> str，把活跃公理集编译为
            目标文本；默认拼接公理 id。

    Returns:
        embedding_fn(axiom_ids) -> list[float] | None：7B 目标嵌入；
        模型不可用时返回 None。
    """

    def _default_text(axiom_ids):
        return "目标：维持 W2 稳态。活跃公理集：" + (
            ",".join(sorted(axiom_ids)) if axiom_ids else "空")

    tb = text_builder or _default_text

    def model_embedding_fn(axiom_ids):
        try:
            return adapter.compute_target_g(tb(axiom_ids))
        except ContextOverflowError:
            # 前置条件②破坏：上下文溢出 → 系统不可信，穿透给 runner
            # 处理（与 state_fn 路径一致，决策 #12——S29.15 修复：
            # 此前被裸 except 吞成「模型不可用」）
            raise
        except Exception as e:
            log.warning("model embedding_fn unavailable: %s "
                        "(g injection None, no deterministic fallback)", e)
            return None

    return model_embedding_fn


def read_disintegration_config(config_path=None):
    """读取 Γ脆判据配置段（锁定决策 #12 的 K 与 H_L 的 N）。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    cfg = json.loads(path.read_text(encoding="utf-8"))
    d = cfg.get("disintegration", {})
    return {
        "functional_death_k": int(d.get("functional_death_k", 3)),
        "h_l_window_n": int(d.get("h_l_window_n", 5)),
    }


def read_fault_tolerance_config(config_path=None):
    """读取 S17 容错配置段（采集重试 / 重连等待）。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    cfg = json.loads(path.read_text(encoding="utf-8"))
    f = cfg.get("fault_tolerance", {})
    return {
        "collect_retry_max": int(f.get("collect_retry_max", 2)),
        "collect_retry_sleep_s": float(f.get("collect_retry_sleep_s", 1.0)),
        "reconnect_wait_s": float(f.get("reconnect_wait_s", 30.0)),
    }


class RoundUnavailable(Exception):
    """游戏侧持续不可用（重试与重连等待均失败），该轮须优雅跳过。

    区别于程序错误：本异常不代表实验中断，run() 捕获后记入
    skipped_rounds 并继续后续轮次。
    """


# ── 闭环运行器 ────────────────────────────────────────────

class ClosedLoopRunner:
    """四层数据流闭环运行器。

    裁决权全部下沉到既有硬编码模块（rule_validator / axiom_engine /
    downgrade_manager / qs_kernel / log_recorder），本类只做数据流编排。
    """

    def __init__(self, connector, sampler, executor, recorder,
                 axiom_engine=None, downgrade_manager=None, qs_kernel=None,
                 rule_validator=None, icc_auditor=None, embedding_fn=None,
                 state_fn=None, llm_assistant=None, activation_engine=None,
                  action_planner=None, rounds=20, functional_death_k=3,
                  h_l_window_n=5, collect_retry_max=2,
                  collect_retry_sleep_s=1.0, reconnect_wait_s=30.0):
        self.connector = connector
        self.sampler = sampler
        self.executor = executor
        self.recorder = recorder
        self.axiom_engine = axiom_engine or AxiomEngine()
        self.qs_kernel = qs_kernel or QSKernel()
        self.downgrade_manager = downgrade_manager or DowngradeManager(
            self.axiom_engine, qs_kernel=self.qs_kernel)
        self.rule_validator = rule_validator or RuleValidator()
        # 审计 A5：ICC 审计器接入正式闭环（默认硬编码实例，可注入替换）。
        # 修复 1（2026-08-25）：从候选池收集锚点注入 ICC 白名单，避免
        # 候选规则（P_GANG_HOSTILE/P_ITEM/P_STATUS 等）被误判 invalid_anchor。
        _extra_anchors = set()
        for _c in self.rule_validator.get_candidates():
            _a = _c.get("anchor")
            if _a:
                _extra_anchors.add(_a)
        self.icc_auditor = icc_auditor or ICCAuditor(
            extra_valid_anchors=_extra_anchors)
        # 审计 A1：LLM 辅助可选接入（默认 None = 零模型离线主链）。
        # 反套壳边界：LLM 输出仅归档为 advisory_notes（人类可读），
        # 绝不回流 rule_validator/axiom/downgrade/qs/Γ脆 任何判定路径。
        # 本模块顶层不 import llm_assistant——由调用方显式构造注入。
        self.llm_assistant = llm_assistant
        # S28 候选公理自主激活（弱自进化）：None = 不启用（离线主链
        # 无候选）；from_config 默认装配 ActivationEngine。激活只做
        # "值得验证"的选择，真值判定仍归 rule_validator。
        self.activation_engine = activation_engine
        # S29 动作规划器（LLM 自主行动渐进部署）：None = 默认编排
        # （奇偶交替）；注入后 shadow 模式只记录建议、active 模式
        # 校验通过才执行。planner 只决定动作选择（行动权），真值
        # 判定仍归 rule_validator（反套壳对齐）。
        self.action_planner = action_planner
        self.embedding_fn = embedding_fn or deterministic_embedding
        self.state_fn = state_fn or default_state_fn
        self.rounds = int(rounds)
        self.functional_death_k = int(functional_death_k)
        self.h_l_window_n = int(h_l_window_n)
        self.collect_retry_max = int(collect_retry_max)
        self.collect_retry_sleep_s = float(collect_retry_sleep_s)
        self.reconnect_wait_s = float(reconnect_wait_s)
        self._label_history = []
        self._seen_label_combos = set()
        # S29.9 L2：实体跨轮历史快照（供候选规则时序断言验证）
        # entity_id -> {"first_seen": round, "last_seen": round}
        self._entity_history = {}
        self._prev_scan_ids = set()
        self._prev_entities = {}  # entity_id -> 上轮实体 dict（消失实体验证用）
        # S29.17 F19：默认编排移动按「已执行移动次数」交替 ±10——
        # 此前按 round_id 奇偶，奇数轮恒攻击时移动只发生在偶数轮、
        # dx 恒 +10 → 单向漂移走出实验区。
        self._default_move_count = 0

    # ── 装配 ──────────────────────────────────────────────

    def inject_axioms_from_w1(self):
        """从 W1 规则库注入全部实验公理（已存在的跳过，幂等）。"""
        w1_defs = self.rule_validator.get_axioms()
        injected = []
        for a in w1_defs:
            aid = a["axiom_id"]
            if self.axiom_engine.get_axiom(aid) is not None:
                continue
            self.axiom_engine.add_axiom(aid, a["hypothesis"], a["anchor"])
            injected.append(aid)
        log.info("injected %d axioms from W1: %s", len(injected), injected)
        return injected

    @classmethod
    def from_config(cls, connector, sampler, executor, recorder,
                    config_path=None, rounds=20, state_fn=None):
        path = str(config_path) if config_path else str(DEFAULT_CONFIG_PATH)
        qs = QSKernel.from_config(path)
        engine = AxiomEngine()
        dm = DowngradeManager(engine, qs_kernel=qs)
        dis = read_disintegration_config(path)
        ft = read_fault_tolerance_config(path)
        runner = cls(connector, sampler, executor, recorder,
                     axiom_engine=engine, downgrade_manager=dm, qs_kernel=qs,
                     rule_validator=RuleValidator(),
                     embedding_fn=deterministic_embedding,
                     state_fn=state_fn or default_state_fn,
                     rounds=rounds,
                     functional_death_k=dis["functional_death_k"],
                     h_l_window_n=dis["h_l_window_n"],
                     collect_retry_max=ft["collect_retry_max"],
                     collect_retry_sleep_s=ft["collect_retry_sleep_s"],
                     reconnect_wait_s=ft["reconnect_wait_s"])
        runner.inject_axioms_from_w1()
        runner.activation_engine = ActivationEngine.from_config(
            runner.rule_validator, runner.axiom_engine, path)
        with open(path, "r", encoding="utf-8") as _f:
            _cfg = json.load(_f)
        pl = _cfg.get("planner", {}) or {}
        runner.action_planner = (ActionPlanner.from_config(path)
                                 if pl.get("enabled", False) else None)
        return runner

    # ── 主循环 ────────────────────────────────────────────

    def run(self, resume=False):
        """跑 N 轮闭环，返回实验报告（含 Γ脆判定与虚拟寿命）。

        resume=True：以 JSONL 已归档轮次为检查点，跳过已归档轮，
        从检查点后续轮恢复（游戏崩溃重启场景，S17）。
        """
        archived_set = set()
        resumed_skipped = []
        if resume:
            archived_set = set(self.recorder.list_rounds())

        round_details = []
        c_extend_series = []
        skipped_rounds = []
        criterion = None
        interruption = False
        interruption_reason = None
        completed = 0

        for round_id in range(self.rounds):
            if resume and round_id in archived_set:
                resumed_skipped.append(round_id)
                continue
            try:
                out = self._run_round(round_id)
            except RoundUnavailable as e:
                skipped_rounds.append({"round_id": round_id, "reason": str(e)})
                log.warning("round %d skipped (game unavailable): %s",
                            round_id, e)
                continue
            except Exception as e:
                interruption = True
                interruption_reason = f"{type(e).__name__}: {e}"
                log.error("round %d interrupted: %s", round_id, interruption_reason)
                break
            round_details.append(out["detail"])
            c_extend_series.append(out["c_extend"])
            completed += 1
            if out["criterion"] is not None:
                criterion = out["criterion"]
                log.warning("disintegration at round %d: criterion=%s",
                            round_id, criterion)
                break

        qs = self.qs_kernel
        # 审计 A5：批次末 ICC 审计（公理一致性/完整性/锚点/状态）
        # 注意：初始公理为全称公理（无边界条件），missing_boundary 为
        # 预期状态（S8 设计：边界从反例自动补全），审计如实呈现不阻断。
        icc_report = self.icc_auditor.audit(self.axiom_engine.get_all_axioms())
        # 安全检查 G3/F1：崩溃/跳轮标记（不改 Γ脆 三判据本身）。
        # crashed_at_round = 首次跳过轮；system_dead = 连续跳过 >= 3 轮
        # （判据缝隙缓解：系统"已死但不判解体"以标记留痕，可纳入寿命分析）。
        crashed_at_round = (skipped_rounds[0]["round_id"]
                            if skipped_rounds else None)
        max_consecutive_skip = 0
        cur = 0
        prev = None
        for s in skipped_rounds:
            rid = s["round_id"]
            cur = cur + 1 if prev is not None and rid == prev + 1 else 1
            max_consecutive_skip = max(max_consecutive_skip, cur)
            prev = rid
        report = {
            "rounds_target": self.rounds,
            "completed": completed,
            "disintegrated": criterion is not None,
            "criterion": criterion,
            "virtual_lifespan": completed,
            "interruption": interruption,
            "interruption_reason": interruption_reason,
            "skipped_rounds": skipped_rounds,
            "crashed_at_round": crashed_at_round,
            "system_dead": max_consecutive_skip >= 3,
            "max_consecutive_skipped": max_consecutive_skip,
            "resume": {"enabled": resume, "skipped_archived": resumed_skipped},
            "axiom_final_status": {
                a["id"]: a["status"] for a in self.axiom_engine.get_all_axioms()
            },
            "c_extend_series": c_extend_series,
            "qs_history": list(qs.get_history()),
            "converged_final": qs.check_monotonicity()["converged"],
            "h_l": self._evaluate_h_l(criterion, c_extend_series),
            "icc_audit": icc_report,
            "activation": (self.activation_engine.get_status()
                           if self.activation_engine is not None else None),
            "round_details": round_details,
            "downgrade_events": self.downgrade_manager.get_downgrade_events(),
        }
        return report

    # ── S17 采集容错 ──────────────────────────────────────

    def _collect_with_retry(self, round_id):
        """W0 采集容错（S17）：重试 → 断连等待重连 → 持续不可用抛
        RoundUnavailable（该轮优雅跳过，不中断实验）。

        每次失败后先判连接状态：已断连 → 等待重连（成功则立即重试，
        标 recovered_via_reconnect）；重连窗口内恢复的连接不会被误杀。
        返回 (w0_data, attempts, recovered_via_reconnect)。
        剧情锁/空实体不在此路径——二者为轮内非致命降级，照常归档。
        """
        attempts = 0
        last_err = None
        recovered = False
        while True:
            attempts += 1
            try:
                return self.sampler.collect_round(round_id), attempts, recovered
            except GameConnectorError as e:
                last_err = e
                log.warning("collect attempt %d failed: %s", attempts, e)
            if not self.connector.is_game_connected:
                log.warning("game disconnected, waiting %.1fs for reconnect",
                            self.reconnect_wait_s)
                if not self.connector.wait_for_game(timeout=self.reconnect_wait_s):
                    break
                recovered = True
                continue
            if attempts > self.collect_retry_max:
                break
            time.sleep(self.collect_retry_sleep_s)
        raise RoundUnavailable(
            f"collection unavailable after {attempts} attempts: {last_err}")

    # ── 单轮流程 ──────────────────────────────────────────

    def _run_round(self, round_id):
        engine, qs, dm = self.axiom_engine, self.qs_kernel, self.downgrade_manager

        # 1. 轮内快照冻结（锁定决策 #13）
        dm.begin_round(round_id)

        # 2. g 注入（首轮或降级后缺失时）
        g_injected = False
        g_vector = None
        if qs.get_target_g() is None:
            ids = sorted(a["id"] for a in engine.get_active_axioms(effective=True))
            if ids:
                g_vector = qs.compute_target_g(self.embedding_fn(ids))
                g_injected = g_vector is not None

        # 3. W0 采集（S17 容错：重试→重连等待→不可用则优雅跳过该轮）
        try:
            w0_data, collect_attempts, recovered = self._collect_with_retry(round_id)
        except RoundUnavailable:
            dm.end_round()
            raise
        entities = w0_data.get("entities", [])
        empty_scan = len(entities) == 0

        # S29.9 L2：实体跨轮历史快照 + 本轮消失实体（供时序断言验证）
        cur_by_id = {}
        for ent in entities:
            eid = ent.get("entity_id")
            if eid:
                cur_by_id[eid] = ent
        recent_gone = [self._prev_entities[eid]
                       for eid in (set(self._prev_entities)
                                   - set(cur_by_id))
                       if eid in self._prev_entities]
        for eid, ent in cur_by_id.items():
            rec = self._entity_history.setdefault(
                eid, {"first_seen": round_id, "last_seen": round_id})
            rec["last_seen"] = round_id
        self._prev_scan_ids = set(cur_by_id)
        self._prev_entities = cur_by_id

        # S28：候选激活引擎消费本轮观测（事件计数窗口累计）
        if self.activation_engine is not None:
            self.activation_engine.feed(w0_data, round_id)

        # 4. 本轮全部实体标签并入 label_history（降级保护多样性闸门前置）
        for ent in entities:
            labels = ent.get("labels") or []
            if not labels:
                continue
            combo = tuple(sorted(str(x) for x in labels))
            if combo not in self._seen_label_combos:
                self._seen_label_combos.add(combo)
                self._label_history.append(list(labels))

        # 5. W3 动作（白名单；S29：ActionPlanner 可选——shadow 只记录
        #    建议不执行、active 校验通过才执行，被拒回退默认编排）
        proposed_action = None
        proposed_validated = None
        action_source = "default"
        plan = None
        if self.action_planner is not None:
            player_pos_now = player_pos_from(entities) or (0.0, 0.0, 0.0)
            plan = self.action_planner.plan(
                {"entities": entities,
                 "player_position": {"x": player_pos_now[0],
                                     "y": player_pos_now[1],
                                     "z": player_pos_now[2]}},
                round_id)
            ok, why = self.action_planner.validate(
                plan, {"entities": entities},
                player_position={"x": player_pos_now[0],
                                 "y": player_pos_now[1],
                                 "z": player_pos_now[2]})
            proposed_action = plan
            proposed_validated = {"ok": ok, "reason": why}
            if self.action_planner.mode == "active" and ok:
                action_source = "planner"
        action_label, action_result = self._execute_action(
            round_id, entities,
            plan if action_source == "planner" else None)
        action_result = action_result or {}

        # 6. 事件回流：暂存事件 + 带 kind 的动作证据并入 W0 事件流
        merged = list(self.executor.pop_stashed_events())
        for ev in (action_result.get("evidence") or []):
            if isinstance(ev, dict) and "kind" in ev:
                merged.append(ev)
        for ev in merged:
            ev.setdefault("round_id", round_id)
            ev.setdefault("collection_ts", w0_data.get("collection_ts"))
        w0_data["events"].extend(merged)

        # 7-9. 假设生成 → 反例处理 → 降级后 g 重注入
        hypotheses = []
        proc_list = []
        g_reinjected = False
        g_vector_after = None
        player_pos = player_pos_from(entities)
        active_axioms = engine.get_active_axioms(effective=True)

        # S29.9 L2：假设生成遍历 = 本轮扫描实体 + 本轮消失实体
        # （消失实体带 disappeared 标记，供时序断言"出现后 N 轮内
        # 未消失/过早消失"验证）
        scan_ents = [(ent, False) for ent in entities] + \
                    [(ent, True) for ent in recent_gone]
        for axiom in active_axioms:
            aid = axiom["id"]
            for ent, gone in scan_ents:
                obs = self._build_observation(ent, w0_data, player_pos,
                                              disappeared=gone)
                hyp = engine.generate_hypothesis(
                    aid, obs, rule_validator=self.rule_validator)
                rv = hyp.get("rule_validation") or {}
                hyp_arch = dict(hyp)
                hyp_arch["violated_rules"] = [
                    v.get("rule_id") for v in rv.get("violated_rules", [])
                    if isinstance(v, dict)
                ]
                hypotheses.append(hyp_arch)
                if not hyp.get("is_counterexample"):
                    continue
                proc = dm.process_counterexample(
                    aid, hyp, observation=obs, qs_kernel=qs)
                proc_arch = dict(proc)
                proc_arch["axiom_id"] = aid
                proc_arch["entity_id"] = ent.get("entity_id")
                proc_list.append(proc_arch)
                if proc.get("action") == "downgrade" and proc.get("g_recomputed"):
                    remaining = sorted(
                        a["id"] for a in engine.get_active_axioms(effective=True))
                    if remaining:
                        g_vector_after = qs.compute_target_g(
                            self.embedding_fn(remaining))
                        g_reinjected = g_vector_after is not None

        # 10. 轮末对齐分（必须在重注入之后）
        alignment_score = None
        model_state_unavailable = False
        system_untrusted = False
        g_now = qs.get_target_g()
        if g_now:
            # ctx 含活跃公理集（审计 A3：make_model_state_fn 观测文本编译）
            # 安全检查 F3 双层防御：即使注入的 state_fn 直接抛错
            # （如 CUDA OOM 未包装），也不中断轮——该轮对齐分置 None。
            s_t = None
            try:
                s_t = self.state_fn(round_id, {
                    "round_id": round_id,
                    "target_g": g_now,
                    "axiom_ids": [a["id"] for a in
                                  engine.get_active_axioms(effective=True)],
                })
            except ContextOverflowError:
                # 前置条件②破坏：上下文溢出 → 系统不可信（非判据②），
                # 该轮对齐分不可采信，不追加 QS 历史。
                system_untrusted = True
            except Exception as e:
                log.warning("state_fn raised at round %s: %s "
                            "(alignment None)", round_id, e)
            # F3：模型路失败 → 该轮无对齐分（不追加 QS 历史、
            # 不注入伪高分）；默认离线路恒返回 target_g。
            if s_t:
                # S29.17 F13：计分步同样纳入 F3 保护——劣化隐状态
                # （零向量/维度错配/非有限）在 qs 内抛 ValueError，
                # 穿透会中断整批实验，契约要求不中断轮。
                try:
                    alignment_score = qs.compute_alignment_score(s_t)
                except ValueError as e:
                    log.warning("alignment scoring failed at round %s: %s "
                                "(alignment None)", round_id, e)
                    s_t = None
            model_state_unavailable = (s_t is None
                                       and self.state_fn is not default_state_fn
                                       and not system_untrusted)

        # 11. 轮间生效
        dm.end_round()

        # S28：轮间激活检查（候选达标 → 注入活跃公理，下轮生效）
        if self.activation_engine is not None:
            self.activation_engine.end_round()

        # 12. 四层归档 + C_H
        w1_data = {
            "checked_axioms": [a["id"] for a in active_axioms],
            "observations_checked": len(active_axioms) * len(entities),
            "valid_counterexamples": len(proc_list),
            "label_history_size": len(self._label_history),
        }
        w2_data = engine.export_w2_data()
        # 审计 A1：LLM 辅助接入（可选）——轮末生成人类可读 advisory 文本。
        # 仅在 llm_assistant 显式注入时启用；输出仅归档，零决策回流。
        advisory_notes = self._build_advisory_notes(hypotheses, proc_list,
                                                    round_id)
        w3_data = {
            "action": action_label,
            "action_status": action_result.get("status"),
            "action_channel": action_result.get("channel"),
            "action_control_tier": action_result.get("control_tier"),
            "action_receipt_reason": action_result.get("receipt_reason"),
            "action_outcome_semantics": action_result.get("outcome_semantics"),
            "action_confirmation_status": action_result.get(
                "confirmation_status"),
            "hypotheses": hypotheses,
            "counterexample_processing": proc_list,
            "downgrade_events": dm.get_downgrade_events(),
            "warn_events": dm.get_warn_events(),
            "alignment_score": alignment_score,
            "g": qs.get_target_g(),
            "advisory_notes": advisory_notes,
        }
        entry = self.recorder.record_round(
            round_id, w0_data, w1_data, w2_data, w3_data)

        # 13. Γ脆三判据（锁定决策 #12，外层 OR，顺序 ①→②→③）
        criterion = self._check_disintegration(entry)

        detail = {
            "round_id": round_id,
            "action": action_label,
            "action_status": action_result.get("status"),
            "action_channel": action_result.get("channel"),
            "action_control_tier": action_result.get("control_tier"),
            "action_reject_reason": action_result.get("reject_reason"),
            "action_receipt_reason": action_result.get("receipt_reason"),
            "action_outcome_semantics": action_result.get("outcome_semantics"),
            "action_confirmation_status": action_result.get(
                "confirmation_status"),
            "action_evidence_count": len(action_result.get("evidence") or []),
            "action_source": action_source,
            "proposed_action": proposed_action,
            "proposed_validated": proposed_validated,
            "empty_scan": empty_scan,
            "collect_attempts": collect_attempts,
            "recovered_via_reconnect": recovered,
            "g_injected": g_injected,
            "g_vector": g_vector,
            "g_reinjected": g_reinjected,
            "g_vector_after": g_vector_after,
            "alignment_score": alignment_score,
            "model_state_unavailable": model_state_unavailable,
            "system_untrusted": system_untrusted,
            "counterexample_processing": proc_list,
        }
        return {
            "detail": detail,
            "c_extend": entry.get("c_h", {}).get("extend", 0),
            "criterion": criterion,
        }

    # ── 动作 ──────────────────────────────────────────────

    def _execute_action(self, round_id, entities, plan=None):
        """W3 动作执行。plan=None → 默认奇偶交替（攻击首个 NPC / 相对
        当前位置 ±10m 交替）。plan 来自 ActionPlanner（active 模式已通过
        白名单/位置校验）：attack 用建议目标、move 用建议偏移 dx。

        实机移动（S26 用户需求 + S5 教训）：move 目标必须是**相对当前
        位置的小步偏移**（绝对坐标盲传会越界/卡墙/坠落，S5 实测教训）。
        每轮 move 在当前位置 ±10m 交替摆动（0→+10→-10→+10…），幅度
        显著便于视觉确认真实移动；绝对避免 dx=0（必须真移动）。
        """
        # S29：planner 建议动作（已通过白名单/位置校验）
        if plan is not None and plan.get("action_type") in ("attack", "pickup"):
            target = plan.get("target_entity_id")
            res = self.executor.execute(plan["action_type"],
                                        {"target_id": target})
            return f"{plan['action_type']}:{target}", res
        if plan is not None and plan.get("action_type") == "kill":
            # S30：kill 走 native 通道（半径制，无目标/无位置约束），
            # 参数本地校验已在 planner.validate + executor 兜底。
            params = plan.get("params") or {}
            res = self.executor.execute("kill", dict(params))
            return f"kill:r={params.get('radius', 'default')}", res
        if plan is not None and plan.get("action_type") == "move":
            params = plan.get("params") or {}
            pos = player_pos_from(entities)
            if pos is None:
                return "move_no_position", None
            # 地形感知移动：实机坐标系 x=东 y=北 z=高度——
            # dx→x、dz→y（水平面），z 抬升 HOP 后交给游戏物理沉降到
            # 当地地面（落点高度实测读回，不标定；S29.7/S29.8 的
            # z=北 mock 约定在实机是高度轴，旧映射会把角色压入地下）。
            dx = float(params.get("dx", 0.0))
            dz = float(params.get("dz", 0.0))
            goal = {"x": pos[0] + dx, "y": pos[1] + dz,
                    "z": pos[2] + MOVE_HOP_RISE_M}
            res = self.executor.execute("move", goal)
            return (f"move:({goal['x']:.1f},{goal['y']:.1f},"
                    f"{goal['z']:.1f})", res)
        # 默认编排（奇偶交替，S26 语义不变）
        npcs = [e for e in entities if e.get("kind") == "npc"]
        if round_id % 2 == 1 and npcs:
            target = npcs[0].get("entity_id")
            res = self.executor.execute("attack", {"target_id": target})
            return f"attack:{target}", res
        pos = player_pos_from(entities) or (0.0, 0.0, 0.0)
        # 相对偏移：+10 / -10 交替（每轮 move 必真移动，幅度显著）
        # S29.17 F19：交替键为移动计数而非 round_id（防单向漂移）
        dx = 10.0 if self._default_move_count % 2 == 0 else -10.0
        self._default_move_count += 1
        goal = {"x": pos[0] + dx, "y": pos[1], "z": pos[2] + MOVE_HOP_RISE_M}
        res = self.executor.execute("move", goal)
        return f"move:({goal['x']:.1f},{goal['y']:.1f},{goal['z']:.1f})", res

    # ── 观测构造 ──────────────────────────────────────────

    def _build_observation(self, entity, w0_data, player_pos,
                           disappeared=False):
        dist = None
        pos = entity.get("position")
        if player_pos is not None and isinstance(pos, dict):
            x, y, z = pos.get("x"), pos.get("y"), pos.get("z")
            if all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in (x, y, z)):
                dist = math.sqrt((x - player_pos[0]) ** 2
                                 + (y - player_pos[1]) ** 2
                                 + (z - player_pos[2]) ** 2)
        # S29.9 L2：跨轮时序上下文（候选规则 verification 判定用）
        eid = entity.get("entity_id")
        rec = self._entity_history.get(eid) if eid else None
        first_seen = rec.get("first_seen") if rec else None
        last_seen = rec.get("last_seen") if rec else None
        round_id = w0_data.get("round_id")
        elapsed = None
        if first_seen is not None and round_id is not None:
            elapsed = int(round_id) - int(first_seen)

        # R_M1_2（攻击后 HP 应下降）判定数据：从事件流提取以当前实体
        # 为 target 的 hp_changed 事件的 hp_before/hp_after。修复
        # 2026-08-25：此前 context 从不携带 hp 上下文，导致 A_M1 的真反例
        # 分支（攻击降 HP）在闭环里不可达，只能靠 R_M1_1 旁观 mismatch
        # 充数——现补齐，让「攻击降 HP」假设被其真正反例判定。
        hp_before = None
        hp_after = None
        for ev in (w0_data.get("events") or []):
            if ev.get("kind") != "hp_changed":
                continue
            if ev.get("target_entity") != eid:
                continue
            hp_before = ev.get("hp_before")
            hp_after = ev.get("hp_after")
            break

        return {
            "entity": entity,
            "events": w0_data.get("events", []),
            "context": {
                "distance_to_player": dist,
                # S29.17 P2-5：消失实体不在本轮扫描中——此前恒 True，
                # 旧位置超距的消失实体会被 R_W1_1 误判为"超距却可见"。
                "entity_in_scan": not disappeared,
                "entity_in_later_scan": not disappeared,
                "first_seen_round": first_seen,
                "last_seen_round": last_seen,
                "rounds_since_first_seen": elapsed,
                "disappeared": disappeared,
                "round_id": round_id,
                "hp_before": hp_before,
                "hp_after": hp_after,
            },
            "label_history": self._label_history,
        }

    # ── Γ脆判据（锁定决策 #12）────────────────────────────

    def _check_disintegration(self, entry):
        """轮末三判据检查，顺序 ①语义死亡 → ②功能死亡 → ③超预算。

        判据边界（审计 A8）：本函数只判定 W₂ 稳态的三类解体。游戏进程
        崩溃 / 连接中断属实验基础设施事件，不归 Γ脆——由 S17 容错
        （RoundUnavailable → skipped_rounds + 检查点 resume）处理，
        二者在日志中可通过 criterion 字段与 skipped_rounds 区分。
        """
        axioms = self.axiom_engine.get_all_axioms()
        if axioms and all(a["status"] == "deprecated" for a in axioms):
            return "semantic_death"

        hist = self.qs_kernel.get_history()
        k = self.functional_death_k
        tau = self.qs_kernel.convergence_threshold
        if len(hist) >= k and all(s < tau for s in hist[-k:]):
            return "functional_death"

        if entry.get("c_h", {}).get("within_budget") is False:
            return "over_budget"
        return None

    def _build_advisory_notes(self, hypotheses, proc_list, round_id):
        """LLM 辅助（可选）：轮末假设自然语言化 + 降级语义总结。

        仅当 self.llm_assistant 注入时启用；输出为 dict 列表（含
        disclaimer/meta），仅归档于 w3.advisory_notes，不参与任何判定。
        任何 LLM 异常被捕获并记录 error 条目，绝不中断实验轮。
        """
        if self.llm_assistant is None:
            return []
        notes = []
        for h in hypotheses[:3]:
            aid = h.get("axiom_id", "")
            anchor = (self.axiom_engine.get_axiom(aid) or {}).get("anchor", "")
            try:
                out = self.llm_assistant.hypothesis_nl({
                    "axiom_id": aid,
                    "anchor": anchor,
                    "predicate": h.get("description", ""),
                })
                notes.append({"source": "hypothesis_nl",
                              "hypothesis_id": h.get("hypothesis_id"),
                              "output": out})
            except Exception as e:
                notes.append({"source": "hypothesis_nl",
                              "hypothesis_id": h.get("hypothesis_id"),
                              "error": f"{type(e).__name__}: {e}"})
        for p in proc_list:
            if p.get("action") not in ("warn", "downgrade"):
                continue
            to_status = ("deprecated" if p["action"] == "downgrade"
                         else "warned")
            from_status = ("warned" if p["action"] == "downgrade"
                           else "active")
            try:
                out = self.llm_assistant.summarize_downgrade({
                    "axiom_id": p.get("axiom_id", ""),
                    "from_status": from_status,
                    "to_status": to_status,
                    "reason": p.get("reason", ""),
                    "counterexamples_used": [],
                    "boundary_added": p.get("boundary_conditions_added") or [],
                    "round_id": round_id,
                })
                notes.append({"source": "summarize_downgrade",
                              "axiom_id": p.get("axiom_id"),
                              "output": out})
            except Exception as e:
                notes.append({"source": "summarize_downgrade",
                              "axiom_id": p.get("axiom_id"),
                              "error": f"{type(e).__name__}: {e}"})
        return notes

    def _evaluate_h_l(self, criterion, c_extend_series):
        """H_L 递进动力学链检验（逻辑学审查增设，2026-08-25）。

        仅当 ①/② 触发时评估：触发前的 N 轮窗口内 C_extend 从未达到
        B_extend（即判据③未先于①②触发）→ 递进链「③→②→①」被证伪，
        violated=True。
        """
        budget = self.recorder.extend_budget
        if criterion not in ("semantic_death", "functional_death"):
            return {"evaluated": False, "violated": None,
                    "window": [], "extend_budget": budget}
        window = c_extend_series[:-1][-self.h_l_window_n:]
        violated = all(v <= budget for v in window) if window else True
        return {"evaluated": True, "violated": violated,
                "window": list(window), "extend_budget": budget}
