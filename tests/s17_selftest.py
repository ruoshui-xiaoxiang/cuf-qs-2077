"""S17 异常边界处理离线自测（RED→GREEN 契约）。

验收标准：剧情锁、空实体、加载异常、游戏崩溃恢复与检查点四类边界场景
全部优雅降级，不中断实验（interruption 恒 False）。

故障分级契约：
- 剧情锁：动作回执 accepted=False → status=rejected，轮照常归档。
- 空实体：扫描为空 → 空轮照常归档（无假设/无反例），标 empty_scan。
- 加载异常（采集瞬时故障）：GameConnectorError → 重试 collect_retry_max
  次（间隔 collect_retry_sleep_s）→ 恢复则继续；若已断连则等待重连
  reconnect_wait_s；持续不可用 → 该轮优雅跳过（记入 skipped_rounds）。
- 游戏崩溃恢复：断连（reader EOF 即时检出）→ 等待重连 → 恢复后继续。
- 检查点：JSONL 已归档轮次即检查点，run(resume=True) 跳过已归档轮。

端口分配：20800-20806。
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
from w1.rule_validator import RuleValidator
from w2.axiom_engine import AxiomEngine
from w2.qs_kernel import QSKernel
from w2.downgrade_manager import DowngradeManager
from w3.action_executor import ActionExecutor
from w3.log_recorder import LogRecorder
from w3.closed_loop_runner import (
    ClosedLoopRunner,
    deterministic_embedding,
    default_state_fn,
    read_fault_tolerance_config,
)
from mock_game_server import MockGameServer, MockCallError

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


# ── mock 变体 ───────────────────────────────────────────────


class BaseMock(MockGameServer):
    """最小可运行 mock：玩家 + 普通 NPC（无僵尸，不触发反例）。

    crash_after_scan：第 N 次 scan_entities 被调用时同步关连接并抛错
    （应答永不送达），确定性模拟游戏崩溃——connector reader EOF 即时
    检出断连，该次调用在 Python 侧必然得到 GameConnectorError。
    注意：执行器 move 观测的 _player_position 也调 scan_entities，
    同样计入 _scan_count。
    """

    def __init__(self, port, crash_after_scan=None):
        super().__init__(port=port, initial_scenario="empty")
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._events_queue = []
        self._next_evt = 0
        self.crash_after_scan = crash_after_scan
        self._scan_count = 0
        self.handlers["scan_entities"] = self._h_scan
        self.handlers["get_events"] = self._h_get_events
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move

    def _crash_now(self):
        self._stop.set()
        sock = self.sock
        self.sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _h_scan(self, args):
        self._scan_count += 1
        if (self.crash_after_scan is not None
                and self._scan_count == self.crash_after_scan):
            self._crash_now()
            raise MockCallError("game_crashed")
        entities = [
            {"entity_id": "EID:1", "kind": "player",
             "labels": ["player"], "hp": 100,
             "position": dict(self.player_pos)},
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


class StoryLockMock(BaseMock):
    """剧情锁：攻击一律被游戏侧拒绝（如剧情演出期间输入被锁）。"""

    def _h_attack(self, args):
        return {"action_id": None, "accepted": False,
                "reason": "story_locked"}


class EmptyScanMock(MockGameServer):
    """空实体：扫描恒空（加载完成但区域内无实体）。"""

    def __init__(self, port):
        super().__init__(port=port, initial_scenario="empty")
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.handlers["scan_entities"] = self._h_scan
        self.handlers["get_events"] = self._h_get_events
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move

    def _h_scan(self, args):
        return {"count": 0, "entities": [], "player": None}

    def _h_get_events(self, args):
        return {"events": []}

    def _h_attack(self, args):
        return {"action_id": None, "accepted": True,
                "reason": "damage_dispatched"}

    def _h_move(self, args):
        self.player_pos = {"x": float(args.get("x", 0)),
                           "y": float(args.get("y", 0)),
                           "z": float(args.get("z", 0))}
        return {"action_id": "act_move", "accepted": True,
                "reason": "teleport_dispatched"}


class TransientFaultMock(BaseMock):
    """加载异常（瞬时）：前 fail_count 次 scan_entities 抛错，随后恢复。"""

    def __init__(self, port, fail_count=2):
        super().__init__(port)
        self.fail_count = fail_count
        self._scan_calls = 0
        self.handlers["scan_entities"] = self._h_scan_flaky

    def _h_scan_flaky(self, args):
        self._scan_calls += 1
        if self._scan_calls <= self.fail_count:
            raise MockCallError("loading_in_progress")
        return self._h_scan(args)


class PersistentFaultMock(BaseMock):
    """加载异常（持续）：scan_entities 恒抛错但连接不断（长时间加载/卡死）。"""

    def __init__(self, port):
        super().__init__(port)
        self.handlers["scan_entities"] = self._h_scan_dead

    def _h_scan_dead(self, args):
        raise MockCallError("loading_stuck")


# ── 装配 ────────────────────────────────────────────────────


def setup_env(port, log_path, mock):
    connector = GameConnector(port=port, response_timeout_s=3.0)
    connector.start()
    mock.port = port
    mock.connect_with_retry(interval_s=0.3)
    if not connector.wait_for_game(timeout=5.0):
        raise RuntimeError("mock did not connect")
    sampler = DataSampler(connector)
    executor = ActionExecutor(connector, poll_interval_s=0.05,
                              attack_timeout_s=2.0, move_timeout_s=2.0)
    recorder = LogRecorder(log_path)
    validator = RuleValidator()
    engine = AxiomEngine()
    qs = QSKernel(window_size=5, epsilon=0.01, min_rounds=5,
                  convergence_threshold=0.95)
    dm = DowngradeManager(engine, qs_kernel=qs)
    return connector, mock, sampler, executor, recorder, validator, engine, dm, qs


def teardown(connector, mock):
    connector.stop()
    try:
        mock.close()
    except Exception:
        pass


def make_runner(env, rounds, mock_cls_kwargs=None):
    connector, mock, sampler, executor, recorder, validator, engine, dm, qs = env
    runner = ClosedLoopRunner(
        connector, sampler, executor, recorder,
        axiom_engine=engine, downgrade_manager=dm, qs_kernel=qs,
        rule_validator=validator,
        embedding_fn=deterministic_embedding,
        state_fn=default_state_fn,
        rounds=rounds,
        functional_death_k=3, h_l_window_n=5,
        collect_retry_max=2,
        collect_retry_sleep_s=0.05,
        reconnect_wait_s=8.0,
    )
    runner.inject_axioms_from_w1()
    return runner


# ── 测试 1：剧情锁——动作被拒不中断 ──────────────────────────


def test_story_lock_rejected_nonfatal():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "s17.jsonl"
        mock = StoryLockMock(port=20800)
        env = setup_env(20800, log_path, mock)
        connector = env[0]
        recorder = env[4]
        try:
            runner = make_runner(env, rounds=4)
            report = runner.run()
            assert report["interruption"] is False, report["interruption_reason"]
            assert report["completed"] == 4, report["completed"]
            rds = report["round_details"]
            attacks = [r for r in rds if r["action"].startswith("attack")]
            assert len(attacks) == 2, f"r1/r3 should attack: {[r['action'] for r in rds]}"
            for r in attacks:
                assert r["action_status"] == "rejected", r
                assert r["action_reject_reason"] == "story_locked", r
            assert len(recorder.list_rounds()) == 4, recorder.list_rounds()
            for rid in recorder.list_rounds():
                entry = recorder.load_round(rid)
                assert entry["w3"]["action_status"] in ("rejected", "completed"), entry["w3"]
        finally:
            teardown(connector, mock)


# ── 测试 2：空实体——空轮照常归档 ─────────────────────────────


def test_empty_scan_nonfatal():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "s17.jsonl"
        mock = EmptyScanMock(port=20801)
        env = setup_env(20801, log_path, mock)
        connector, recorder = env[0], env[4]
        try:
            runner = make_runner(env, rounds=3)
            report = runner.run()
            assert report["interruption"] is False, report["interruption_reason"]
            assert report["completed"] == 3, report["completed"]
            for r in report["round_details"]:
                assert r["empty_scan"] is True, r
                assert r["counterexample_processing"] == [], r
            assert len(recorder.list_rounds()) == 3, recorder.list_rounds()
            for rid in recorder.list_rounds():
                entry = recorder.load_round(rid)
                assert entry["w0"]["entities"] == [], entry["w0"]
                assert entry["w3"]["hypotheses"] == [], entry["w3"]
            assert report["disintegrated"] is False, report["criterion"]
        finally:
            teardown(connector, mock)


# ── 测试 3：加载异常（瞬时）——重试后恢复 ────────────────────


def test_transient_collect_error_retry_recovers():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "s17.jsonl"
        mock = TransientFaultMock(port=20802, fail_count=2)
        env = setup_env(20802, log_path, mock)
        connector, recorder = env[0], env[4]
        try:
            runner = make_runner(env, rounds=3)
            report = runner.run()
            assert report["interruption"] is False, report["interruption_reason"]
            assert report["completed"] == 3, report["completed"]
            assert report["skipped_rounds"] == [], report["skipped_rounds"]
            rd0 = report["round_details"][0]
            assert rd0["collect_attempts"] >= 2, \
                f"r0 must have retried collection: {rd0}"
            assert len(recorder.list_rounds()) == 3, recorder.list_rounds()
        finally:
            teardown(connector, mock)


# ── 测试 4：游戏崩溃——断线等待重连后继续 ─────────────────────


def test_disconnect_reconnect_recovery():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "s17.jsonl"
        mock = BaseMock(port=20803, crash_after_scan=2)
        env = setup_env(20803, log_path, mock)
        connector, recorder = env[0], env[4]
        holder = {}

        def _restart():
            time.sleep(0.8)
            m2 = BaseMock(port=20803)
            m2.connect_with_retry(interval_s=0.3)
            holder["mock2"] = m2

        t = threading.Thread(target=_restart, daemon=True)
        t.start()
        try:
            runner = make_runner(env, rounds=4)
            report = runner.run()
            t.join(timeout=5.0)
            assert report["interruption"] is False, report["interruption_reason"]
            assert report["completed"] == 4, \
                f"crash round must recover, not skip: {report}"
            assert report["skipped_rounds"] == [], report["skipped_rounds"]
            retried = [r for r in report["round_details"]
                       if r["collect_attempts"] > 1 or r.get("recovered_via_reconnect")]
            assert retried, \
                f"some round must show reconnect recovery: {report['round_details']}"
            assert len(recorder.list_rounds()) == 4, recorder.list_rounds()
        finally:
            teardown(connector, holder.get("mock2") or mock)


# ── 测试 5：加载异常（持续）——优雅跳轮不中断 ─────────────────


def test_persistent_failure_graceful_skip():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "s17.jsonl"
        mock = PersistentFaultMock(port=20804)
        env = setup_env(20804, log_path, mock)
        connector, recorder = env[0], env[4]
        try:
            runner = ClosedLoopRunner(
                connector, env[2], env[3], recorder,
                axiom_engine=env[6], downgrade_manager=env[7], qs_kernel=env[8],
                rule_validator=env[5],
                embedding_fn=deterministic_embedding,
                state_fn=default_state_fn,
                rounds=3,
                functional_death_k=3, h_l_window_n=5,
                collect_retry_max=1,
                collect_retry_sleep_s=0.02,
                reconnect_wait_s=0.2,
            )
            runner.inject_axioms_from_w1()
            report = runner.run()
            assert report["interruption"] is False, report["interruption_reason"]
            assert report["completed"] == 0, report["completed"]
            assert len(report["skipped_rounds"]) == 3, report["skipped_rounds"]
            for s in report["skipped_rounds"]:
                assert "round_id" in s and "reason" in s, s
            assert len(recorder.list_rounds()) == 0, recorder.list_rounds()
            assert report["disintegrated"] is False, report["criterion"]
        finally:
            teardown(connector, mock)


# ── 测试 6：检查点——JSONL 归档即检查点，resume 恢复 ──────────


def test_checkpoint_resume():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "s17.jsonl"
        mock = BaseMock(port=20805)
        env = setup_env(20805, log_path, mock)
        connector, recorder = env[0], env[4]
        try:
            r1 = make_runner(env, rounds=4)
            rep1 = r1.run()
            assert rep1["completed"] == 4, rep1
            assert recorder.list_rounds() == [0, 1, 2, 3], recorder.list_rounds()

            engine2 = AxiomEngine()
            qs2 = QSKernel(window_size=5, epsilon=0.01, min_rounds=5,
                           convergence_threshold=0.95)
            dm2 = DowngradeManager(engine2, qs_kernel=qs2)
            runner2 = ClosedLoopRunner(
                connector, env[2], env[3], recorder,
                axiom_engine=engine2, downgrade_manager=dm2, qs_kernel=qs2,
                rule_validator=env[5],
                embedding_fn=deterministic_embedding,
                state_fn=default_state_fn,
                rounds=8,
                functional_death_k=3, h_l_window_n=5,
                collect_retry_max=2,
                collect_retry_sleep_s=0.05,
                reconnect_wait_s=8.0,
            )
            runner2.inject_axioms_from_w1()
            rep2 = runner2.run(resume=True)
            assert rep2["interruption"] is False, rep2["interruption_reason"]
            assert rep2["completed"] == 4, \
                f"resume must only run r4-r7: completed={rep2['completed']}"
            assert rep2["resume"]["enabled"] is True, rep2["resume"]
            assert rep2["resume"]["skipped_archived"] == [0, 1, 2, 3], rep2["resume"]
            assert [r["round_id"] for r in rep2["round_details"]] == [4, 5, 6, 7], \
                [r["round_id"] for r in rep2["round_details"]]
            assert recorder.list_rounds() == [0, 1, 2, 3, 4, 5, 6, 7], \
                recorder.list_rounds()
        finally:
            teardown(connector, mock)


# ── 测试 7：容错配置段 + from_config 装配 ────────────────────


def test_fault_tolerance_config_and_wiring():
    cfg = read_fault_tolerance_config(str(CONFIG_PATH))
    assert cfg["collect_retry_max"] == 2, cfg
    assert cfg["collect_retry_sleep_s"] == 1.0, cfg
    assert cfg["reconnect_wait_s"] == 30.0, cfg
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "fault_tolerance" in raw, "config must declare fault_tolerance section"

    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "s17.jsonl"
        connector = GameConnector(port=20806, response_timeout_s=3.0)
        connector.start()
        mock = BaseMock(port=20806)
        mock.connect_with_retry(interval_s=0.3)
        try:
            assert connector.wait_for_game(timeout=5.0)
            sampler = DataSampler(connector)
            executor = ActionExecutor(connector, poll_interval_s=0.05,
                                      attack_timeout_s=2.0, move_timeout_s=2.0)
            recorder = LogRecorder(log_path)
            runner = ClosedLoopRunner.from_config(
                connector, sampler, executor, recorder,
                config_path=str(CONFIG_PATH), rounds=2)
            assert runner.collect_retry_max == 2, runner.collect_retry_max
            assert runner.collect_retry_sleep_s == 1.0, runner.collect_retry_sleep_s
            assert runner.reconnect_wait_s == 30.0, runner.reconnect_wait_s
            report = runner.run()
            assert report["completed"] == 2, report
            assert report["interruption"] is False, report["interruption_reason"]
        finally:
            connector.stop()
            mock.close()


if __name__ == "__main__":
    check("story_lock_rejected_nonfatal", test_story_lock_rejected_nonfatal)
    check("empty_scan_nonfatal", test_empty_scan_nonfatal)
    check("transient_collect_error_retry_recovers",
          test_transient_collect_error_retry_recovers)
    check("disconnect_reconnect_recovery", test_disconnect_reconnect_recovery)
    check("persistent_failure_graceful_skip", test_persistent_failure_graceful_skip)
    check("checkpoint_resume", test_checkpoint_resume)
    check("fault_tolerance_config_and_wiring", test_fault_tolerance_config_and_wiring)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
