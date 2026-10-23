"""S29 动作规划器（LLM 自主行动渐进部署）离线自测。

契约（S29 锁定）：
- ActionPlanner 双模式：shadow（旁观学习——建议只记录不执行，实际
  动作仍走默认编排）/ active（自主行动——建议经白名单+位置约束校验
  通过才执行，被拒回退默认编排）。
- 反套壳：planner 只决定动作选择（行动权），真值判定仍归
  rule_validator（对齐分/反例/降级不因 planner 改变）；默认离线
  heuristic 确定性策略零模型。
"""
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_game_server import MockGameServer

from w0.game_connector import GameConnector
from w0.data_sampler import DataSampler
from w1.rule_validator import RuleValidator
from w2.axiom_engine import AxiomEngine
from w2.downgrade_manager import DowngradeManager
from w2.qs_kernel import QSKernel
from w3.action_planner import ActionPlanner, parse_plan_json
from w3.action_executor import ActionExecutor
from w3.closed_loop_runner import ClosedLoopRunner, deterministic_embedding
from w3.closed_loop_runner import default_state_fn
from w3.log_recorder import LogRecorder

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


def entity(eid, kind, labels, x, y=0.0, z=0.0):
    return {"entity_id": eid, "kind": kind, "labels": labels,
            "hp": 100, "position": {"x": x, "y": y, "z": z}}


# ── mock：玩家 + 1 NPC（s17 BaseMock 精简版）───────────────


class PlayerMock(MockGameServer):
    def __init__(self, port):
        super().__init__(port=port, initial_scenario="empty")
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._events_queue = []
        self._next_evt = 0
        self.handlers["scan_entities"] = self._h_scan
        self.handlers["get_events"] = self._h_get_events
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move

    def _h_scan(self, args):
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
            "event_id": f"evt_{self._next_evt}", "kind": "hp_changed",
            "source_entity": "EID:1", "target_entity": eid,
            "game_ts": int(time.time() * 1000)})
        return {"action_id": f"act_{self._next_evt}", "accepted": True,
                "reason": "damage_dispatched"}

    def _h_move(self, args):
        self.player_pos = {"x": float(args.get("x", 0)),
                           "y": float(args.get("y", 0)),
                           "z": float(args.get("z", 0))}
        return {"action_id": "act_move", "accepted": True,
                "reason": "teleport_dispatched"}


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


def make_runner(env, rounds, planner=None):
    connector, mock, sampler, executor, recorder, validator, engine, dm, qs = env
    return ClosedLoopRunner(
        connector, sampler, executor, recorder,
        axiom_engine=engine, downgrade_manager=dm, qs_kernel=qs,
        rule_validator=validator,
        embedding_fn=deterministic_embedding,
        state_fn=default_state_fn,
        action_planner=planner,
        rounds=rounds,
        functional_death_k=3, h_l_window_n=5,
        collect_retry_max=2, collect_retry_sleep_s=0.05,
        reconnect_wait_s=8.0)


# ── 测试 1：heuristic 默认策略（确定性）─────────────────────


def test_heuristic_default_plan():
    pl = ActionPlanner(mode="shadow")
    obs = {"entities": [entity("EID:N1", "npc", ["civilian"], 5.0),
                        entity("EID:N2", "npc", ["gang"], 12.0)],
           "player_position": {"x": 0.0, "y": 0.0, "z": 0.0}}
    plan = pl.plan(obs, 0)
    assert plan["action_type"] == "attack", plan
    assert plan["target_entity_id"] == "EID:N1", plan  # 最近目标
    assert plan.get("advisory") is True, plan
    # 无目标 → 交替移动
    obs2 = {"entities": [entity("EID:1", "player", ["player"], 0.0)],
            "player_position": {"x": 0.0, "y": 0.0, "z": 0.0}}
    m1 = pl.plan(obs2, 1)
    m2 = pl.plan(obs2, 2)
    assert m1["action_type"] == "move" and m2["action_type"] == "move", (m1, m2)
    assert m1["params"]["dx"] == 10.0 and m2["params"]["dx"] == -10.0, (m1, m2)
    # 确定性：同输入同输出
    pl2 = ActionPlanner(mode="shadow")
    assert pl2.plan(obs, 0)["target_entity_id"] == "EID:N1"


# ── 测试 2：白名单校验（非法动作 / 目标不存在拒绝）────────────


def test_whitelist_validation():
    pl = ActionPlanner(mode="shadow", area_center={"x": 0, "y": 0, "z": 0},
                       area_radius=50.0)
    obs = {"entities": [entity("EID:N1", "npc", ["civilian"], 5.0)]}
    ok, why = pl.validate({"action_type": "teleport"}, obs)
    assert not ok and "whitelist" in why, why
    ok, why = pl.validate({"action_type": "attack"}, obs)
    assert not ok and "target" in why, why
    ok, why = pl.validate({"action_type": "attack",
                           "target_entity_id": "EID:GHOST"}, obs)
    assert not ok and "not in current scan" in why, why
    ok, why = pl.validate({"action_type": "attack",
                           "target_entity_id": "EID:N1"}, obs)
    assert ok, why
    ok, why = pl.validate({"action_type": "move"}, obs)  # 缺 dx/dz → 拒绝
    assert not ok and "non-numeric" in why, why


# ── 测试 3：位置约束（固定实验区域）─────────────────────────


def test_area_constraint():
    pl = ActionPlanner(mode="shadow", area_center={"x": 0, "y": 0, "z": 0},
                       area_radius=50.0)
    obs = {"entities": [entity("EID:1", "player", ["player"], 0.0)]}
    # 区域内：原点 +dx20 → 落点距离 20 ≤ 50
    ok, why = pl.validate({"action_type": "move",
                           "params": {"dx": 20, "dz": 0}}, obs,
                          player_position={"x": 0, "y": 0, "z": 0})
    assert ok, why
    # S29.15 落点校验：当前位置 40 在区域内，落点 60 出界
    # （旧语义查当前位置会误放行，每步可合法越界 ≤20m）
    ok, why = pl.validate({"action_type": "move",
                           "params": {"dx": 20, "dz": 0}}, obs,
                          player_position={"x": 40, "y": 0, "z": 0})
    assert not ok and "outside" in why, why
    # 水平面欧氏度量（2026-08-26 地形感知改造）：落点映射
    # dx→x、dz→y，高度不计入区域距离。平面 (50,50) → 70.7 > 50 出界。
    ok, why = pl.validate({"action_type": "move",
                           "params": {"dx": 20, "dz": 20}}, obs,
                          player_position={"x": 30, "y": 30, "z": 20})
    assert not ok and "outside" in why, why
    # 高度差不影响区域判定（落在高台/天桥仍属区域内）
    ok, why = pl.validate({"action_type": "move",
                           "params": {"dx": 5, "dz": 5}}, obs,
                          player_position={"x": 20, "y": 20, "z": 100})
    assert ok, why
    # 无区域约束时 move 不检查位置
    pl2 = ActionPlanner(mode="shadow")
    ok, why = pl2.validate({"action_type": "move",
                            "params": {"dx": 5, "dz": 0}}, obs)
    assert ok, why
    # S29.15 非数值参数拒绝（此前能通过校验、执行层 float() 崩溃
    # 杀死整批实验——护栏须在校验层拒绝并回退默认编排）
    ok, why = pl.validate({"action_type": "move",
                           "params": {"dx": "ten", "dz": 0}}, obs,
                          player_position={"x": 0, "y": 0, "z": 0})
    assert not ok and "non-numeric" in why, why


# ── 测试 4：shadow 模式——只记录不执行，动作序列不变 ──────────


def test_shadow_mode_does_not_execute():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "a.jsonl"
        shadow = Path(d) / "b.jsonl"
        # 无 planner
        mock = PlayerMock(port=20900)
        env = setup_env(20900, base, mock)
        try:
            rep_none = make_runner(env, rounds=4).run()
            action_seq_none = [r["action"] for r in rep_none["round_details"]]
            assert all(r["action_source"] == "default"
                       for r in rep_none["round_details"]), rep_none
        finally:
            teardown(env[0], mock)
        # shadow planner
        mock2 = PlayerMock(port=20901)
        env2 = setup_env(20901, shadow, mock2)
        try:
            pl = ActionPlanner(mode="shadow")
            rep_shadow = make_runner(env2, rounds=4, planner=pl).run()
            rds = rep_shadow["round_details"]
            action_seq_shadow = [r["action"] for r in rds]
            assert action_seq_shadow == action_seq_none, \
                f"shadow must not change actions: {action_seq_shadow} vs {action_seq_none}"
            for r in rds:
                assert r["action_source"] == "default", r
                assert r["proposed_action"] is not None, r
                assert r["proposed_validated"] is not None, r
                assert "ok" in r["proposed_validated"], r
        finally:
            teardown(env2[0], mock2)


# ── 测试 5：active 模式——校验通过执行建议，被拒回退 ─────────


def test_active_mode_executes_proposal():
    with tempfile.TemporaryDirectory() as d:
        # plan_fn 返回合法 attack（EID:N1 在扫描中）
        def plan_fn(planner, obs, rid):
            return {"action_type": "attack", "target_entity_id": "EID:N1",
                    "params": {}, "reason": "test-llm", "advisory": True}
        mock = PlayerMock(port=20902)
        log_path = Path(d) / "active.jsonl"
        env = setup_env(20902, log_path, mock)
        try:
            pl = ActionPlanner(mode="active", plan_fn=plan_fn)
            rep = make_runner(env, rounds=4, planner=pl).run()
            rds = rep["round_details"]
            # attack 轮 action_source=planner（奇数轮默认也 attack，但
            # planner 建议总是合法 → 所有轮 source=planner）
            for r in rds:
                assert r["action_source"] == "planner", r
                assert r["action"].startswith("attack:EID:N1"), r
        finally:
            teardown(env[0], mock)
        # plan_fn 返回非法（目标不存在）→ 回退默认编排
        def bad_fn(planner, obs, rid):
            return {"action_type": "attack", "target_entity_id": "EID:GHOST",
                    "params": {}, "reason": "bad", "advisory": True}
        mock2 = PlayerMock(port=20903)
        log_path2 = Path(d) / "fallback.jsonl"
        env2 = setup_env(20903, log_path2, mock2)
        try:
            pl2 = ActionPlanner(mode="active", plan_fn=bad_fn)
            rep2 = make_runner(env2, rounds=4, planner=pl2).run()
            rds2 = rep2["round_details"]
            for r in rds2:
                assert r["action_source"] == "default", r
                assert r["proposed_validated"]["ok"] is False, r
        finally:
            teardown(env2[0], mock2)


# ── 测试 6：零 LLM 默认（action_planner 源码无模型依赖）─────


def test_no_model_imports():
    src = (Path(__file__).resolve().parents[1] / "src" / "w3"
           / "action_planner.py").read_text(encoding="utf-8")
    imports = [ln.strip() for ln in src.splitlines()
               if re.match(r"^\s*(import|from)\s", ln)]
    banned = ("torch", "transformers", "model_adapter", "llm_assistant",
              "bitsandbytes")
    for ln in imports:
        for word in banned:
            assert word not in ln, f"action_planner must not import {word}: {ln}"


# ── 测试 7：反套壳——planner 不改变裁决（对齐分一致）─────────


def test_planner_does_not_affect_verdict():
    with tempfile.TemporaryDirectory() as d:
        seqs = []
        for port, tag in ((20904, "none"), (20905, "shadow")):
            mock = PlayerMock(port=port)
            env = setup_env(port, Path(d) / f"{tag}.jsonl", mock)
            try:
                pl = None if tag == "none" else ActionPlanner(mode="shadow")
                rep = make_runner(env, rounds=4, planner=pl).run()
                seqs.append([r["alignment_score"] for r in rep["round_details"]])
            finally:
                teardown(env[0], mock)
        assert seqs[0] == seqs[1], f"planner must not alter alignment: {seqs}"


# ── 测试 8：LLM 建议 JSON 解析容错 ─────────────────────────


def test_parse_plan_json():
    ok = parse_plan_json('{"action_type": "attack", "target_entity_id": '
                         '"EID:N1", "params": null, "reason": "最近"}')
    assert ok and ok["action_type"] == "attack" \
        and ok["target_entity_id"] == "EID:N1", ok
    ok2 = parse_plan_json('```json\n{"action_type": "move", '
                          '"target_entity_id": null, "params": {"dx": 5}, '
                          '"reason": "探索"}\n```')
    assert ok2 and ok2["params"]["dx"] == 5, ok2
    assert parse_plan_json("抱歉，我无法") is None
    assert parse_plan_json('{"action_type": "fly"}') is None
    assert parse_plan_json('{"action_type": "attack"}') is None  # 缺目标
    assert parse_plan_json("") is None
    assert parse_plan_json(None) is None
    assert parse_plan_json("{not json}") is None


if __name__ == "__main__":
    check("heuristic_default_plan", test_heuristic_default_plan)
    check("whitelist_validation", test_whitelist_validation)
    check("area_constraint", test_area_constraint)
    check("shadow_mode_does_not_execute", test_shadow_mode_does_not_execute)
    check("active_mode_executes_proposal", test_active_mode_executes_proposal)
    check("no_model_imports", test_no_model_imports)
    check("planner_does_not_affect_verdict", test_planner_does_not_affect_verdict)
    check("parse_plan_json", test_parse_plan_json)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
