"""S18 对照组基线——纯统计拟合版本（与实验组对比口径对齐）。

对照口径契约（S18 锁定）：
- 同一批观测数据：回放实验组 JSONL 归档的 w0 层（run_from_archive），
  对照组不连接游戏、不执行动作，是被动归纳者。
- 机制差异（被检验项）：无公理、无降级、无标签多样性保护闸门、
  无边界补全——仅按标签组合 (kind, labels) 做异常经验频率拟合。
- 实际异常真值：rule_validator.is_valid_counterexample 的 violated_rules
  非空（任一公理）。不施加标签多样性闸门——闸门是实验组机制，
  「是否异常」是客观事实，与机制无关。
- 口径映射：per-round accuracy acc_t ∈ [0,1] → alignment-equivalent
  score_t = 2*acc_t - 1 ∈ [-1,1]，与实验组对齐分同域（余弦内积域）。
- 测量仪器复用：QSKernel.check_monotonicity(scores=...)，同
  window/ε/τ 配置；对照组不伪造向量，不走 compute_alignment_score。
- 解体判据：①语义死亡 / ③超预算 not_applicable（无公理、无反例机制）；
  ②功能死亡同口径——连续 K 轮 score < τ，K/τ 取同一配置。
  虚拟寿命 = completed（与实验组同定义）。
- 口径差异诚实落档（S29.17 P2 群）：
  · 对照组公理集在构造时固化——实验组中途激活候选公理时，对照组
    真值集不跟随（F2，已知局限；当前正式批次候选激活不影响三基础
    公理世界的对照）。
  · hp_changed 取事件流中第一条 target 命中（F3）：两侧同构（公平），
    但对事件到达顺序脆弱——同轮同目标多条 hp_changed 时，无"与攻击
    证据关联"的挑选规则（当前剧本每轮每目标恰 1 条）。
  · 预测阈值严格大于（p > threshold）且首观测用 prior（F5）：异常率
    恰 0.5 的交替世界组合频率钉死 0.5、永不预测异常，对照组准确率
    被结构性压低——已声明语义，解释对比结果时须知。
  · 空实体轮对照组不记分（score=None 不入历史），实验组照常计分
    （F7）——两侧功能死亡窗口构成在空轮时不同（当前剧本无空轮）。
  · 同一 τ 施加于两个分布不同的分数域（F8）：实验组余弦域 ~0.77，
    对照组 2*acc-1 离散谱——机制一致（同参同式），解释需谨慎。
  · 实验组降级触发 qs.reset() 会截断判据②窗口，对照组无降级、窗口
    全程累计（F6）——窗口连续性语义不对称，已知落档。
- 在线学习协议（轮级快照，S29.16 议题 2 方案 A 锁定）：同轮全部实体
  的预测使用**当轮更新前**的统计快照；统计更新在本轮全部预测与
  裁决完成后批量发生（标准预序学习的轮级粒度，防止当轮自证满分；
  与实验组「轮内快照冻结」决策 #13 的公平性口径对齐——实体级即时
  更新会让同组合后续实体消费本轮先学知识，给对照组额外学习速度
  优势）。
- 观测构造镜像实验组（closed_loop_runner 步骤 4/7 时序）：先并入当轮
  全部标签组合再评估；context = {distance_to_player,
  entity_in_scan=True, entity_in_later_scan=True, hp_before, hp_after}
  （hp 上下文供 R_M1_2 判定，与实验组同口径）。

反套壳铁则对齐：
- 铁则 #1：对照组裁决同样下沉——「实际异常」由 rule_validator 硬编码
  判定，「预测对错」为机械比较；induction_fn 默认纯频率统计，实机可
  替换为 7B 归纳路径（结构化输出），不改变任何裁决归属。
- 铁则 #3：本模块零模型依赖，离线可单测。
- 铁则 #4：每轮归纳结论结构化归档于 round_details，可 100% 复现。
"""

import json
import logging
import math
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from common.utils import player_pos_from
from w1.rule_validator import RuleValidator
from w2.qs_kernel import QSKernel

log = logging.getLogger("statistical_baseline")

DEFAULT_CONFIG_PATH = _SRC.parent / "config" / "experiment_config.json"


def read_baseline_config(config_path=None):
    """读取对照组配置段（S18）。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    cfg = json.loads(path.read_text(encoding="utf-8"))
    b = cfg.get("baseline", {})
    d = cfg.get("disintegration", {})
    return {
        "prior_anomaly_prob": float(b.get("prior_anomaly_prob", 0.0)),
        "anomaly_prob_threshold": float(b.get("anomaly_prob_threshold", 0.5)),
        "functional_death_k": int(d.get("functional_death_k", 3)),
    }


def default_induction_fn(combo_key, n_observed, n_anomalies, prior):
    """默认归纳函数：纯频率估计（离线路）。

    实机 7B 路替换口径：以结构化观测历史提示 7B，解析其归纳结论为
    同签名概率输出；裁决权仍归 rule_validator（对照组也不例外）。
    """
    if n_observed <= 0:
        return float(prior)
    return n_anomalies / n_observed


# ── 对照组运行器 ────────────────────────────────────────────


class StatisticalBaseline:
    """纯统计拟合对照组——被动消费观测批，输出归纳结论。"""

    def __init__(self, rule_validator=None, qs_kernel=None, rounds=20,
                 functional_death_k=3, prior_anomaly_prob=0.0,
                 anomaly_prob_threshold=0.5, induction_fn=None):
        self.rule_validator = rule_validator or RuleValidator()
        self.qs_kernel = qs_kernel or QSKernel()
        self.rounds = int(rounds)
        self.functional_death_k = int(functional_death_k)
        self.prior_anomaly_prob = float(prior_anomaly_prob)
        self.anomaly_prob_threshold = float(anomaly_prob_threshold)
        self.induction_fn = induction_fn or default_induction_fn
        self._axiom_ids = [a["axiom_id"]
                           for a in self.rule_validator.get_axioms()]
        self._stats = {}
        self._label_history = []
        self._seen_label_combos = set()
        self._score_history = []
        self._acc_history = []
        self._round_details = []

    # ── 装配 ──────────────────────────────────────────────

    @classmethod
    def from_config(cls, config_path=None, rounds=20):
        path = str(config_path) if config_path else str(DEFAULT_CONFIG_PATH)
        qs = QSKernel.from_config(path)
        bc = read_baseline_config(path)
        return cls(rule_validator=RuleValidator(), qs_kernel=qs,
                   rounds=rounds,
                   functional_death_k=bc["functional_death_k"],
                   prior_anomaly_prob=bc["prior_anomaly_prob"],
                   anomaly_prob_threshold=bc["anomaly_prob_threshold"],
                   induction_fn=default_induction_fn)

    # ── 主循环 ────────────────────────────────────────────

    def run(self, observation_batches):
        """消费观测批序列（与实验组同批观测），返回对照组报告。

        observation_batches: [{"round_id"?, "entities": [...],
                               "events": [...]}, ...]
        """
        criterion = None
        completed = 0
        for idx, batch in enumerate(list(observation_batches)[:self.rounds]):
            round_id = batch.get("round_id", idx)
            detail = self._process_round(round_id, batch)
            self._round_details.append(detail)
            completed += 1
            if detail["alignment_score"] is not None:
                self._score_history.append(detail["alignment_score"])
                self._acc_history.append(detail["accuracy"])
                if self._check_functional_death():
                    criterion = "functional_death"
                    log.warning("baseline functional_death at round %s",
                                round_id)
                    break
        return self._build_report(criterion, completed)

    def run_from_archive(self, recorder):
        """回放实验组 JSONL 归档（同一批观测数据）并运行对照组。"""
        batches = []
        for entry in recorder.load_all():
            w0 = entry.get("w0", {}) or {}
            batches.append({
                "round_id": entry.get("round_id"),
                "entities": w0.get("entities", []) or [],
                "events": w0.get("events", []) or [],
            })
        return self.run(batches)

    # ── 单轮流程 ──────────────────────────────────────────

    def _process_round(self, round_id, batch):
        entities = batch.get("entities", []) or []
        events = batch.get("events", []) or []
        player_pos = player_pos_from(entities)

        # 镜像实验组步骤 4：评估前先并入当轮全部标签组合
        for ent in entities:
            labels = ent.get("labels") or []
            if not labels:
                continue
            combo = tuple(sorted(str(x) for x in labels))
            if combo not in self._seen_label_combos:
                self._seen_label_combos.add(combo)
                self._label_history.append(list(labels))

        predictions = []
        adjudications = []
        correct = 0
        # S29.16 议题 2 方案 A（轮级快照）：同轮实体的预测全部基于
        # 轮前统计，裁决结果暂存，轮末批量更新——此前实体级即时更新
        # 使同组合后续实体消费本轮刚学到的统计，违背轮级契约。
        pending_updates = []
        for ent in entities:
            combo_key, combo_display = self._combo_of(ent)
            st = self._stats.get(combo_key, {"n": 0, "anomalies": 0})
            p_anom = self.induction_fn(
                combo_key, st["n"], st["anomalies"], self.prior_anomaly_prob)
            predicted = p_anom > self.anomaly_prob_threshold
            actual, violated = self._actual_anomaly(ent, events, player_pos)
            is_correct = bool(predicted) == bool(actual)
            correct += 1 if is_correct else 0
            predictions.append({
                "entity_id": ent.get("entity_id"),
                "combo": combo_display,
                "p_anomaly": p_anom,
                "predicted_anomaly": bool(predicted),
            })
            adjudications.append({
                "entity_id": ent.get("entity_id"),
                "actual_anomaly": bool(actual),
                "violated_rules": violated,
                "predicted": bool(predicted),
                "correct": is_correct,
            })
            pending_updates.append((combo_key, bool(actual)))

        # 轮末批量更新统计（预序学习的轮级粒度）
        for combo_key, actual in pending_updates:
            st2 = self._stats.setdefault(
                combo_key, {"n": 0, "anomalies": 0})
            st2["n"] += 1
            if actual:
                st2["anomalies"] += 1

        if entities:
            acc = correct / len(entities)
            score = 2.0 * acc - 1.0
        else:
            acc = None
            score = None

        return {
            "round_id": round_id,
            "entities_count": len(entities),
            "events_count": len(events),
            "predictions": predictions,
            "adjudications": adjudications,
            "accuracy": acc,
            "alignment_score": score,
            "induction_conclusions": self._conclusions(),
            "model_size": len(self._stats),
        }

    # ── 真值与观测构造（镜像实验组口径）────────────────────

    def _actual_anomaly(self, entity, events, player_pos):
        """实际异常判定：任一公理维度出现规则违反（W1 硬编码裁决）。

        不施加标签多样性闸门——该闸门是实验组的降级保护机制，
        异常事实本身与机制无关。
        """
        obs = self._build_observation(entity, events, player_pos)
        violated = []
        for aid in self._axiom_ids:
            res = self.rule_validator.is_valid_counterexample(aid, obs)
            for v in res.get("violated_rules", []) or []:
                violated.append({
                    "axiom_id": aid,
                    "rule_id": v.get("rule_id"),
                    "reason": v.get("reason"),
                })
        return (len(violated) > 0), violated

    def _build_observation(self, entity, events, player_pos):
        dist = None
        pos = entity.get("position")
        if player_pos is not None and isinstance(pos, dict):
            x, y, z = pos.get("x"), pos.get("y"), pos.get("z")
            if all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in (x, y, z)):
                dist = math.sqrt((x - player_pos[0]) ** 2
                                 + (y - player_pos[1]) ** 2
                                 + (z - player_pos[2]) ** 2)
        # 观测构造镜像实验组（含 hp 上下文）：R_M1_2（攻击后 HP 应下降）
        # 的判定数据从事件流提取——以当前实体为 target 的 hp_changed 的
        # hp_before/hp_after。此前对照组 context 缺 hp 上下文，导致其裁决
        # 从不触发 R_M1_2、全轮判「无异常」，与实验组真值口径不一致、
        # 对比退化为满分——现补齐，保证「是否异常」两侧同口径。
        eid = entity.get("entity_id")
        hp_before = None
        hp_after = None
        for ev in (events or []):
            if ev.get("kind") != "hp_changed":
                continue
            if ev.get("target_entity") != eid:
                continue
            hp_before = ev.get("hp_before")
            hp_after = ev.get("hp_after")
            break
        return {
            "entity": entity,
            "events": events,
            "context": {
                "distance_to_player": dist,
                "entity_in_scan": True,
                "entity_in_later_scan": True,
                "hp_before": hp_before,
                "hp_after": hp_after,
            },
            "label_history": self._label_history,
        }

    @staticmethod
    def _combo_of(entity):
        kind = str(entity.get("kind", ""))
        labels = sorted(str(x) for x in (entity.get("labels") or []))
        return kind + "|" + "|".join(labels), [kind] + labels

    # ── 归纳结论 ──────────────────────────────────────────

    def _conclusions(self):
        """当前统计模型的归纳结论快照（结构化，自然语言仅人类可读）。"""
        out = []
        for key in sorted(self._stats.keys()):
            st = self._stats[key]
            p = (st["anomalies"] / st["n"]) if st["n"] else self.prior_anomaly_prob
            out.append({
                "combo": key.split("|"),
                "n_observed": st["n"],
                "n_anomalies": st["anomalies"],
                "p_anomaly": p,
                "prediction": ("anomaly" if p > self.anomaly_prob_threshold
                               else "normal"),
            })
        return out

    # ── 解体判据与报告 ─────────────────────────────────────

    def _check_functional_death(self):
        """判据②（同口径）：连续 K 轮 score < τ。"""
        k = self.functional_death_k
        tau = self.qs_kernel.convergence_threshold
        hist = self._score_history
        return len(hist) >= k and all(s < tau for s in hist[-k:])

    def _build_report(self, criterion, completed):
        return {
            "group": "baseline_statistical",
            "rounds_target": self.rounds,
            "completed": completed,
            "disintegrated": criterion is not None,
            "criterion": criterion,
            "virtual_lifespan": completed,
            "score_history": list(self._score_history),
            "accuracy_history": list(self._acc_history),
            "converged_final": self.qs_kernel.check_monotonicity(
                scores=list(self._score_history))["converged"],
            "criteria_applicability": {
                "semantic_death": "not_applicable",
                "functional_death": "applicable",
                "over_budget": "not_applicable",
            },
            "model_snapshot": {
                "combos": len(self._stats),
                "total_observations": sum(
                    st["n"] for st in self._stats.values()),
                "label_history_size": len(self._label_history),
            },
            "round_details": list(self._round_details),
        }


# ── 对比口径产物（P_W2_5 检验的数据底座）─────────────────────


def compare_reports(exp_report, base_report, min_rounds=5):
    """对齐实验组与对照组报告，输出可作统计检验的成对序列。

    逐轮配对依据：两侧 round_details 的 round_id 交集，分数取各轮
    alignment_score（实验组为余弦对齐分，对照组为 2*acc-1 映射分，
    同域 [-1,1]）；任一侧该轮无分（如空实体轮）则剔除该轮。

    S29.15 护栏：配对轮数 < min_rounds（S21 契约 compared_rounds>=5）
    直接 fail-loud——此前静默截断到交集、均值基于截断前缀，可静默
    偏移 P_W2_5 结论（任一侧提前解体/终止即触发）。truncated 标记
    两侧轮数不齐（交集收缩或 None 剔除）。
    """
    exp_scores = {
        rd.get("round_id"): rd.get("alignment_score")
        for rd in exp_report.get("round_details", []) or []
        if rd.get("round_id") is not None  # S29.17 F26：None 轮不参与配对
    }
    base_scores = {
        rd.get("round_id"): rd.get("alignment_score")
        for rd in base_report.get("round_details", []) or []
        if rd.get("round_id") is not None
    }
    common = sorted(set(exp_scores) & set(base_scores))
    exp_series, base_series = [], []
    for rid in common:
        e, b = exp_scores[rid], base_scores[rid]
        if e is None or b is None:
            continue
        exp_series.append(e)
        base_series.append(b)
    n = len(exp_series)
    truncated = n < min(len(exp_scores), len(base_scores))
    if n < min_rounds:
        raise ValueError(
            f"compare_reports: paired rounds {n} < min_rounds {min_rounds} "
            f"(exp_rounds={len(exp_scores)}, base_rounds={len(base_scores)})"
            f"——截断对比会静默偏移 P_W2_5，fail-loud（S29.15）")
    life_e = exp_report.get("virtual_lifespan")
    life_b = base_report.get("virtual_lifespan")
    return {
        "exp_series": exp_series,
        "baseline_series": base_series,
        "compared_rounds": n,
        "truncated": truncated,
        "exp_mean": (sum(exp_series) / n) if n else None,
        "baseline_mean": (sum(base_series) / n) if n else None,
        "converged_exp": exp_report.get("converged_final"),
        "converged_baseline": base_report.get("converged_final"),
        "lifespan_exp": life_e,
        "lifespan_baseline": life_b,
        "lifespan_delta": (life_e or 0) - (life_b or 0),
        "exp_disintegrated": exp_report.get("disintegrated"),
        "baseline_disintegrated": base_report.get("disintegrated"),
        "exp_criterion": exp_report.get("criterion"),
        "baseline_criterion": base_report.get("criterion"),
    }
