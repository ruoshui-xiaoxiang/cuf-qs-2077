"""W2 候选公理自主激活引擎（S28 弱自进化机制）。

候选公理池（world_rules.candidate_axioms）中的公理**不直接注入**引擎；
ActivationEngine 依据观测证据（事件计数 + 滑动窗口）自主决定激活——
系统从候选池中选择"哪些规则值得验证"，对应 CUF 框架内假设空间的
选择性扩展（弱自进化：选择而非生成）。

关键语义（反套壳对齐）：
- 激活 ≠ 宣告真/假：激活只表示"该规则进入验证"；激活后与现有公理
  同权参与假设生成/反例判定/降级（真值判定仍归 rule_validator 硬编码）。
- 激活判定为硬编码统计阈值（事件计数 ≥ min_count 于最近 window_rounds
  轮），无 LLM；激活不写回 W1（world_rules 只读），只调
  AxiomEngine.add_axiom（轮间生效，幂等）。
- 防误激活：窗口跨轮累计（单轮突发不计）、阈值可配置。
"""

import json
import os

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "experiment_config.json"
)


class ActivationEngine:
    """候选公理激活引擎——证据驱动的选择性公理激活。"""

    def __init__(self, rule_validator, axiom_engine, enabled=True,
                 min_count_overrides=None, window_overrides=None):
        self.candidates = rule_validator.get_candidates()
        self.engine = axiom_engine
        self.enabled = bool(enabled)
        self._overrides_min = dict(min_count_overrides or {})
        self._overrides_window = dict(window_overrides or {})
        self._window = {}          # axiom_id -> [(round_id, count)]
        self._activated = set()    # 已激活 axiom_id
        self._events = []          # 激活事件记录
        self._last_round = 0

    @classmethod
    def from_config(cls, rule_validator, axiom_engine, config_path=None):
        """从 config activation 段装配（enabled 默认 True）。"""
        path = config_path or DEFAULT_CONFIG_PATH
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        act = cfg.get("activation", {}) or {}
        return cls(rule_validator, axiom_engine,
                   enabled=act.get("enabled", True))

    # ── 主流程 ──────────────────────────────────────────────

    def _window_rounds(self, cand):
        tr = cand.get("activation_trigger", {}) or {}
        return max(0, int(self._overrides_window.get(
            cand["axiom_id"], tr.get("window_rounds", 5))))

    def _min_count(self, cand):
        tr = cand.get("activation_trigger", {}) or {}
        return int(self._overrides_min.get(
            cand["axiom_id"], tr.get("min_count", 3)))

    def feed(self, w0_data, round_id):
        """消费一轮观测：统计候选触发事件数（窗口累计）。

        S29.17 P2 窗口卫生：同一 round_id 重复 feed 直接报错（防止
        复用/误调导致计数翻倍）；窗口记录按轮裁剪，不长膨胀。
        """
        if not self.enabled:
            return
        self._last_round = int(round_id)
        events = (w0_data or {}).get("events", []) or []
        for cand in self.candidates:
            aid = cand["axiom_id"]
            if aid in self._activated:
                continue
            hist = self._window.setdefault(aid, [])
            if hist and hist[-1][0] == self._last_round:
                raise ValueError(
                    f"axiom_activator: duplicate feed for round_id "
                    f"{self._last_round} (candidate {aid})")
            kinds = set(cand.get("activation_trigger", {}).get(
                "event_kinds", []))
            cnt = sum(1 for e in events if e.get("kind") in kinds)
            hist.append((self._last_round, cnt))
            # 裁剪：只保留窗口可能用到的记录（轮号 > 当前轮 - 窗口）
            w = self._window_rounds(cand)
            cutoff = self._last_round - w
            self._window[aid] = [(rid, c) for rid, c in hist
                                 if rid > cutoff]

    def end_round(self):
        """轮间检查：窗口内计数 ≥ min_count 的候选激活（轮间生效）。

        S29.17 P2：窗口按**轮号**过滤（(last_round - W, last_round]），
        不再按记录条数切片——跳轮时记录条数与日历轮数脱钩；
        window=0 → 空窗口（此前 [-0:] 返回全部历史，语义相反）。
        """
        if not self.enabled:
            return []
        activated_now = []
        for cand in self.candidates:
            aid = cand["axiom_id"]
            if aid in self._activated:
                continue
            tr = cand.get("activation_trigger", {}) or {}
            window = self._window_rounds(cand)
            min_count = self._min_count(cand)
            cutoff = self._last_round - window
            recent = [(rid, c) for rid, c in self._window.get(aid, [])
                      if rid > cutoff]
            total = sum(c for _, c in recent)
            if total >= int(min_count):
                # 修复 2（2026-08-25）：激活时把候选 assertion 的标签约束
                # 转为初始边界条件，避免"裸规则"（ICC missing_boundary 88
                # 条的根因 = 激活只传 hypothesis+anchor，丢了适用范围）。
                assertion = cand.get("assertion") or {}
                bc_labels = list(dict.fromkeys(
                    (assertion.get("label_match_any") or []) +
                    (assertion.get("label_require") or [])))
                bc = ([{"kind": assertion.get("kind", ""),
                        "labels": bc_labels}] if bc_labels else None)
                self.engine.add_axiom(aid, cand["hypothesis"],
                                      cand["anchor"],
                                      boundary_conditions=bc)
                self._activated.add(aid)
                ev = {
                    "axiom_id": aid,
                    "round_id": self._last_round,
                    "evidence_count": total,
                    "window_rounds": int(window),
                    "min_count": int(min_count),
                    "trigger": tr,
                }
                self._events.append(ev)
                activated_now.append(ev)
        return activated_now

    # ── 查询 ──────────────────────────────────────────────

    def get_activation_events(self):
        """返回全部激活事件（含轮次/证据计数/阈值）。"""
        return list(self._events)

    def get_inactive_candidates(self):
        """返回尚未激活的候选（report 归档，可追溯"无证据未激活"）。"""
        return [c for c in self.candidates
                if c["axiom_id"] not in self._activated]

    def activated_axiom_ids(self):
        return sorted(self._activated)

    def get_status(self):
        """激活引擎状态快照。"""
        return {
            "enabled": self.enabled,
            "candidate_count": len(self.candidates),
            "activated": self.activated_axiom_ids(),
            "inactive": [c["axiom_id"]
                         for c in self.get_inactive_candidates()],
            "activation_events": list(self._events),
        }
