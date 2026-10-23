"""S19 自动实验脚本——一键启动 N 轮闭环实验（全程无人工干预）。

职责边界：
- 本脚本只做「装配 → 等待连接 → 跑闭环 → 报告落盘 → 清理」的编排；
  每轮 Γ脆三判据检查、触发即自动终止、虚拟寿命记录全部由
  ClosedLoopRunner 硬编码完成（锁定决策 #12），本层不重复实现。
- 检查点恢复透传 run(resume=True)（S17 契约）；装配全部 from_config，
  config_path 可指向临时配置副本（S20 多种子批量实验的复用基础）。

退出码语义（CLI main 返回值）：
- 0 = 实验跑完（解体是合法实验结果，不是错误；虚拟寿命已记录）。
- 1 = 装配失败（等待游戏连接超时）或程序性中断（interruption=True）。

无人工干预契约：
- 游戏不可用：不挂起、不抛裸异常——落盘含原因的报告后按退出码 1 返回。
- 报告永远落盘（JSON），meta 记录配置/日志/报告路径，100% 可复现
  （反套壳自查清单 #4）。
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from w0.game_connector import GameConnector
from w0.data_sampler import DataSampler
from w3.action_executor import ActionExecutor
from w3.log_recorder import LogRecorder
from w3.closed_loop_runner import ClosedLoopRunner

log = logging.getLogger("auto_experiment")

DEFAULT_CONFIG_PATH = _SRC.parent / "config" / "experiment_config.json"


def _write_report(report_path, report):
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    log.info("report written: %s", report_path)


def run_auto_experiment(rounds=20, config_path=None, host=None, port=None,
                        log_path=None, report_path=None, resume=False,
                        wait_timeout_s=60.0, seed=None, state_fn=None,
                        embedding_fn=None, llm_assistant=None,
                        action_planner=None, disable_planner=False):
    """一键跑 N 轮闭环实验，返回报告 dict（并落盘）。

    Args:
        rounds: 目标轮数（Γ脆触发会提前自动终止）。
        config_path: 实验配置路径（None = 默认配置）。
        host / port: 覆盖配置中的连接地址（临时实验端口隔离）。
        log_path: JSONL 归档路径（None = 配置 logging.log_dir 默认）。
        report_path: 报告 JSON 落盘路径（None = log_dir 默认）。
        resume: True = 以已归档轮次为检查点跳过（S17）。
        wait_timeout_s: 等待游戏连接的最长秒数，超时优雅退出。
        seed: 重复实验种子标识（S20，锁定决策 #9），仅落档于报告
            meta，不进入任何裁决路径。
        state_fn: 隐状态函数（审计 A3）。None = 默认离线确定性路
            （对齐分恒 1.0 的退化解，仅限机制验证）；正式批次应注入
            make_model_state_fn(adapter) 以获得 7B 隐状态判别力。
        embedding_fn: 目标嵌入函数（审计 A3，7B 双路配套）。
            None = 默认离线确定性嵌入（32 维）；注入
            make_model_embedding_fn(adapter) 后 g 与 s_t 同空间
            （3584 维），二者必须同源（不能只换其一，否则维度
            不匹配被 QSKernel fail-loud 拦截）。
        llm_assistant: LLM 辅助（审计 A1，可选）。None = 零模型离线
            主链；注入 LLMAssistant（可 attach 已加载 ModelAdapter）
            后，轮末生成人类可读 advisory_notes 归档，零决策回流。
    """
    cfg_path = str(config_path) if config_path else str(DEFAULT_CONFIG_PATH)
    if report_path is None:
        cfg_raw = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        log_dir = Path(cfg_raw.get("logging", {}).get("log_dir", "logs"))
        report_path = log_dir / "auto_experiment_report.json"
    report_path = Path(report_path)

    meta = {
        "mode": "auto_experiment",
        "generated_ts": None,
        "config_path": cfg_path,
        "log_path": None,
        "report_path": str(report_path),
        "rounds_target": int(rounds),
        "resume": bool(resume),
        "wait_timeout_s": float(wait_timeout_s),
        "seed": seed,
    }

    connector = GameConnector.from_config(cfg_path)
    if host is not None:
        connector.host = host
    if port is not None:
        connector.port = int(port)

    connector.start()
    try:
        log.info("waiting for game on %s:%s (timeout %.1fs)",
                 connector.host, connector.port, wait_timeout_s)
        if not connector.wait_for_game(timeout=float(wait_timeout_s)):
            report = {
                "interruption": True,
                "interruption_reason": (
                    f"game_unavailable: no game connection within "
                    f"{wait_timeout_s}s on {connector.host}:{connector.port}"),
                "completed": 0,
                "disintegrated": False,
                "criterion": None,
                "virtual_lifespan": 0,
                "round_details": [],
            }
            report["meta"] = {**meta, "generated_ts": time.time()}
            _write_report(report_path, report)
            log.error("game unavailable within %.1fs, exiting gracefully",
                      wait_timeout_s)
            return report

        sampler = DataSampler(connector)
        executor = ActionExecutor.from_config(connector, path=cfg_path)
        recorder = LogRecorder.from_config(config_path=cfg_path,
                                           log_path=log_path)
        meta["log_path"] = str(recorder.log_path)
        runner = ClosedLoopRunner.from_config(
            connector, sampler, executor, recorder,
            config_path=cfg_path, rounds=int(rounds), state_fn=state_fn)
        if embedding_fn is not None:
            runner.embedding_fn = embedding_fn
        if llm_assistant is not None:
            runner.llm_assistant = llm_assistant
        # S29：动作规划器（shadow 旁观/active 自主）。from_config 默认已
        # 装配 config planner；显式传入覆盖（如真实 LLM plan_fn 注入）。
        # disable_planner=True：显式禁用（对照组——纯默认编排）。
        if disable_planner:
            runner.action_planner = None
        elif action_planner is not None:
            runner.action_planner = action_planner

        report = runner.run(resume=bool(resume))
        report["meta"] = {**meta, "generated_ts": time.time()}
        _write_report(report_path, report)
        log.info("auto experiment finished: completed=%s disintegrated=%s "
                 "criterion=%s virtual_lifespan=%s",
                 report.get("completed"), report.get("disintegrated"),
                 report.get("criterion"), report.get("virtual_lifespan"))
        return report
    finally:
        connector.stop()


# ── CLI ────────────────────────────────────────────────────


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="CUF-QS 一键自动实验（S19）：N 轮闭环 + Γ脆自动终止 + 虚拟寿命记录")
    p.add_argument("--rounds", type=int, default=20,
                   help="目标轮数（解体触发会提前终止）")
    p.add_argument("--config", default=None, help="实验配置路径")
    p.add_argument("--host", default=None, help="覆盖配置连接主机")
    p.add_argument("--port", type=int, default=None, help="覆盖配置连接端口")
    p.add_argument("--log-path", dest="log_path", default=None,
                   help="JSONL 归档路径")
    p.add_argument("--report-path", dest="report_path", default=None,
                   help="报告 JSON 落盘路径")
    p.add_argument("--resume", action="store_true",
                   help="以已归档轮次为检查点跳过（S17）")
    p.add_argument("--wait-timeout", dest="wait_timeout", type=float,
                   default=60.0, help="等待游戏连接的最长秒数")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    report = run_auto_experiment(
        rounds=args.rounds, config_path=args.config, host=args.host,
        port=args.port, log_path=args.log_path,
        report_path=args.report_path, resume=args.resume,
        wait_timeout_s=args.wait_timeout)
    # S29.17 F15：system_dead（游戏长期不可用、连续跳轮）不得静默
    # "成功完成"——退出码非零并显式留痕，防止掩盖实验事实上的死亡。
    rc = 1 if (report.get("interruption") or report.get("system_dead")) else 0
    if report.get("system_dead"):
        log.warning("SYSTEM_DEAD: consecutive round skips >= threshold "
                    "(game unavailable)——实验未完成，退出码置 1")
    summary = {
        "completed": report.get("completed"),
        "disintegrated": report.get("disintegrated"),
        "criterion": report.get("criterion"),
        "virtual_lifespan": report.get("virtual_lifespan"),
        "converged_final": report.get("converged_final"),
        "interruption": report.get("interruption"),
        "interruption_reason": report.get("interruption_reason"),
        "system_dead": report.get("system_dead"),
        "report_path": (report.get("meta") or {}).get("report_path"),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
