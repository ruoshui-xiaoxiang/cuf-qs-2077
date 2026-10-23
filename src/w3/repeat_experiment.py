"""S20 重复实验编排——每条公理条件 ≥3 个独立随机种子（锁定决策 #9）。

种子化契约（S20 锁定）：
- 管线本身零随机源（src 全程无 random 引用，纯确定性）。「独立随机
  种子」操作化为种子化世界生成器：mock_factory(seed, port) 注入以
  种子驱动的逐轮世界剧本变异（异常出现时机/数量/标签组合）；裁决
  链（W1 真值 / 降级 / QS / Γ脆）与种子完全无关。
- 每条公理条件 ≥3 个独立种子：每次 repeat 全量注入 3 条公理，种子
  随机化世界轨迹；seeds ≥ 3 即满足决策 #9。
- 复现性口径：同种子两次运行 → trajectory_signature 完全一致；
  不同种子 → 签名互异。签名 = 种子决定的轨迹核心：
  c_extend_series / qs_history / axiom_final_status / converged_final /
  virtual_lifespan / disintegrated / criterion / 对齐分序列 / 动作序列。
- 每种子产物四件套（独立目录）：rounds.jsonl（实验组归档）、
  report.json（实验组报告）、baseline_report.json（S18 对照组回放
  同一批观测）、comparison.json（compare_reports 成对序列）；
  总汇 repeat_summary.json 含 trend（收敛趋势 + 虚拟寿命 + 解体分布）。
- 编排沿用 S19 工程事实：mock 在后台线程先启 connect_with_retry，
  一键函数内部才 start connector。
"""

import json
import logging
import sys
import threading
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from w3.auto_experiment import run_auto_experiment
from w3.log_recorder import LogRecorder
from w3.statistical_baseline import StatisticalBaseline, compare_reports

log = logging.getLogger("repeat_experiment")

DEFAULT_OUT_DIR = _SRC.parent / "logs" / "repeat"

_SIGNATURE_KEYS = (
    "c_extend_series", "qs_history", "axiom_final_status",
    "converged_final", "virtual_lifespan", "disintegrated", "criterion",
)


def trajectory_signature(report):
    """提取种子决定的轨迹核心（剔除时间戳/路径等非轨迹字段）。"""
    sig = {k: report.get(k) for k in _SIGNATURE_KEYS}
    rds = report.get("round_details", []) or []
    sig["alignment_series"] = [rd.get("alignment_score") for rd in rds]
    sig["action_series"] = [rd.get("action") for rd in rds]
    return sig


def _run_baseline_for_seed(report, log_path, config_path, rounds):
    """S18 对照组回放该种子实验组归档（同一批观测），返回报告与对比。"""
    recorder = LogRecorder(log_path)
    baseline = StatisticalBaseline.from_config(
        config_path=config_path, rounds=rounds)
    baseline_report = baseline.run_from_archive(recorder)
    comparison = compare_reports(report, baseline_report)
    return baseline_report, comparison


def repeat_experiment(seeds, rounds, mock_factory, config_path=None,
                      out_dir=None, base_port=20830, wait_timeout_s=10.0,
                      run_baseline=True):
    """按种子列表逐个跑一键实验 + 对照组回放，返回总汇（并落盘）。

    Args:
        seeds: 种子列表（≥3 满足锁定决策 #9）。
        rounds: 每种子目标轮数。
        mock_factory: (seed, port) -> mock 游戏侧对象（须有
            connect_with_retry 与 close；种子化世界剧本由工厂注入）。
        config_path: 实验配置路径（None = 默认）。
        out_dir: 产物根目录（每种子一个子目录）。
        base_port: 第 i 个种子使用 base_port + i（顺序执行，端口复用安全）。
        wait_timeout_s: 每种子等待游戏连接的超时。
        run_baseline: True = 每种子附带 S18 对照组回放与对比。
    """
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = []

    for idx, seed in enumerate(seeds):
        port = int(base_port) + idx
        seed_dir = out_dir / f"seed_{seed}"
        seed_dir.mkdir(parents=True, exist_ok=True)
        log_path = seed_dir / "rounds.jsonl"
        report_path = seed_dir / "report.json"

        mock = mock_factory(seed, port)
        t = threading.Thread(target=mock.connect_with_retry,
                             kwargs={"interval_s": 0.1}, daemon=True)
        t.start()
        try:
            report = run_auto_experiment(
                rounds=rounds, config_path=config_path, port=port,
                log_path=str(log_path), report_path=str(report_path),
                wait_timeout_s=wait_timeout_s, seed=seed)
        finally:
            mock.close()

        run_entry = {
            "seed": seed,
            "port": port,
            "log_path": str(log_path),
            "report_path": str(report_path),
            "interruption": bool(report.get("interruption")),
            "exp_converged_final": report.get("converged_final"),
            "exp_disintegrated": report.get("disintegrated"),
            "exp_criterion": report.get("criterion"),
            "virtual_lifespan": report.get("virtual_lifespan"),
            "trajectory": trajectory_signature(report),
            "baseline_report_path": None,
            "comparison_path": None,
            "baseline_converged_final": None,
            "exp_mean": None,
            "baseline_mean": None,
        }

        if run_baseline and not report.get("interruption"):
            baseline_report, comparison = _run_baseline_for_seed(
                report, log_path, config_path, rounds)
            b_path = seed_dir / "baseline_report.json"
            c_path = seed_dir / "comparison.json"
            b_path.write_text(json.dumps(baseline_report, ensure_ascii=False,
                                         indent=2, default=str),
                              encoding="utf-8")
            c_path.write_text(json.dumps(comparison, ensure_ascii=False,
                                         indent=2, default=str),
                              encoding="utf-8")
            run_entry["baseline_report_path"] = str(b_path)
            run_entry["comparison_path"] = str(c_path)
            run_entry["baseline_converged_final"] = \
                baseline_report.get("converged_final")
            run_entry["exp_mean"] = comparison.get("exp_mean")
            run_entry["baseline_mean"] = comparison.get("baseline_mean")

        log.info("seed %s done: converged=%s lifespan=%s baseline_mean=%s",
                 seed, run_entry["exp_converged_final"],
                 run_entry["virtual_lifespan"], run_entry["baseline_mean"])
        runs.append(run_entry)

    paired = [r for r in runs
              if r["exp_mean"] is not None and r["baseline_mean"] is not None]
    trend = {
        "seeds_count": len(seeds),
        "exp_converged_count": sum(
            1 for r in runs if r["exp_converged_final"] is True),
        "exp_converged_all": all(
            r["exp_converged_final"] is True for r in runs) if runs else False,
        "baseline_mean_le_exp_all": all(
            r["baseline_mean"] <= r["exp_mean"] for r in paired) if paired else False,
        "disintegrations": {str(r["seed"]): r["exp_criterion"] for r in runs},
        "virtual_lifespans": {str(r["seed"]): r["virtual_lifespan"]
                              for r in runs},
    }
    summary = {
        "mode": "repeat_experiment",
        "rounds_target": int(rounds),
        "seeds": list(seeds),
        "runs": runs,
        "trend": trend,
    }
    summary_path = out_dir / "repeat_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False,
                                       indent=2, default=str),
                            encoding="utf-8")
    log.info("repeat summary written: %s", summary_path)
    return summary
