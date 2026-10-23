"""W2 QS 内核——量化收敛判定，纯向量运算。

对应 CUF v7 W₂ 稳态自指体的收敛监控面。
反套壳铁则 #1：QS 收敛计算为独立向量运算，不修改模型权重，不由模型生成分数。
锁定决策 #1：目标向量 g 用 7B 模型自身嵌入生成（弃用 CLIP），保证 g 与隐状态 s_t 同空间。
锁定决策 #3：收敛判定容差带——滑动窗口均值单调 + 允许单点 ±ε 回撤，ε 写入配置。
"""

import json
import math
import os


class QSKernel:
    """QS 收敛内核——硬编码向量运算，无 LLM 参与。

    三个核心方法：
    - compute_target_g: 用 7B 模型嵌入生成目标向量 g（或从外部注入）
    - compute_alignment_score: 余弦内积计算对齐分 s_t = cos(g, s_t)
    - check_monotonicity: 容差带收敛判定
    """

    def __init__(self, window_size=5, epsilon=0.01, min_rounds=5,
                 convergence_threshold=0.6):
        """
        Args:
            window_size: 滑动窗口大小（锁定决策 #3）
            epsilon: 允许单点回撤量 ±ε（锁定决策 #3）
            min_rounds: 收敛判定最少轮数
            convergence_threshold: 对齐分收敛阈值。单一事实源为
                config qs.convergence_threshold（当前 0.6，2026-08-25
                用户批准重标定，论证见配置 _threshold_note）；默认值
                与配置保持一致，禁止双轨（S29.15 τ 统一）。

        Raises:
            ValueError: 逻辑学审查 D：ε ≥ τ 时 fail-loud 抛错。
                ε（容差带）与 τ（收敛阈值）同属对齐分 [-1,1] 域，
                必须满足 0 < ε ≪ convergence_threshold；否则容差带语义失效。
        """
        self.window_size = int(window_size)
        eps = float(epsilon)
        tau = float(convergence_threshold)
        if eps <= 0:
            raise ValueError(f"epsilon must be > 0, got {eps}")
        if eps >= tau:
            raise ValueError(
                f"epsilon must be strictly less than convergence_threshold "
                f"(same domain cosine score [-1,1]); got epsilon={eps}, "
                f"threshold={tau}. If eps >= tau, the tolerance band "
                f"semantics collapses (逻辑学审查 D).")
        self.epsilon = eps
        self.min_rounds = int(min_rounds)
        self.convergence_threshold = tau
        self._target_g = None
        self._history = []

    @classmethod
    def from_config(cls, config_path=None):
        """从配置文件构造。"""
        path = config_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "experiment_config.json"
        )
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        qs_cfg = cfg.get("qs", {})
        return cls(
            window_size=qs_cfg.get("window_size", 5),
            epsilon=qs_cfg.get("epsilon", 0.01),
            min_rounds=qs_cfg.get("min_rounds", 5),
            convergence_threshold=qs_cfg.get("convergence_threshold", 0.6),
        )

    # ── 目标向量 g ─────────────────────────────────────────

    def compute_target_g(self, embedding=None):
        """生成目标向量 g。

        锁定决策 #1：g 用 7B 模型自身嵌入生成，保证与 s_t 同空间。
        在 S13（7B 模型对接）之前，接受外部注入的 embedding 向量。

        Args:
            embedding: 7B 模型输出的嵌入向量（list[float] 或 numpy array）。
                       若为 None，返回 None（等 S13 接通后由模型提供）。

        Returns:
            归一化后的目标向量 g（list[float]），或 None。
        """
        if embedding is None:
            return None

        vec = [float(x) for x in embedding]
        norm = math.sqrt(sum(x * x for x in vec))
        if norm < 1e-12:
            return None

        self._target_g = [x / norm for x in vec]
        return list(self._target_g)

    def set_target_g(self, g):
        """直接注入已归一化的目标向量 g（用于测试或从存档恢复）。"""
        self._target_g = [float(x) for x in g]
        return list(self._target_g)

    def get_target_g(self):
        """获取当前目标向量 g。"""
        return list(self._target_g) if self._target_g else None

    def get_history(self):
        """获取对齐分历史（浅拷贝，只读语义）。"""
        return list(self._history)

    # ── 对齐分 ────────────────────────────────────────────

    @staticmethod
    def _normalize(vec):
        """L2 归一化。零向量无方向、对齐分无定义，直接抛错（fail-loud），
        禁止静默替换为伪造向量。"""
        norm = math.sqrt(sum(x * x for x in vec))
        if norm < 1e-12:
            raise ValueError("zero-vector input: alignment score undefined")
        return [x / norm for x in vec]

    def compute_alignment_score(self, hidden_state):
        """计算对齐分 s_t = cos(g, s_t)。

        反套壳铁则 #5：QS 收敛计算为独立向量运算，不修改模型权重，
        不由模型生成分数。

        Args:
            hidden_state: 模型隐状态 s_t（list[float]），来自 forward hook（S13）。

        Returns:
            对齐分 float ∈ [-1.0, 1.0]，或 None（g 未设置）。

        Raises:
            ValueError: 维度不一致或零向量输入（fail-loud，禁止静默截断/伪造）。
        """
        if self._target_g is None:
            return None

        s = [float(x) for x in hidden_state]
        g = self._target_g

        if len(s) != len(g):
            raise ValueError(
                f"dimension mismatch: hidden_state dim {len(s)} != target_g dim {len(g)}")

        s = self._normalize(s)

        score = sum(a * b for a, b in zip(s, g))
        # S29.17 P2：NaN/Inf 防护——非有限分进入历史后所有 < / >= 比较
        # 静默失效（monotonic 恒真、阈值判定恒假），fail-loud。
        if not math.isfinite(score):
            raise ValueError(f"non-finite alignment score: {score}")
        self._history.append(score)
        return score

    # ── 收敛判定 ──────────────────────────────────────────

    def check_monotonicity(self, scores=None):
        """容差带收敛判定。

        锁定决策 #3：滑动窗口均值单调 + 允许单点 ±ε 回撤。

        判定逻辑：
        1. 取最近 window_size 个对齐分
        2. 计算窗口内均值序列
        3. 均值序列必须单调不减（允许单点回撤 ≤ ε）
        4. 最新均值 >= convergence_threshold
        5. 至少经过 min_rounds 轮

        Args:
            scores: 对齐分列表，若为 None 则使用内部历史。

        Returns:
            {
                "converged": bool,
                "window_mean": float,
                "window_scores": list,
                "mean_series": list,
                "monotonic": bool,
                "above_threshold": bool,
                "enough_rounds": bool,
                "reason": str
            }
        """
        hist = scores if scores is not None else self._history
        if len(hist) < self.min_rounds:
            return {
                "converged": False,
                "window_mean": None,
                "window_scores": hist[-self.window_size:],
                "mean_series": [],
                "monotonic": False,
                "above_threshold": False,
                "enough_rounds": False,
                "reason": f"only {len(hist)} rounds, need {self.min_rounds}",
            }

        window = hist[-self.window_size:]
        if len(window) < 2:
            return {
                "converged": False,
                "window_mean": window[0] if window else None,
                "window_scores": window,
                "mean_series": [],
                "monotonic": True,
                "above_threshold": False,
                "enough_rounds": True,
                "reason": "insufficient window data",
            }

        # 计算窗口内均值序列（累积均值）
        mean_series = []
        cumsum = 0.0
        for i, s in enumerate(window):
            cumsum += s
            mean_series.append(cumsum / (i + 1))

        # 单调性检查：允许单点 ±ε 回撤
        monotonic = True
        violations = []
        for i in range(1, len(mean_series)):
            if mean_series[i] < mean_series[i - 1] - self.epsilon:
                monotonic = False
                violations.append({
                    "index": i,
                    "prev_mean": mean_series[i - 1],
                    "curr_mean": mean_series[i],
                    "drop": mean_series[i - 1] - mean_series[i],
                })

        latest_mean = mean_series[-1]
        above_threshold = latest_mean >= self.convergence_threshold

        converged = monotonic and above_threshold

        if not converged:
            reasons = []
            if not monotonic:
                reasons.append(f"{len(violations)} monotonicity violation(s)")
            if not above_threshold:
                reasons.append(
                    f"mean {latest_mean:.4f} < threshold {self.convergence_threshold}")
            reason = "; ".join(reasons)
        else:
            reason = f"converged: mean={latest_mean:.4f} >= {self.convergence_threshold}"

        return {
            "converged": converged,
            "window_mean": latest_mean,
            "window_scores": window,
            "mean_series": mean_series,
            "monotonic": monotonic,
            "above_threshold": above_threshold,
            "enough_rounds": True,
            "violations": violations,
            "reason": reason,
        }

    # ── 漂移检测 ──────────────────────────────────────────

    def detect_drift(self, scores=None, drift_threshold=0.1):
        """检测对齐分漂移。

        当最近一个对齐分与窗口均值的差异超过 drift_threshold 时判定漂移。

        Args:
            scores: 对齐分列表，若为 None 则使用内部历史。
            drift_threshold: 漂移阈值。

        Returns:
            {"drifted": bool, "latest_score": float, "window_mean": float,
             "delta": float, "reason": str}
        """
        hist = scores if scores is not None else self._history
        if len(hist) < 2:
            return {"drifted": False, "latest_score": hist[-1] if hist else None,
                    "window_mean": None, "delta": 0.0,
                    "reason": "insufficient data"}

        latest = hist[-1]
        window = hist[-(self.window_size + 1):-1] if len(hist) > self.window_size else hist[:-1]
        if not window:
            return {"drifted": False, "latest_score": latest,
                    "window_mean": None, "delta": 0.0,
                    "reason": "no baseline"}

        window_mean = sum(window) / len(window)
        delta = abs(latest - window_mean)
        drifted = delta > drift_threshold

        return {
            "drifted": drifted,
            "latest_score": latest,
            "window_mean": window_mean,
            "delta": delta,
            "reason": (f"drift detected: delta={delta:.4f} > {drift_threshold}"
                       if drifted else f"stable: delta={delta:.4f} <= {drift_threshold}"),
        }

    # ── 状态管理 ──────────────────────────────────────────

    def reset(self):
        """清空历史（新实验或新公理时调用）。"""
        self._history = []

    # 注：get_history 定义见「目标向量」节（S29.17：删除重复定义）。

    def get_state(self):
        """返回 QS 内核状态快照（用于日志归档）。"""
        return {
            "target_g_dim": len(self._target_g) if self._target_g else 0,
            "target_g_set": self._target_g is not None,
            "history_len": len(self._history),
            "window_size": self.window_size,
            "epsilon": self.epsilon,
            "min_rounds": self.min_rounds,
            "convergence_threshold": self.convergence_threshold,
            "latest_score": self._history[-1] if self._history else None,
            "latest_convergence": self.check_monotonicity() if self._history else None,
        }
