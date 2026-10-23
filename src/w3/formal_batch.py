"""S21 批量正式实验——3 条公理完整迭代日志 + 收敛曲线 + 对照组对比数据。

批量契约（S21 锁定）：
- 三公理聚焦剧本族：每公理一个种子化世界模式（由 conditions 的
  mock_factory 注入），机械触发目标公理完成整条迭代链（反例累计 →
  3 预警 → 5 降级 → 边界自动补全），其余公理保持 active（迭代特异性，
  每次运行的 other_axiom_status 落档验证）。
- 每公理条件 ≥3 种子（锁定决策 #9）：axiom_coverage 机械统计。
- 三大交付物（每运行全部落盘，S22 可视化与统计直接消费）：
  ① 完整迭代日志：rounds.jsonl 归档 + iteration 提取（final_status /
     warn_round / downgrade_round / max_count / boundary_conditions_final）；
  ② 收敛曲线：convergence_curve（逐轮 alignment_score）+ qs_history；
  ③ 对照组对比数据：S18 回放同一批观测 + compare_reports 成对序列。
- 编排沿用 S19/S20：mock 后台线程先启 connect_with_retry；一键函数
  内部装配与裁决；种子仅落档与驱动世界剧本，不进任何裁决路径。
"""

import json
import logging
import sys
import threading
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from w1.rule_validator import RuleValidator
from w3.auto_experiment import run_auto_experiment
from w3.log_recorder import LogRecorder
from w3.statistical_baseline import StatisticalBaseline, compare_reports

log = logging.getLogger("formal_batch")

DEFAULT_OUT_DIR = _SRC.parent / "logs" / "formal_batch"

_KNOWN_AXIOMS = tuple(a["axiom_id"] for a in RuleValidator().get_axioms())


def extract_axiom_iteration(report, recorder, axiom_id):
    """从报告与归档提取一条公理的完整迭代轨迹。

    Returns:
        {"axiom", "final_status", "warn_round", "downgrade_round",
         "max_count", "boundary_conditions_final"}
    """
    warn_round = None
    downgrade_round = None
    max_count = 0
    for rd in report.get("round_details", []) or []:
        for p in rd.get("counterexample_processing", []) or []:
            if p.get("axiom_id") != axiom_id:
                continue
            if p.get("recorded"):
                max_count = max(max_count, int(p.get("count", 0)))
            action = p.get("action")
            if action == "warn" and warn_round is None:
                warn_round = rd.get("round_id")
            if action == "downgrade" and downgrade_round is None:
                downgrade_round = rd.get("round_id")

    final_status = (report.get("axiom_final_status") or {}).get(axiom_id)
    boundary_final = []
    round_ids = recorder.list_rounds()
    if round_ids:
        last = recorder.load_round(max(round_ids))
        for a in (last.get("w2", {}).get("axioms", []) or []):
            if a.get("id") == axiom_id:
                boundary_final = a.get("boundary_conditions", []) or []
    return {
        "axiom": axiom_id,
        "final_status": final_status,
        "warn_round": warn_round,
        "downgrade_round": downgrade_round,
        "max_count": max_count,
        "boundary_conditions_final": boundary_final,
    }


def _run_one(axiom, seed, port, rounds, config_path, mock_factory, run_dir,
             wait_timeout_s, embedding_fn=None, state_fn=None):
    """单公理单种子运行：一键实验 + 对照组回放 + 对比 + 迭代提取。

    7B 双路（S26-7B 契约）：embedding_fn/state_fn 注入后 g 与 s_t 同
    空间（7B 隐状态 3584 维），对齐分为真实判别分；None 走离线确定
    性路（32 维）。二者必须同源（不能只换其一）。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "rounds.jsonl"
    report_path = run_dir / "report.json"

    mock = mock_factory(seed, port)
    t = threading.Thread(target=mock.connect_with_retry,
                         kwargs={"interval_s": 0.1}, daemon=True)
    t.start()
    try:
        report = run_auto_experiment(
            rounds=rounds, config_path=config_path, port=port,
            log_path=str(log_path), report_path=str(report_path),
            wait_timeout_s=wait_timeout_s, seed=seed,
            embedding_fn=embedding_fn, state_fn=state_fn)
    finally:
        mock.close()

    if report.get("interruption"):
        return {
            "seed": seed, "port": port,
            "log_path": str(log_path), "report_path": str(report_path),
            "interruption": True,
            "interruption_reason": report.get("interruption_reason"),
        }

    # S29.17 P2：预算从配置装配（此前裸构造用默认值，与配置漂移时
    # 对照组回放口径与实验组不一致）
    recorder = LogRecorder.from_config(config_path=config_path,
                                       log_path=log_path)
    baseline = StatisticalBaseline.from_config(
        config_path=config_path, rounds=rounds)
    baseline_report = baseline.run_from_archive(recorder)
    comparison = compare_reports(report, baseline_report)
    (run_dir / "baseline_report.json").write_text(
        json.dumps(baseline_report, ensure_ascii=False, indent=2,
                   default=str), encoding="utf-8")
    (run_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")

    final_status = report.get("axiom_final_status") or {}
    return {
        "seed": seed,
        "port": port,
        "log_path": str(log_path),
        "report_path": str(report_path),
        "baseline_report_path": str(run_dir / "baseline_report.json"),
        "comparison_path": str(run_dir / "comparison.json"),
        "interruption": False,
        "iteration": extract_axiom_iteration(report, recorder, axiom),
        "other_axiom_status": {
            aid: final_status.get(aid)
            for aid in _KNOWN_AXIOMS if aid != axiom
        },
        "convergence_curve": [
            rd.get("alignment_score")
            for rd in report.get("round_details", []) or []
        ],
        "qs_history": report.get("qs_history"),
        "converged_final": report.get("converged_final"),
        "virtual_lifespan": report.get("virtual_lifespan"),
        "disintegrated": report.get("disintegrated"),
        "criterion": report.get("criterion"),
        "comparison": comparison,
    }


def run_formal_batch(conditions, rounds, out_dir=None, config_path=None,
                     base_port=20850, wait_timeout_s=10.0,
                     embedding_fn=None, state_fn=None):
    """按条件矩阵跑批量正式实验，返回总汇（并落盘）。

    Args:
        conditions: [{"axiom": str, "seeds": [int,...],
                      "mock_factory": callable(seed, port)}, ...]
        rounds: 每运行目标轮数。
        out_dir: 产物根目录（公理/种子两级）。
        config_path: 实验配置路径（None = 默认）。
        base_port: 端口游标（顺序执行，每运行递增）。
        wait_timeout_s: 每运行等待游戏连接的超时。
        embedding_fn / state_fn: 7B 双路注入（S26-7B 契约）。
            None = 离线确定性路；注入 make_model_embedding_fn/
            make_model_state_fn(adapter) 后对齐分为真实 7B 判别分。
            二者必须同源（g 与 s_t 同空间），不能只换其一。
    """
    out_dir = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    port_cursor = int(base_port)
    cond_summaries = []

    for cond in conditions:
        axiom = cond["axiom"]
        seeds = list(cond["seeds"])
        factory = cond["mock_factory"]
        runs = []
        for seed in seeds:
            port = port_cursor
            port_cursor += 1
            run_dir = out_dir / axiom / f"seed_{seed}"
            entry = _run_one(axiom, seed, port, rounds, config_path,
                             factory, run_dir, wait_timeout_s,
                             embedding_fn=embedding_fn, state_fn=state_fn)
            log.info("formal run %s/seed=%s done: iteration=%s converged=%s",
                     axiom, seed,
                     (entry.get("iteration") or {}).get("final_status"),
                     entry.get("converged_final"))
            runs.append(entry)
        cond_summaries.append({"axiom": axiom, "seeds": seeds, "runs": runs})

    summary = {
        "mode": "formal_batch",
        "rounds_target": int(rounds),
        "conditions": cond_summaries,
        "axiom_coverage": {c["axiom"]: len(c["seeds"])
                           for c in cond_summaries},
    }
    summary_path = out_dir / "formal_batch_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False,
                                       indent=2, default=str),
                            encoding="utf-8")
    log.info("formal batch summary written: %s", summary_path)
    return summary
