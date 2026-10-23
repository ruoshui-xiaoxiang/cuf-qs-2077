"""S9 QS 内核脱机自测。

验收标准：输入模拟向量输出正确对齐分，可检出漂移。
反套壳铁则 #5：QS 收敛计算为独立向量运算，不修改模型权重，不由模型生成分数。
"""

import sys
import os
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from w2.qs_kernel import QSKernel

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    # ── 1. 构造与配置 ──
    print("\n=== 1. 构造与配置 ===")
    qs = QSKernel(window_size=5, epsilon=0.01, min_rounds=5,
                  convergence_threshold=0.95)
    check("window_size == 5", qs.window_size == 5)
    check("epsilon == 0.01", qs.epsilon == 0.01)
    check("min_rounds == 5", qs.min_rounds == 5)
    check("threshold == 0.95", qs.convergence_threshold == 0.95)
    check("target_g is None initially", qs.get_target_g() is None)

    # from_config
    qs_cfg = QSKernel.from_config()
    check("from_config window_size == 5", qs_cfg.window_size == 5)
    check("from_config epsilon == 0.01", qs_cfg.epsilon == 0.01)
    check("from_config threshold == 0.6", qs_cfg.convergence_threshold == 0.6)

    # ── 2. 目标向量 g ──
    print("\n=== 2. 目标向量 g ===")
    # compute_target_g with None
    g_none = qs.compute_target_g(None)
    check("compute_target_g(None) returns None", g_none is None)

    # compute_target_g with embedding
    emb = [3.0, 4.0, 0.0]
    g = qs.compute_target_g(emb)
    check("g is not None", g is not None)
    # 归一化后 g = [0.6, 0.8, 0.0]
    check("g[0] == 0.6", abs(g[0] - 0.6) < 1e-6, f"got {g[0]}")
    check("g[1] == 0.8", abs(g[1] - 0.8) < 1e-6, f"got {g[1]}")
    check("g[2] == 0.0", abs(g[2]) < 1e-6, f"got {g[2]}")
    check("g norm == 1.0", abs(math.sqrt(sum(x*x for x in g)) - 1.0) < 1e-6)

    # set_target_g
    qs2 = QSKernel()
    g2 = qs2.set_target_g([1.0, 0.0, 0.0])
    check("set_target_g returns g", g2 is not None)
    check("g[0] == 1.0", abs(g2[0] - 1.0) < 1e-6)

    # zero vector → None
    g_zero = QSKernel().compute_target_g([0.0, 0.0, 0.0])
    check("zero vector → None", g_zero is None)

    # ── 3. 对齐分计算 ──
    print("\n=== 3. 对齐分计算 ===")
    qs3 = QSKernel()
    qs3.set_target_g([1.0, 0.0, 0.0])

    # 完全对齐
    score_aligned = qs3.compute_alignment_score([1.0, 0.0, 0.0])
    check("cos(同向) == 1.0", abs(score_aligned - 1.0) < 1e-6, f"got {score_aligned}")

    # 正交
    score_ortho = qs3.compute_alignment_score([0.0, 1.0, 0.0])
    check("cos(正交) == 0.0", abs(score_ortho) < 1e-6, f"got {score_ortho}")

    # 反向
    score_opp = qs3.compute_alignment_score([-1.0, 0.0, 0.0])
    check("cos(反向) == -1.0", abs(score_opp - (-1.0)) < 1e-6, f"got {score_opp}")

    # 未设置 g → None
    qs_no_g = QSKernel()
    score_none = qs_no_g.compute_alignment_score([1.0, 0.0])
    check("no g → None", score_none is None)

    # 维度不一致 → 抛 ValueError（fail-loud）
    qs4 = QSKernel()
    qs4.set_target_g([1.0, 0.0, 0.0, 0.0])
    try:
        qs4.compute_alignment_score([1.0, 0.0])
        check("dim mismatch raises ValueError", False, "no exception")
    except ValueError:
        check("dim mismatch raises ValueError", True)

    # 零向量隐状态 → 抛 ValueError（fail-loud，禁止静默伪造）
    qs4b = QSKernel()
    qs4b.set_target_g([1.0, 0.0, 0.0])
    try:
        qs4b.compute_alignment_score([0.0, 0.0, 0.0])
        check("zero vector raises ValueError", False, "no exception")
    except ValueError:
        check("zero vector raises ValueError", True)

    # ── 4. 收敛判定：收敛 ──
    print("\n=== 4. 收敛判定：收敛 ===")
    qs5 = QSKernel(window_size=5, epsilon=0.01, min_rounds=5,
                   convergence_threshold=0.95)
    # 直接用对齐分序列测试收敛逻辑（不依赖 g 的具体值）
    scores_conv = [0.80, 0.85, 0.93, 0.95, 0.96, 0.97, 0.97]
    qs5._history = list(scores_conv)

    result = qs5.check_monotonicity()
    check("enough_rounds", result["enough_rounds"] is True)
    check("monotonic", result["monotonic"] is True, str(result.get("violations")))
    check("above_threshold", result["above_threshold"] is True)
    check("converged", result["converged"] is True, result["reason"])

    # ── 5. 收敛判定：未收敛（轮数不足）──
    print("\n=== 5. 收敛判定：轮数不足 ===")
    qs6 = QSKernel(window_size=5, epsilon=0.01, min_rounds=5)
    qs6._history = [0.95, 0.96, 0.97]
    result6 = qs6.check_monotonicity()
    check("not enough rounds", result6["enough_rounds"] is False)
    check("not converged", result6["converged"] is False)

    # ── 6. 收敛判定：单调性违反 ──
    print("\n=== 6. 收敛判定：单调性违反 ===")
    qs7 = QSKernel(window_size=5, epsilon=0.01, min_rounds=5)
    # 前面上升，后面突然大跌
    scores_drop = [0.90, 0.92, 0.94, 0.96, 0.97, 0.50, 0.50]
    qs7._history = list(scores_drop)
    result7 = qs7.check_monotonicity()
    check("monotonicity violated", result7["monotonic"] is False,
          str(result7.get("violations", [])))
    check("not converged (drop)", result7["converged"] is False)
    check("has violations", len(result7.get("violations", [])) > 0)

    # ── 7. 收敛判定：容差带允许单点小回撤 ──
    print("\n=== 7. 收敛判定：容差带允许小回撤 ===")
    qs8 = QSKernel(window_size=5, epsilon=0.02, min_rounds=5,
                   convergence_threshold=0.95)
    # 单点回撤 0.01 <= epsilon 0.02
    scores_eps = [0.90, 0.93, 0.96, 0.97, 0.96, 0.97]
    qs8._history = list(scores_eps)
    result8 = qs8.check_monotonicity()
    check("small dip within epsilon", result8["monotonic"] is True,
          str(result8.get("violations")))
    check("converged with dip", result8["converged"] is True, result8["reason"])

    # ── 8. 漂移检测 ──
    print("\n=== 8. 漂移检测 ===")
    qs9 = QSKernel(window_size=5)
    qs9._history = [0.95, 0.96, 0.95, 0.96, 0.95]
    drift = qs9.detect_drift(drift_threshold=0.1)
    check("no drift (stable)", drift["drifted"] is False)

    # 添加一个大漂移
    qs9._history.append(0.50)
    drift2 = qs9.detect_drift(drift_threshold=0.1)
    check("drift detected", drift2["drifted"] is True, drift2["reason"])
    check("delta > 0.1", drift2["delta"] > 0.1, f"delta={drift2['delta']}")

    # ── 9. 状态管理 ──
    print("\n=== 9. 状态管理 ===")
    qs10 = QSKernel(window_size=5, epsilon=0.01, min_rounds=5,
                    convergence_threshold=0.95)
    qs10.set_target_g([1.0, 0.0])
    qs10._history = [0.90, 0.93, 0.96, 0.97, 0.97]
    state = qs10.get_state()
    check("state target_g_set", state["target_g_set"] is True)
    check("state target_g_dim == 2", state["target_g_dim"] == 2)
    check("state history_len == 5", state["history_len"] == 5)
    check("state latest_score is not None", state["latest_score"] is not None)
    check("state latest_convergence is not None",
          state["latest_convergence"] is not None)

    qs10.reset()
    check("reset clears history", len(qs10.get_history()) == 0)
    check("reset keeps target_g", qs10.get_target_g() is not None)

    # ── 10. 反套壳铁则 #5 验证 ──
    print("\n=== 10. 反套壳铁则 #5 ===")
    # QS 计算不修改任何权重，纯向量运算
    qs11 = QSKernel()
    g_before = qs11.get_target_g()
    qs11.set_target_g([1.0, 0.0])
    qs11.compute_alignment_score([0.95, 0.0])
    qs11.check_monotonicity()
    qs11.detect_drift()
    g_after = qs11.get_target_g()
    # g 不应被对齐分计算修改
    check("g unchanged after computations",
          g_after == [1.0, 0.0], f"got {g_after}")

    # 对齐分不依赖任何外部模型调用
    check("no model dependency", not hasattr(qs11, "model"))

    # ── 11. 逻辑学审查 D：ε ≥ τ fail-loud ──
    print("\n=== 11. 逻辑学审查 D：epsilon-tau fail-loud ===")
    # 直接构造 ε > τ → 必须 ValueError
    try:
        QSKernel(window_size=3, epsilon=0.5, min_rounds=1,
                 convergence_threshold=0.4)
        check("epsilon_gt_tau_raises_direct_construct", False, "no exception")
    except ValueError as e:
        check("epsilon_gt_tau_raises_direct_construct", True, str(e))

    # ε == τ → 也必须抛（容差带语义失效）
    try:
        QSKernel(window_size=3, epsilon=0.5, min_rounds=1,
                 convergence_threshold=0.5)
        check("epsilon_eq_tau_raises_direct_construct", False, "no exception")
    except ValueError as e:
        check("epsilon_eq_tau_raises_direct_construct", True, str(e))

    # 正常构造（ε ≪ τ）→ OK
    try:
        qs_ok = QSKernel(window_size=3, epsilon=0.01, min_rounds=1,
                         convergence_threshold=0.95)
        check("epsilon_lt_tau_ok", True)
    except ValueError:
        check("epsilon_lt_tau_ok", False, "unexpected ValueError")

    # ── 总结 ──
    print(f"\n{'='*50}")
    print(f"S9 QS 内核自测: {PASS} passed, {FAIL} failed")
    print(f"{'='*50}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
