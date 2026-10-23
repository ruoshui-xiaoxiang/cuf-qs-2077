"""W3 日志归档模块（锁定决策 #7 JSONL 追加式 + #11 C_H 代理度量）。

每轮实验四层全量数据归档为一行 JSONL，含 C_H 三项代理度量。
任意轮次可通过 load_round 一键复现状态。
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("log_recorder")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "experiment_config.json"
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

DEFAULT_BUDGET_B = 100.0

# 逻辑学审查 B：分项预算默认值（C_H 三子维度异质，不可相加比较）
DEFAULT_STRUCT_BUDGET = 50     # struct ∈ O(10)：活跃公理数 + 边界条件数
DEFAULT_EXTEND_BUDGET = 10    # extend ∈ O(10)：min(N_counter, threshold)
DEFAULT_ADAPT_BUDGET = 1000   # adapt ∈ O(100-1000)：采样吞吐 + 模型更新数


class LogRecorder:
    """JSONL 追加式日志归档，每轮四层全量 + C_H 三项代理度量。"""

    def __init__(self, log_path, budget_b=DEFAULT_BUDGET_B,
                 struct_budget=None, extend_budget=None, adapt_budget=None):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.budget_b = float(budget_b)                # 兼容字段，绘图统计用
        self.struct_budget = int(struct_budget) if struct_budget is not None else DEFAULT_STRUCT_BUDGET
        self.extend_budget = int(extend_budget) if extend_budget is not None else DEFAULT_EXTEND_BUDGET
        self.adapt_budget = int(adapt_budget) if adapt_budget is not None else DEFAULT_ADAPT_BUDGET
        if self.struct_budget <= 0 or self.extend_budget <= 0 or self.adapt_budget <= 0:
            raise ValueError("component budgets must be positive integers")

    @classmethod
    def from_config(cls, connector=None, config_path=None, log_path=None):
        cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        c_h_cfg = cfg.get("c_h", {})
        budget_b = c_h_cfg.get("budget_b", DEFAULT_BUDGET_B)
        sb = c_h_cfg.get("struct_budget", None)
        eb = c_h_cfg.get("extend_budget", None)
        ab = c_h_cfg.get("adapt_budget", None)
        if log_path is None:
            log_dir = Path(cfg.get("logging", {}).get("log_dir", str(DEFAULT_LOG_DIR)))
            log_path = log_dir / "experiment.jsonl"
        return cls(log_path, budget_b=budget_b,
                   struct_budget=sb, extend_budget=eb, adapt_budget=ab)

    # ── 主接口 ──────────────────────────────────────────────

    def record_round(self, round_id, w0_data=None, w1_data=None,
                     w2_data=None, w3_data=None):
        """归档一轮四层全量数据 + C_H 代理度量，返回写入的 entry。"""
        w0_data = w0_data or {}
        w1_data = w1_data or {}
        w2_data = w2_data or {}
        w3_data = w3_data or {}

        c_h = self._compute_c_h(w0_data, w2_data)
        entry = {
            "round_id": round_id,
            "timestamp": time.time(),
            "w0": w0_data,
            "w1": w1_data,
            "w2": w2_data,
            "w3": w3_data,
            "c_h": c_h,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        log.info("round %s archived (c_h.total=%s, within_budget=%s)",
                 round_id, c_h["total"], c_h["within_budget"])
        return entry

    def load_round(self, round_id):
        """读取指定轮次的完整归档，找不到返回 None。"""
        for entry in self._iter_entries():
            if entry.get("round_id") == round_id:
                return entry
        return None

    def list_rounds(self):
        """返回所有已归档轮次 ID 列表（按写入顺序）。"""
        return [e["round_id"] for e in self._iter_entries()]

    def load_all(self):
        """返回全部归档条目列表。"""
        return list(self._iter_entries())

    # ── C_H 代理度量（锁定决策 #11 + 逻辑学审查 A/B）─────

    def _compute_c_h(self, w0_data, w2_data):
        """计算 C_H 三项代理度量。

        A. C_H,struct  = 活跃公理数 + 边界条件总数（活跃口径：active + warned，
                        2026-08-25 用户批准；仅 deprecated 视为失去稳态贡献）
        B. C_H,adapt   = 当轮采样次数 + 模型更新次数
        C. C_H,extend  = min(counterexamples, downgrade_threshold)（逻辑学审查 A：
                        随反例数单调递增；饱和于阈值，保持 bounded 代理度量）

        ── 预算判定（逻辑学审查 B1/B2 + P_C 判据修正 2026-08-25）──
        三子维度异质（不同量纲），不做 total vs budget_b 的合加比较。
        P_C（Γ脆 判据③）= 【C_struct > B_struct OR C_extend > B_extend 判
        within_budget=False】（备忘录 4.2 修正版：struct 与 extend 均参与
        判据③，规则池膨胀（struct 超载）也必须触发受控解体）。
        C_adapt 超预算仅打 overload 标记，不触发 P_C 解体
          —— 对应生物：感知繁忙本身不直接致命；
          对应 AI：模型不可用 / 上下文溢出属系统前置条件（标记「系统不可用」/「系统不可信」），不进判据②，不走 P_C。
        """
        # ── C_H,struct ──
        active_axioms = 0
        boundary_conditions = 0
        axioms = w2_data.get("axioms", [])
        if isinstance(axioms, list):
            for a in axioms:
                if not isinstance(a, dict):
                    continue
                if a.get("status", "active") not in ("active", "warned"):
                    continue
                active_axioms += 1
                bc = a.get("boundary_conditions", [])
                if isinstance(bc, list):
                    boundary_conditions += len(bc)
        c_h_struct = active_axioms + boundary_conditions

        # ── C_H,adapt ──
        sampling_count = 0
        stats = w0_data.get("stats", {})
        if isinstance(stats, dict):
            sampling_count = (stats.get("raw_entities", 0) +
                              stats.get("raw_events", 0))
        model_updates = w2_data.get("model_update_count", 0)
        c_h_adapt = sampling_count + model_updates

        # ── C_H,extend（逻辑学审查 A：单调递增 min(N, threshold) 方案）──
        counterexamples = w2_data.get("cumulative_counterexamples", 0)
        downgrade_threshold = w2_data.get("downgrade_threshold", 5)
        try:
            ce_int = int(counterexamples)
            th_int = int(downgrade_threshold)
        except (TypeError, ValueError) as e:
            # 安全检查 A1 / S29.17 P2：fail-loud——核心度量异常静默清零
            # 等于伪造 C_H 预算判定依据（此前回退 (0, 5) 与"不静默"
            # 注释自相矛盾）。
            raise ValueError(
                f"C_H,extend parse failed (counterexamples="
                f"{counterexamples!r}, downgrade_threshold="
                f"{downgrade_threshold!r}): {e}")
        c_h_extend = min(ce_int, th_int) if ce_int >= 0 else 0

        total = c_h_struct + c_h_adapt + c_h_extend

        # ── 分项过载标记 + P_C 判定 ──
        struct_over = c_h_struct > self.struct_budget
        adapt_over = c_h_adapt > self.adapt_budget
        extend_over = c_h_extend > self.extend_budget
        # P_C（判据③）= struct 或 extend 任一超预算即 within_budget=False
        # （2026-08-25 修正：原仅 extend 参与，struct 只打标记 → 规则池
        # 膨胀失控无刹车；备忘录 4.2 明确 struct 也须触发解体）。
        # 诚实口径（S29.16 议题 1 方案 C）：默认参数下 extend 饱和于
        # 降级阈值=5 < extend_budget=10，extend 分支结构性不可达——
        # 判据③实际由 struct 主导；extend 是信念修正活动强度的成本
        # 记账/遥测通道，压力实证走注入低预算路径（见配置
        # _extend_budget_note 与 S29.12 pc_hl 批次）。
        within_budget = not (struct_over or extend_over)

        return {
            "struct": c_h_struct,
            "adapt": c_h_adapt,
            "extend": c_h_extend,
            "total": total,                          # 兼容字段，仅用于绘图统计
            "budget_b": self.budget_b,               # 兼容字段
            "budgets": {
                "struct_budget": self.struct_budget,
                "extend_budget": self.extend_budget,
                "adapt_budget": self.adapt_budget,
            },
            "overloads": {
                "struct_overload": bool(struct_over),
                "adapt_overload": bool(adapt_over),
                "extend_overload": bool(extend_over),
            },
            "within_budget": bool(within_budget),
            "detail": {
                "active_axioms": active_axioms,
                "boundary_conditions": boundary_conditions,
                "sampling_count": sampling_count,
                "model_updates": model_updates,
                "counterexamples": ce_int,
                "downgrade_threshold": th_int,
            },
        }

    # ── 内部 ────────────────────────────────────────────────

    def _iter_entries(self):
        if not self.log_path.exists():
            return
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # 安全检查 D3：损坏行记录行号（可定位修复/排除）
                    log.warning("corrupt JSONL line %d skipped in %s",
                                line_no, self.log_path)
