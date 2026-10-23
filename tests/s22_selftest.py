"""S22 可视化与统计离线自测（RED→GREEN 契约）。

六大交付物（消费 S21 formal_batch 产物，输出 figures + stats_summary.json）：
1. 收敛曲线：实验组/对照组对齐分 vs 轮数（每公理一张 SVG）。
2. C_H 成本曲线：struct/adapt/extend 三子维度逐轮（异质禁合加——
   三条独立曲线，不做 total 叠加，锁定决策 #11）。
3. 虚拟寿命统计：每公理条件 mean/std/min/max + overall。
4. 锚点验证结果表：P_W2_5 / P_M1 / P_W1 / P_C / H_L 的机械验证与
   verdict（supported / falsified / not_triggered / not_tested），
   表述纪律：操作化验证口径，不称「证明」（锁定决策 #14）。
5. 量化指标：convergence_round / max_c_extend / warn_round /
   downgrade_round / virtual_lifespan / boundary_final_count。
6. 统计检验：成对精确重排检验（主判定，纯 Python 全枚举，
   确定性无分布假设，n≤20）+ 成对 t 检验（纯 Python 不完全贝塔
   实现，scipy 可用时交叉核对）；检验单元 = 种子级运行均值
   （锁定决策 #9 独立性单元）。

反套壳对齐：全部统计判定硬编码纯函数，零 LLM、零模型依赖；
图形仅人类可读产物，数据以 stats_summary.json 为准。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w3.visualizer import (
    BatchVisualizer,
    paired_permutation_test,
    paired_t_test,
)
from w3.formal_batch import run_formal_batch
from s21_selftest import scenario_factory

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "experiment_config.json"

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except AssertionError as e:
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}: {e}")
    except Exception as e:
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


# ── 共享批次：3 公理 × 3 种子（构建一次，全部测试复用）────────

BATCH = {}


def ensure_batch():
    if BATCH:
        return BATCH
    tmp = tempfile.mkdtemp(prefix="s22_batch_")
    out_dir = Path(tmp) / "batch"
    seeds = [7, 42, 2077]
    conditions = [
        {"axiom": "A_M1", "seeds": seeds,
         "mock_factory": scenario_factory("A_M1", 10)},
        {"axiom": "A_W1", "seeds": seeds,
         "mock_factory": scenario_factory("A_W1", 10)},
        {"axiom": "A_W2_5", "seeds": seeds,
         "mock_factory": scenario_factory("A_W2_5", 10)},
    ]
    summary = run_formal_batch(
        conditions=conditions, rounds=10, out_dir=str(out_dir),
        config_path=str(CONFIG_PATH), base_port=20870,
        wait_timeout_s=5.0)
    BATCH["dir"] = out_dir
    BATCH["summary"] = summary
    return BATCH


# ── 测试 1：精确重排检验——手算对照 ────────────────────────────


def test_permutation_test_exact_values():
    # n=3 全正且相等：2^3=8 种符号翻转，仅全正/全负达 |观测均值| → 2/8
    r = paired_permutation_test([1.0, 1.0, 1.0], [0.5, 0.5, 0.5])
    assert r["n"] == 3
    assert abs(r["observed_mean_diff"] - 0.5) < 1e-12, r
    assert abs(r["p_value"] - 2.0 / 8.0) < 1e-12, r
    assert r["enumerated"] == 8

    # n=9 全正且相等 → 2/512
    r9 = paired_permutation_test([1.0] * 9, [0.0] * 9)
    assert abs(r9["p_value"] - 2.0 / 512.0) < 1e-12, r9
    assert r9["enumerated"] == 512

    # 差值对称 [1,-1]：观测均值 0，全部翻转都 ≥ |0| → p=1
    r0 = paired_permutation_test([1.0, 0.0], [0.0, 1.0])
    assert abs(r0["p_value"] - 1.0) < 1e-12, r0

    # 完全相同序列 → p=1（无差异）
    rsame = paired_permutation_test([0.3, 0.7], [0.3, 0.7])
    assert abs(rsame["p_value"] - 1.0) < 1e-12, rsame
    assert abs(rsame["observed_mean_diff"]) < 1e-12

    # 空输入 fail-loud；长度不等 fail-loud
    try:
        paired_permutation_test([], [])
        raise AssertionError("empty input must raise")
    except ValueError:
        pass
    try:
        paired_permutation_test([1.0], [0.0, 1.0])
        raise AssertionError("unequal length must raise")
    except ValueError:
        pass
    # n>20：降级为确定性蒙特卡洛（S27 扩样 pooled n=27 支撑）
    mc1 = paired_permutation_test([1.0] * 21, [0.0] * 21)
    assert mc1["approximate"] is True, mc1
    assert mc1["test"] == "mc_paired_permutation_two_sided", mc1
    assert 0.0 < mc1["p_value"] <= 1.0, mc1
    mc2 = paired_permutation_test([1.0] * 21, [0.0] * 21)
    assert mc1["p_value"] == mc2["p_value"], \
        "MC must be deterministic (fixed seed)"


# ── 测试 2：成对 t 检验——纯 Python 实现 + scipy 交叉核对 ──────


def test_paired_t_test_values():
    # 零差异 → p=1
    r0 = paired_t_test([0.5, 0.5], [0.5, 0.5])
    assert abs(r0["p_value"] - 1.0) < 1e-12, r0
    assert r0["t_stat"] == 0.0

    # 零方差非零效应 → p=0（t 无定义，JSON 安全置 None）
    rc = paired_t_test([1.0, 1.0, 1.0], [0.0, 0.0, 0.0])
    assert rc["p_value"] == 0.0, rc
    assert rc["t_stat"] is None, rc
    # S29.15：原断言 `cohen_d is None or is not None` 恒真空洞——
    # 零方差分支 cohen_d 无定义，实现契约就是 None + 注释字段
    assert rc["cohen_d"] is None, rc
    assert rc.get("note") == "zero_variance_nonzero_effect", rc

    # 一般情形：与 scipy.stats.ttest_rel 交叉核对（差值方差非零）
    exp = [1.0, 0.9, 0.95, 1.0, 0.85]
    base = [0.4, 0.5, 0.3, 0.6, 0.45]
    r = paired_t_test(exp, base)
    assert r["dof"] == 4
    assert r["observed_mean_diff"] > 0
    try:
        from scipy import stats as sps
        ref = sps.ttest_rel(exp, base)
        assert abs(r["p_value"] - float(ref.pvalue)) < 1e-9, \
            f"p mismatch: {r['p_value']} vs {ref.pvalue}"
        assert abs(r["t_stat"] - float(ref.statistic)) < 1e-9, r
    except ImportError:
        assert 0.0 <= r["p_value"] <= 1.0


# ── 测试 3：收敛曲线图 + 总汇落盘 ─────────────────────────────


def test_convergence_figures_and_summary():
    b = ensure_batch()
    viz = BatchVisualizer(str(b["dir"]))
    result = viz.render_all()

    figs = result["figures"]
    for axiom in ("A_M1", "A_W1", "A_W2_5"):
        assert f"convergence_{axiom}" in figs, figs.keys()
        svg = Path(figs[f"convergence_{axiom}"])
        assert svg.exists(), svg
        content = svg.read_text(encoding="utf-8")
        assert content.startswith("<svg"), "must be SVG"
        # 3 种子 × (实验 + 对照) = 6 条曲线
        assert content.count("<polyline") >= 6, \
            f"{axiom}: expected >=6 polylines"
    for axiom in ("A_M1", "A_W1", "A_W2_5"):
        assert f"c_h_{axiom}" in figs, figs.keys()
        assert Path(figs[f"c_h_{axiom}"]).exists()

    stats_path = b["dir"] / "stats" / "stats_summary.json"
    assert stats_path.exists(), "stats_summary.json must be written"
    saved = json.loads(stats_path.read_text(encoding="utf-8"))
    for key in ("mode", "figures", "lifespan", "anchors", "metrics",
                "statistical_tests"):
        assert key in saved, f"stats_summary missing {key}"
    assert saved["mode"] == "s22_stats"


# ── 测试 4：虚拟寿命统计 ──────────────────────────────────────


def test_lifespan_statistics():
    b = ensure_batch()
    viz = BatchVisualizer(str(b["dir"]))
    life = viz.lifespan_statistics()
    for axiom in ("A_M1", "A_W1", "A_W2_5"):
        st = life["per_condition"][axiom]
        assert st["values"] == [10, 10, 10], st
        assert st["mean"] == 10 and st["std"] == 0, st
        assert st["min"] == 10 and st["max"] == 10, st
    assert life["overall"]["mean"] == 10, life["overall"]
    assert life["overall"]["n"] == 9, life["overall"]


# ── 测试 5：锚点验证结果表（五锚点机械判定）──────────────────


def test_anchor_validation_table():
    b = ensure_batch()
    viz = BatchVisualizer(str(b["dir"]))
    anchors = viz.anchor_validation_table()
    assert set(anchors.keys()) == {"P_W2_5", "P_M1", "P_W1", "P_C", "H_L"}, \
        anchors.keys()
    for key, entry in anchors.items():
        for field in ("hypothesis", "falsification_criterion",
                      "evidence", "verdict"):
            assert field in entry, f"{key} missing {field}"

    # P_W2_5：实验组恒 ≥0.99 且全对差值非负、重排显著 → supported
    assert anchors["P_W2_5"]["verdict"] == "supported", anchors["P_W2_5"]
    ev = anchors["P_W2_5"]["evidence"]
    assert ev["all_diffs_nonneg"] is True, ev
    assert ev["permutation_p"] <= 0.05, ev

    # P_M1：边界条件单调不减且净增（3预警轮自动补全）→ supported
    assert anchors["P_M1"]["verdict"] == "supported", anchors["P_M1"]

    # P_W1：全部观测经 schema 校验，无脏数据 → supported
    assert anchors["P_W1"]["verdict"] == "supported", anchors["P_W1"]
    assert anchors["P_W1"]["evidence"]["dirty_total"] == 0, anchors["P_W1"]

    # P_C：默认预算无超预算轮 → 无触发实例、无反例
    assert anchors["P_C"]["verdict"] == "not_triggered", anchors["P_C"]

    # H_L：本批无 ①/② 触发 → 未检验
    assert anchors["H_L"]["verdict"] == "not_tested", anchors["H_L"]


# ── 测试 6：量化指标（收敛轮/成本峰值/降级时序）──────────────


def test_quantitative_metrics():
    b = ensure_batch()
    viz = BatchVisualizer(str(b["dir"]))
    metrics = viz.quantitative_metrics()
    for axiom in ("A_M1", "A_W1", "A_W2_5"):
        runs = metrics["per_condition"][axiom]
        assert len(runs) == 3, f"{axiom} must have 3 seed runs"
        for run in runs:
            for field in ("seed", "convergence_round", "max_c_extend",
                          "warn_round", "downgrade_round",
                          "virtual_lifespan", "converged_final",
                          "boundary_final_count"):
                assert field in run, f"{axiom} run missing {field}"
            assert run["max_c_extend"] == 5, run
            assert run["converged_final"] is True, run
            assert run["boundary_final_count"] >= 1, run
            assert run["warn_round"] is not None
            assert run["downgrade_round"] is not None

    # 收敛轮 = 首个连续 window=5 全 ≥τ 的窗口末（0 基）。
    # 离线确定性嵌入路对齐分恒 1.0 → 恒为 4；实机路（7B 隐状态）
    # 该指标才有真实区分度。降级轮为机制时序锚点。
    for axiom in ("A_M1", "A_W1", "A_W2_5"):
        for run in metrics["per_condition"][axiom]:
            assert run["convergence_round"] == 4, run
    for run in metrics["per_condition"]["A_M1"]:
        # R_M1_2 真反例语义（攻击可伤害 NPC 后 HP 未降）：每轮仅记 1 条
        # 反例，且 r0 被标签多样性闸门锁定不计（需 ≥2 组合），r1 起逐轮
        # 计数 → 3 预警 r3、5 降级 r5（3 种子一致，确定性机制时序）。
        assert run["downgrade_round"] == 5, run
    for run in metrics["per_condition"]["A_W1"]:
        assert run["downgrade_round"] == 2, run
    for run in metrics["per_condition"]["A_W2_5"]:
        assert run["downgrade_round"] is not None
        assert run["downgrade_round"] <= 5, run


# ── 测试 7：统计检验汇总（种子级单元 + 双检验）────────────────


def test_statistical_tests_summary():
    b = ensure_batch()
    viz = BatchVisualizer(str(b["dir"]))
    st = viz.statistical_tests()
    for axiom in ("A_M1", "A_W1", "A_W2_5"):
        entry = st["per_condition"][axiom]
        assert entry["n_runs"] == 3, entry
        assert len(entry["exp_means"]) == 3
        assert len(entry["baseline_means"]) == 3
        perm = entry["permutation"]
        assert perm["enumerated"] == 8, perm
        assert 0.0 <= perm["p_value"] <= 1.0
        assert perm["observed_mean_diff"] > 0, perm
        tt = entry["t_test"]
        assert 0.0 <= tt["p_value"] <= 1.0, tt
        assert all(d >= 0 for d in entry["diffs"]), entry["diffs"]
    pooled = st["pooled"]
    assert pooled["permutation"]["n"] == 9
    assert pooled["permutation"]["enumerated"] == 512
    assert pooled["permutation"]["observed_mean_diff"] > 0
    assert pooled["permutation"]["p_value"] <= 2.0 / 512.0 + 1e-12, pooled


if __name__ == "__main__":
    check("permutation_test_exact_values", test_permutation_test_exact_values)
    check("paired_t_test_values", test_paired_t_test_values)
    check("convergence_figures_and_summary", test_convergence_figures_and_summary)
    check("lifespan_statistics", test_lifespan_statistics)
    check("anchor_validation_table", test_anchor_validation_table)
    check("quantitative_metrics", test_quantitative_metrics)
    check("statistical_tests_summary", test_statistical_tests_summary)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
