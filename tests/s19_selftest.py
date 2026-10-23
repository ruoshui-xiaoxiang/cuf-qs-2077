"""S19 自动实验脚本离线自测（RED→GREEN 契约）。

一键脚本契约（S19 锁定）：
- run_auto_experiment 全流程无人工干预：装配（全部 from_config）→
  等待游戏连接（wait_timeout_s 超时即优雅落盘退出）→ N 轮闭环
  （Γ脆三判据每轮检查已在 ClosedLoopRunner 内，触发即自动终止）→
  记录虚拟寿命 → 报告落盘（JSON）→ 连接器清理。
- 退出码语义（CLI main 返回值）：0 = 实验跑完（解体是合法实验结果，
  不是错误）；1 = 装配失败（游戏不可用）或程序性中断。
- 检查点：resume=True 透传给 ClosedLoopRunner.run（S17 契约）。
- 临时配置覆盖：config_path 可指向修改过的配置副本（如
  extend_budget=2），一键装配全链路按该配置执行——S20 多种子
  批量实验的复用基础。
"""
import json
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tempfile

from w3.auto_experiment import (
    run_auto_experiment,
    main as auto_main,
    parse_args,
)
from mock_game_server import MockGameServer

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


# ── mock 游戏侧（与 s16 同构的僵尸剧本）──────────────────────


class LoopMock(MockGameServer):
    """僵尸剧本 mock：attack 生成 hp_changed（避免 combat_attack 误触发 A_M1）。"""

    def __init__(self, port, zombies=True):
        super().__init__(port=port, initial_scenario="empty")
        self.zombies = zombies
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._events_queue = []
        self._next_evt = 0
        self.handlers["scan_entities"] = self._h_scan
        self.handlers["get_events"] = self._h_get_events
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move

    def _h_scan(self, args):
        entities = [{"entity_id": "EID:1", "kind": "player",
                     "labels": ["player"], "hp": 100,
                     "position": dict(self.player_pos)}]
        if self.zombies:
            entities += [
                {"entity_id": "EID:Z1", "kind": "npc",
                 "labels": ["undead", "hostile"], "hp": 0,
                 "position": {"x": 5.0, "y": 0.0, "z": 0.0}},
                {"entity_id": "EID:Z2", "kind": "npc",
                 "labels": ["undead", "neutral"], "hp": 0,
                 "position": {"x": 8.0, "y": 0.0, "z": 0.0}},
            ]
        else:
            entities += [
                {"entity_id": "EID:N1", "kind": "npc",
                 "labels": ["civilian"], "hp": 100,
                 "position": {"x": 5.0, "y": 0.0, "z": 0.0}},
            ]
        return {"count": len(entities), "entities": entities,
                "player": entities[0]}

    def _h_get_events(self, args):
        evts = list(self._events_queue)
        self._events_queue.clear()
        return {"events": evts}

    def _h_attack(self, args):
        eid = args.get("target_id", "")
        self._next_evt += 1
        self._events_queue.append({
            "event_id": f"evt_{self._next_evt}",
            "kind": "hp_changed",
            "source_entity": "EID:1",
            "target_entity": eid,
            "game_ts": int(time.time() * 1000),
        })
        return {"action_id": f"act_{self._next_evt}", "accepted": True,
                "reason": "damage_dispatched"}

    def _h_move(self, args):
        self.player_pos = {"x": float(args.get("x", 0)),
                           "y": float(args.get("y", 0)),
                           "z": float(args.get("z", 0))}
        return {"action_id": "act_move", "accepted": True,
                "reason": "teleport_dispatched"}


def start_mock_bg(port):
    """后台线程启动 mock 连接重试（一键脚本内部才起 connector）。"""
    mock = LoopMock(port=port, zombies=True)
    t = threading.Thread(target=mock.connect_with_retry,
                         kwargs={"interval_s": 0.1}, daemon=True)
    t.start()
    return mock, t


# ── 测试 1：一键全流程（僵尸剧本 8 轮）+ 报告可追溯 ───────────


def test_one_click_full_run_and_traceability():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "rounds.jsonl"
        report_path = Path(d) / "report.json"
        mock, t = start_mock_bg(20820)
        try:
            report = run_auto_experiment(
                rounds=8, config_path=str(CONFIG_PATH), port=20820,
                log_path=str(log_path), report_path=str(report_path),
                wait_timeout_s=5.0)

            assert report["completed"] == 8, report
            assert report["disintegrated"] is False, report
            assert report["criterion"] is None
            assert report["virtual_lifespan"] == 8
            assert report["interruption"] is False, report
            assert report["axiom_final_status"]["A_W2_5"] == "deprecated", \
                report["axiom_final_status"]

            # —— Γ脆判定与虚拟寿命字段齐全（锁定决策 #12）——
            for key in ("disintegrated", "criterion", "virtual_lifespan",
                        "h_l", "c_extend_series", "round_details",
                        "downgrade_events", "qs_history", "converged_final"):
                assert key in report, f"report missing {key}"
            rds = report["round_details"]
            assert len(rds) == 8
            for rd in rds:
                assert "round_id" in rd and "alignment_score" in rd, rd

            # —— 报告落盘 + meta 可追溯 ——
            assert report_path.exists(), "report file must be written"
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            assert saved["completed"] == 8
            meta = saved.get("meta", {})
            assert meta.get("mode") == "auto_experiment", meta
            assert meta.get("log_path") == str(log_path), meta
            assert meta.get("report_path") == str(report_path), meta
            assert meta.get("rounds_target") == 8, meta
            assert meta.get("resume") is False, meta

            # —— 归档轮数 == 完成轮数（反套壳 #4 可复现）——
            lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
            assert len(lines) == 8, f"archive lines {len(lines)} != 8"
            assert report["c_extend_series"] == [1, 3, 5, 5, 5, 5, 5, 5], \
                report["c_extend_series"]
        finally:
            mock.close()


# ── 测试 2：临时配置驱动——超预算自动终止 + 虚拟寿命 ───────────


def test_auto_stop_over_budget_via_temp_config():
    with tempfile.TemporaryDirectory() as d:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg["c_h"]["extend_budget"] = 2
        temp_cfg = Path(d) / "config_eb2.json"
        temp_cfg.write_text(json.dumps(cfg), encoding="utf-8")

        log_path = Path(d) / "rounds.jsonl"
        report_path = Path(d) / "report.json"
        mock, t = start_mock_bg(20821)
        try:
            report = run_auto_experiment(
                rounds=8, config_path=str(temp_cfg), port=20821,
                log_path=str(log_path), report_path=str(report_path),
                wait_timeout_s=5.0)
            assert report["disintegrated"] is True, report
            assert report["criterion"] == "over_budget", report["criterion"]
            assert report["completed"] == 2, report
            assert report["virtual_lifespan"] == 2, report
            lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
            assert len(lines) == 2, f"archive must stop at disintegration, got {len(lines)}"
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            assert saved["criterion"] == "over_budget"
            assert saved["virtual_lifespan"] == 2
        finally:
            mock.close()


# ── 测试 3：无游戏——优雅落盘退出，不挂起不抛异常 ──────────────


def test_no_game_graceful_exit():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "rounds.jsonl"
        report_path = Path(d) / "report.json"
        start = time.time()
        report = run_auto_experiment(
            rounds=8, config_path=str(CONFIG_PATH), port=20822,
            log_path=str(log_path), report_path=str(report_path),
            wait_timeout_s=0.5)
        elapsed = time.time() - start
        assert elapsed < 5.0, f"must not hang: {elapsed}s"
        assert report["interruption"] is True, report
        assert "game_unavailable" in report["interruption_reason"], report
        assert report["completed"] == 0, report
        assert report["virtual_lifespan"] == 0
        assert report["disintegrated"] is False
        # 报告仍落盘（无人工干预也要留痕）
        saved = json.loads(report_path.read_text(encoding="utf-8"))
        assert saved["interruption"] is True
        # CLI 退出码 = 1（装配失败）
        rc = auto_main(["--rounds", "2", "--config", str(CONFIG_PATH),
                        "--port", "20823",
                        "--log-path", str(log_path),
                        "--report-path", str(report_path),
                        "--wait-timeout", "0.3"])
        assert rc == 1, f"no-game exit code must be 1, got {rc}"


# ── 测试 4：一键检查点恢复（resume 透传 S17 契约）─────────────


def test_resume_one_click():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "rounds.jsonl"
        report_path = Path(d) / "report.json"
        mock1, t1 = start_mock_bg(20824)
        try:
            r1 = run_auto_experiment(
                rounds=4, config_path=str(CONFIG_PATH), port=20824,
                log_path=str(log_path), report_path=str(report_path),
                wait_timeout_s=5.0)
            assert r1["completed"] == 4, r1
        finally:
            mock1.close()

        mock2, t2 = start_mock_bg(20824)
        try:
            r2 = run_auto_experiment(
                rounds=8, config_path=str(CONFIG_PATH), port=20824,
                log_path=str(log_path), report_path=str(report_path),
                resume=True, wait_timeout_s=5.0)
            assert r2["completed"] == 4, \
                f"resume must only run rounds 4-7, got {r2['completed']}"
            assert r2["resume"]["enabled"] is True, r2["resume"]
            assert r2["resume"]["skipped_archived"] == [0, 1, 2, 3], \
                r2["resume"]
            ids = [rd["round_id"] for rd in r2["round_details"]]
            assert ids == [4, 5, 6, 7], ids
            lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines()
                     if ln.strip()]
            assert len(lines) == 8, f"total archive must be 8, got {len(lines)}"
        finally:
            mock2.close()


# ── 测试 5：CLI 参数解析默认值 ────────────────────────────────


def test_cli_parse_defaults():
    args = parse_args([])
    assert args.rounds == 20, args
    assert args.resume is False, args
    assert args.wait_timeout == 60.0, args
    assert args.config is None, args
    assert args.log_path is None, args
    assert args.report_path is None, args
    assert args.host is None and args.port is None, args
    args2 = parse_args(["--rounds", "5", "--resume", "--port", "20999",
                        "--wait-timeout", "3.5"])
    assert args2.rounds == 5 and args2.resume is True
    assert args2.port == 20999 and args2.wait_timeout == 3.5


# ── 测试 6：CLI 成功路径退出码 0（解体/正常完成都算跑完）────────


def test_cli_success_exit_code():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "rounds.jsonl"
        report_path = Path(d) / "report.json"
        mock, t = start_mock_bg(20825)
        try:
            rc = auto_main(["--rounds", "3", "--config", str(CONFIG_PATH),
                            "--port", "20825",
                            "--log-path", str(log_path),
                            "--report-path", str(report_path),
                            "--wait-timeout", "5"])
            assert rc == 0, f"success exit code must be 0, got {rc}"
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            assert saved["completed"] == 3, saved
        finally:
            mock.close()


# ── 测试 7：反套壳——一键脚本零模型依赖 ────────────────────────


def test_no_model_imports():
    src = (Path(__file__).resolve().parents[1] / "src" / "w3"
           / "auto_experiment.py").read_text(encoding="utf-8")
    imports = [ln.strip() for ln in src.splitlines()
               if re.match(r"^\s*(import|from)\s", ln)]
    banned = ("torch", "transformers", "model_adapter", "llm_assistant",
              "bitsandbytes")
    for ln in imports:
        for word in banned:
            assert word not in ln, \
                f"auto_experiment must not import {word}: {ln}"


if __name__ == "__main__":
    check("one_click_full_run_and_traceability",
          test_one_click_full_run_and_traceability)
    check("auto_stop_over_budget_via_temp_config",
          test_auto_stop_over_budget_via_temp_config)
    check("no_game_graceful_exit", test_no_game_graceful_exit)
    check("resume_one_click", test_resume_one_click)
    check("cli_parse_defaults", test_cli_parse_defaults)
    check("cli_success_exit_code", test_cli_success_exit_code)
    check("no_model_imports", test_no_model_imports)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
