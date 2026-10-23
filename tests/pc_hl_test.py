"""P_C 压力批次 + H_L 递进动力学链检验（S29.11 收口，框架主线补充）。

背景（对齐主题文件）：P_C（C_H 超预算 → Γ脆 解体）与 H_L（递进链 ③→②→①）
是框架理论核心但 S23 未实证——根因：C_extend = min(累计反例, 降级阈值=5)
封顶 5 < extend_budget=10，恒不超支。本脚本打破该封顶。

批次设计：
- P_C 压力批次：extend_budget ∈ {10(对照), 4, 3, 2} × 3 公理 × 3 种子 = 36 运行
  离线路（无 7B）。预期 C_extend 达 4/5 > budget → within_budget=False →
  判据③ over_budget 触发 → 解体（虚拟寿命缩短）。判定：P_C supported。
  附带：这些批次 H_L evaluated=False（③先于①②，不构成 H_L 检验）。
- H_L 场景 A（功能死先触发）：state_fn 注入负 target_g（cos=-1 < τ=0.6）→
  连续 3 轮 < τ → functional_death 触发（C_extend 正常积累 < budget）→
  回看前 N=5 轮 C_extend 是否从未达 budget → H_L violated（递进链不成立）。
  3 公理 × 3 种子 = 9 运行。

用法：py -3.12 tests/pc_hl_test.py（无需 GPU、无需游戏在线，mock 驱动）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w3.formal_batch import run_formal_batch
from s21_selftest import scenario_factory

ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "config" / "experiment_config.json"
OUT_DIR = ROOT / "logs" / "pc_hl"
SEEDS = [7, 42, 2077]
BASE_PORT = 21050
ROUNDS = 10
TAU = 0.6  # 与 config 一致（S27 重标定）


def make_config(extend_budget, note_suffix=""):
    """生成临时 config 副本（改 extend_budget），返回路径。"""
    cfg = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    c_h = cfg.setdefault("c_h", {})
    c_h["extend_budget"] = int(extend_budget)
    c_h["_extend_budget_note"] = (
        f"P_C 压力批次注入（原 10）：extend_budget={extend_budget}。"
        f"C_extend = min(累计反例, 降级阈值 5)；当 budget<5 时超支可触发 "
        f"Γ脆 判据③（over_budget）。{note_suffix}")
    fd, path = tempfile.mkstemp(suffix=".json", prefix=f"pc_hl_b{extend_budget}_")
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def low_alignment_state_fn(round_index, ctx):
    """H_L 场景 A：返回 target_g 的负向量 → cos(g, s_t) = -1 < τ，
    稳定触发功能死（连续 K=3 轮 < τ）。C_extend 不受影响（正常积累）。"""
    g = list(ctx.get("target_g") or [])
    return [-x for x in g] if g else None


def run_pc_pressure():
    print("===== P_C 压力批次（extend_budget 扫描） =====")
    budgets = [10, 4, 3, 2]
    all_entries = []
    for budget in budgets:
        cfg_path = make_config(budget)
        try:
            conditions = [
                {"axiom": "A_M1", "seeds": SEEDS,
                 "mock_factory": scenario_factory("A_M1", ROUNDS)},
                {"axiom": "A_W1", "seeds": SEEDS,
                 "mock_factory": scenario_factory("A_W1", ROUNDS)},
                {"axiom": "A_W2_5", "seeds": SEEDS,
                 "mock_factory": scenario_factory("A_W2_5", ROUNDS)},
            ]
            summary = run_formal_batch(
                conditions, rounds=ROUNDS,
                out_dir=str(OUT_DIR / f"pc_b{budget}"),
                config_path=cfg_path, base_port=BASE_PORT + budget * 100,
                wait_timeout_s=5.0)
            for cond in summary["conditions"]:
                for r in cond["runs"]:
                    all_entries.append({
                        "budget": budget, "axiom": cond["axiom"],
                        "seed": r["seed"], "criterion": r["criterion"],
                        "disintegrated": r["disintegrated"],
                        "virtual_lifespan": r["virtual_lifespan"],
                        "h_l": None})
                    print(f"  b={budget} {cond['axiom']}/s{r['seed']} "
                          f"criterion={r['criterion']} "
                          f"lifespan={r['virtual_lifespan']}")
        finally:
            Path(cfg_path).unlink(missing_ok=True)

    # P_C 判定：budget<5 档应出现 over_budget（超支即解体）
    triggered = [e for e in all_entries
                 if e["criterion"] == "over_budget"]
    low_budget = [e for e in all_entries if e["budget"] < 10]
    verdict = {
        "supported": len(triggered) >= 1 and len(low_budget) > 0,
        "over_budget_runs": len(triggered),
        "low_budget_runs": len(low_budget),
        "control_criterion": sorted({
            e["criterion"] for e in all_entries if e["budget"] == 10}),
    }
    print(f"[P_C] over_budget 触发 {len(triggered)}/{len(low_budget)} "
          f"低预算档；对照档 criterion={verdict['control_criterion']}")
    return verdict, all_entries


def run_hl_scenario_a():
    print("===== H_L 场景 A（功能死先触发，无超支） =====")
    cfg_path = make_config(10, "H_L 场景 A：保持 extend_budget=10 保证 C_extend 不超支")
    entries = []
    try:
        conditions = [
            {"axiom": "A_M1", "seeds": SEEDS,
             "mock_factory": scenario_factory("A_M1", ROUNDS)},
            {"axiom": "A_W1", "seeds": SEEDS,
             "mock_factory": scenario_factory("A_W1", ROUNDS)},
            {"axiom": "A_W2_5", "seeds": SEEDS,
             "mock_factory": scenario_factory("A_W2_5", ROUNDS)},
        ]
        summary = run_formal_batch(
            conditions, rounds=ROUNDS,
            out_dir=str(OUT_DIR / "hl_a"),
            config_path=cfg_path, base_port=BASE_PORT + 900,
            wait_timeout_s=5.0,
            state_fn=low_alignment_state_fn)
        for cond in summary["conditions"]:
            for r in cond["runs"]:
                entries.append({
                    "axiom": cond["axiom"], "seed": r["seed"],
                    "criterion": r["criterion"],
                    "virtual_lifespan": r["virtual_lifespan"],
                })
                print(f"  {cond['axiom']}/s{r['seed']} "
                      f"criterion={r['criterion']} lifespan={r['virtual_lifespan']}")
    finally:
        Path(cfg_path).unlink(missing_ok=True)
    return entries


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pc_verdict, pc_entries = run_pc_pressure()
    hl_entries = run_hl_scenario_a()

    # H_L 判定：functional_death 触发批次，回看触发前 C_extend 是否达 budget
    # （report 的 h_l 字段由 closed_loop_runner._evaluate_h_l 计算）
    print("===== 汇总 =====")
    hl_fd = [e for e in hl_entries if e["criterion"] == "functional_death"]
    summary = {
        "pc": pc_verdict,
        "hl_functional_death_runs": len(hl_fd),
        "hl_entries": hl_entries,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    (OUT_DIR / "pc_hl_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] summary -> {OUT_DIR / 'pc_hl_summary.json'}")


if __name__ == "__main__":
    main()
