"""S7 稳定性联调离线自测。

用 mock_game_server 模拟游戏侧，跑 20 轮采集→动作→归档闭环。
验收：无中断、脏数据占比 <1%、日志可复现。
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w0.game_connector import GameConnector
from w0.data_sampler import DataSampler
from w3.action_executor import ActionExecutor
from w3.log_recorder import LogRecorder
from w3.stability_runner import StabilityRunner, DIRTY_RATIO_THRESHOLD
from mock_game_server import MockGameServer

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


class StableMock(MockGameServer):
    """mock that simulates stable game with NPCs and player."""
    def __init__(self, port):
        super().__init__(port=port, initial_scenario="normal")
        self.player_pos = {"x": 10.0, "y": 20.0, "z": 5.0}
        self.npcs = [
            {"entity_id": "EID:100", "kind": "npc", "labels": ["enemy"],
             "hp": 100.0, "position": {"x": 15.0, "y": 20.0, "z": 5.0}},
            {"entity_id": "EID:101", "kind": "npc", "labels": ["enemy"],
             "hp": 80.0, "position": {"x": 12.0, "y": 25.0, "z": 5.0}},
        ]
        self._events_queue = []
        self._next_evt = 0
        self._move_accepted = False
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move

    def _h_attack(self, args):
        eid = args.get("target_id", "")
        # generate a hp_changed event as evidence
        self._next_evt += 1
        self._events_queue.append({
            "event_id": f"evt_{self._next_evt}",
            "kind": "hp_changed",
            "source_entity": "EID:0",
            "target_entity": eid,
            "game_ts": int(time.time() * 1000),
        })
        return {"action_id": f"act_{self._next_evt}", "accepted": True,
                "reason": "damage_dispatched"}

    def _h_move(self, args):
        self.player_pos = {"x": float(args.get("x", 0)), "y": float(args.get("y", 0)),
                           "z": float(args.get("z", 0))}
        self._move_accepted = True
        return {"action_id": "act_move", "accepted": True,
                "reason": "teleport_dispatched"}

    def _h_scan(self, args):
        entities = [{"entity_id": "EID:1", "kind": "player", "labels": ["player"],
                      "hp": 100, "position": self.player_pos}]
        entities.extend(self.npcs)
        return {"count": len(entities), "entities": entities,
                "player": entities[0], "npc_count": len(self.npcs)}

    def _h_get_events(self, args):
        evts = list(self._events_queue)
        self._events_queue.clear()
        return {"events": evts}


def setup_env(port, log_path):
    """Start connector + mock, return (connector, mock, sampler, executor, recorder)."""
    connector = GameConnector(port=port)
    connector.start()
    mock = StableMock(port=port)
    mock.connect_with_retry()
    # wire mock's scan/events to override the inherited handlers
    mock.handlers["scan_entities"] = mock._h_scan
    mock.handlers["get_events"] = mock._h_get_events
    time.sleep(0.5)
    if not connector.wait_for_game(timeout=5.0):
        raise RuntimeError("mock did not connect")
    sampler = DataSampler(connector)
    executor = ActionExecutor.from_config(connector, path=None)
    recorder = LogRecorder(log_path, budget_b=100.0)
    return connector, mock, sampler, executor, recorder


def test_20_rounds_no_interruption():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20780, log_path)
        try:
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=20)
            report = runner.run()
            assert report["interruption"] is False, f"interrupted: {report['interruption_reason']}"
            assert report["completed"] == 20, f"only {report['completed']}/20 completed"
            assert report["failed"] == 0, f"{report['failed']} failures"
        finally:
            connector.stop()
            mock.close()


def test_dirty_ratio_below_threshold():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20781, log_path)
        try:
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=20)
            report = runner.run()
            assert report["dirty_ratio"] < DIRTY_RATIO_THRESHOLD, \
                f"dirty_ratio={report['dirty_ratio']:.4f} >= {DIRTY_RATIO_THRESHOLD}"
        finally:
            connector.stop()
            mock.close()


def test_log_replay_all_rounds():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20782, log_path)
        try:
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=20)
            runner.run()
            # 验收：所有轮次可复现
            all_rounds = recorder.list_rounds()
            assert len(all_rounds) == 20, f"only {len(all_rounds)} rounds in log"
            for rid in all_rounds:
                entry = recorder.load_round(rid)
                assert entry is not None, f"cannot load {rid}"
                assert "w0" in entry
                assert "c_h" in entry
                assert "total" in entry["c_h"]
        finally:
            connector.stop()
            mock.close()


def test_actions_executed():
    """Verify some rounds had attack/move actions."""
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20783, log_path)
        try:
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=20)
            report = runner.run()
            action_rounds = [d for d in report["round_details"] if d.get("action")]
            # index 1 (attack), 2 (move), 5 (attack), 6 (move), 9, 10, 13, 14, 17, 18
            assert len(action_rounds) >= 4, f"only {len(action_rounds)} action rounds"
            # check at least one attack and one move
            attacks = [d for d in action_rounds if d["action"].startswith("attack")]
            moves = [d for d in action_rounds if d["action"].startswith("move")]
            assert len(attacks) >= 1, "no attack rounds"
            assert len(moves) >= 1, "no move rounds"
        finally:
            connector.stop()
            mock.close()


def test_c_h_recorded_each_round():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20784, log_path)
        try:
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=20)
            runner.run()
            for rid in recorder.list_rounds():
                entry = recorder.load_round(rid)
                c = entry["c_h"]
                assert "struct" in c
                assert "adapt" in c
                assert "extend" in c
                assert "total" in c
                assert "within_budget" in c
        finally:
            connector.stop()
            mock.close()


def test_report_passed_flag():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20785, log_path)
        try:
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=20)
            report = runner.run()
            assert report["passed"] is True, \
                f"report not passed: interruption={report['interruption']}, " \
                f"failed={report['failed']}, dirty_ratio={report['dirty_ratio']}"
            assert report["action_completed_confirmed"] >= 1, report
            assert report["action_completed_unconfirmed"] == 0, report
        finally:
            connector.stop()
            mock.close()


def test_action_status_counted():
    """审计修复：动作成败必须计数，且归档需带上动作语义字段。"""
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20787, log_path)
        try:
            def move_reject(args):
                return {"action_id": "act_rej", "accepted": False, "reason": "nav_blocked"}
            mock.handlers["player_move"] = move_reject
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=8)
            report = runner.run()
            assert report["action_completed"] >= 1, report
            assert report["action_failed"] >= 1, report
            assert report["action_completed_confirmed"] >= 1, report
            assert report["action_rejected_or_failed"] >= 1, report
            statuses = [x.get("action_status") for x in report["round_details"] if x.get("action")]
            assert "completed" in statuses and "rejected" in statuses, statuses
            for rid in recorder.list_rounds():
                entry = recorder.load_round(rid)
                w3 = entry.get("w3", {})
                if not w3.get("action"):
                    continue
                for key in ("action_status", "action_outcome_semantics",
                            "action_confirmation_status",
                            "action_receipt_reason"):
                    assert key in w3, (rid, w3)
        finally:
            connector.stop()
            mock.close()


def test_stashed_events_merged():
    """审计修复：攻击观测窗口内消费的非匹配事件须暂存并合并回 W0 数据流。"""
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "stability.jsonl"
        connector, mock, sampler, executor, recorder = setup_env(20786, log_path)
        try:
            orig_attack = mock._h_attack
            def attack_with_stray(args):
                resp = orig_attack(args)
                mock._next_evt += 1
                mock._events_queue.append({
                    "event_id": f"evt_{mock._next_evt}",
                    "kind": "entity_appeared",
                    "source_entity": "EID:999",
                    "target_entity": "EID:999",
                    "game_ts": int(time.time() * 1000),
                })
                return resp
            mock.handlers["player_attack"] = attack_with_stray
            runner = StabilityRunner(connector, sampler, executor, recorder, rounds=8)
            report = runner.run()
            assert report["action_completed"] >= 1, report
            assert report["stashed_events_merged"] >= 1, report
            attack_details = [x for x in report["round_details"]
                              if (x.get("action") or "").startswith("attack")]
            assert any(x.get("stashed_merged", 0) >= 1 for x in attack_details), attack_details
            for rid in recorder.list_rounds():
                entry = recorder.load_round(rid)
                assert isinstance(entry["w0"].get("events", []), list)
        finally:
            connector.stop()
            mock.close()


if __name__ == "__main__":
    check("20_rounds_no_interruption", test_20_rounds_no_interruption)
    check("dirty_ratio_below_threshold", test_dirty_ratio_below_threshold)
    check("log_replay_all_rounds", test_log_replay_all_rounds)
    check("actions_executed", test_actions_executed)
    check("c_h_recorded_each_round", test_c_h_recorded_each_round)
    check("report_passed_flag", test_report_passed_flag)
    check("action_status_counted", test_action_status_counted)
    check("stashed_events_merged", test_stashed_events_merged)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
