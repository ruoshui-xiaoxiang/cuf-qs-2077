"""S18 对照组基线离线自测（RED→GREEN 契约）。

对照口径契约（S18 锁定）：
- 同一批观测数据：回放实验组 JSONL 归档的 w0 层，对照组不连游戏、无动作。
- 机制：纯频率拟合——标签组合 (kind, labels) → 异常经验概率；无公理、
  无降级、无保护闸门、无边界补全（与实验组的机制差异正是被检验项）。
- 实际异常真值：rule_validator.is_valid_counterexample 的 violated_rules
  非空（任一公理），不施加标签多样性闸门（闸门是实验组机制）。
- 口径映射：per-round accuracy acc_t → alignment-equivalent
  score_t = 2*acc_t - 1 ∈ [-1,1]，与实验组对齐分同域。
- 测量仪器：QSKernel.check_monotonicity(scores=...) 同 window/ε/τ。
- 解体：①/③ not_applicable（无公理、无反例机制）；② 同口径——
  连续 K 轮 score < τ → functional_death，虚拟寿命 = completed。
- 在线学习协议：预测用当轮更新前的模型，统计更新在预测与裁决之后。
- 观测构造镜像实验组：先并入当轮标签组合再评估（s16 步骤 4 时序），
  context = {distance_to_player, entity_in_scan=True,
  entity_in_later_scan=True}。
"""
import json
import re
import sys
import tempfile
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
)
from w3.statistical_baseline import (
    StatisticalBaseline,
    compare_reports,
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
                {"entity_id": "EID:N2", "kind": "npc",
                 "labels": ["civilian", "vendor"], "hp": 80,
                 "position": {"x": 9.0, "y": 0.0, "z": 0.0}},
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


def setup_env(port, log_path, extend_budget=None):
    """装配实验组环境（与 s16 setup_env 同口径）。"""
    connector = GameConnector(port=port)
    connector.start()
    mock = LoopMock(port=port, zombies=True)
    mock.connect_with_retry()
    if not connector.wait_for_game(timeout=5.0):
        raise RuntimeError("mock did not connect")
    sampler = DataSampler(connector)
    executor = ActionExecutor.from_config(connector, path=None)
    if extend_budget is not None:
        recorder = LogRecorder(log_path, extend_budget=extend_budget)
    else:
        recorder = LogRecorder(log_path)
    validator = RuleValidator()
    engine = AxiomEngine()
    qs = QSKernel(window_size=5, epsilon=0.01, min_rounds=5,
                  convergence_threshold=0.95)
    dm = DowngradeManager(engine, qs_kernel=qs)
    return connector, mock, sampler, executor, recorder, validator, engine, dm, qs


def teardown(connector, mock):
    connector.stop()
    mock.close()


def run_exp_group(env, rounds):
    connector, mock, sampler, executor, recorder, validator, engine, dm, qs = env
    runner = ClosedLoopRunner(
        connector, sampler, executor, recorder,
        axiom_engine=engine, downgrade_manager=dm, qs_kernel=qs,
        rule_validator=validator,
        embedding_fn=deterministic_embedding,
        state_fn=default_state_fn,
        rounds=rounds,
        functional_death_k=3, h_l_window_n=5,
    )
    runner.inject_axioms_from_w1()
    return runner.run(), recorder


def make_normal_batches(n_rounds=6):
    """正常世界观测批：玩家 + 两个正常 NPC，无任何异常信号。"""
    batches = []
    for r in range(n_rounds):
        batches.append({
            "round_id": r,
            "entities": [
                {"entity_id": "EID:1", "kind": "player",
                 "labels": ["player"], "hp": 100,
                 "position": {"x": 0.0, "y": 0.0, "z": 0.0}},
                {"entity_id": "EID:N1", "kind": "npc",
                 "labels": ["civilian"], "hp": 100,
                 "position": {"x": 5.0, "y": 0.0, "z": 0.0}},
                {"entity_id": "EID:N2", "kind": "npc",
                 "labels": ["civilian", "vendor"], "hp": 80,
                 "position": {"x": 9.0, "y": 0.0, "z": 0.0}},
            ],
            "events": [],
        })
    return batches


def make_adversarial_batches(n_rounds=8):
    """对抗观测批：单一实体在 异常/正常 间交替，频率拟合永远追错。"""
    batches = []
    for r in range(n_rounds):
        hp = 0 if r % 2 == 0 else 100
        batches.append({
            "round_id": r,
            "entities": [
                {"entity_id": "EID:X1", "kind": "npc",
                 "labels": ["rogue"], "hp": hp,
                 "position": {"x": 5.0, "y": 0.0, "z": 0.0}},
            ],
            "events": [],
        })
    return batches


# ── 测试 1：正常世界——纯频率拟合收敛到满分 ──────────────────


def test_normal_scenario_perfect_fit():
    base = StatisticalBaseline(rounds=6, functional_death_k=3)
    report = base.run(make_normal_batches(6))
    assert report["group"] == "baseline_statistical", report["group"]
    assert report["completed"] == 6, report
    assert report["disintegrated"] is False, report
    assert report["criterion"] is None
    assert report["virtual_lifespan"] == 6
    assert report["accuracy_history"] == [1.0] * 6, report["accuracy_history"]
    assert report["score_history"] == [1.0] * 6, report["score_history"]
    assert report["converged_final"] is True, report
    rds = report["round_details"]
    assert len(rds) == 6
    for rd in rds:
        assert rd["entities_count"] == 3, rd
        assert rd["accuracy"] == 1.0, rd
        assert all(not j["actual_anomaly"] for j in rd["adjudications"]), rd
        assert all(p["predicted_anomaly"] is False
                   for p in rd["predictions"]), rd
        assert "induction_conclusions" in rd and rd["induction_conclusions"], rd


# ── 测试 2：归档回放——与实验组同一批观测 ─────────────────────


def test_archive_replay_same_observations():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20810, log_path)
        connector, mock = env[0], env[1]
        try:
            exp_report, recorder = run_exp_group(env, rounds=8)
            assert exp_report["completed"] == 8, exp_report
            base = StatisticalBaseline(rounds=8, functional_death_k=3)
            report = base.run_from_archive(recorder)
            assert len(report["round_details"]) == 8, report["completed"]
            for rid in recorder.list_rounds():
                entry = recorder.load_round(rid)
                n_exp = len(entry["w0"].get("entities", []))
                n_base = report["round_details"][rid]["entities_count"]
                assert n_base == n_exp, \
                    f"round {rid}: baseline {n_base} != experiment {n_exp}"
        finally:
            teardown(connector, mock)


# ── 测试 3：僵尸剧本学习动力学——先错后对，口径可对比 ─────────


def test_anomaly_learning_dynamics():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20811, log_path)
        connector, mock = env[0], env[1]
        try:
            exp_report, recorder = run_exp_group(env, rounds=8)
            base = StatisticalBaseline(rounds=8, functional_death_k=3)
            report = base.run_from_archive(recorder)

            acc = report["accuracy_history"]
            scores = report["score_history"]
            assert len(acc) == 8 and len(scores) == 8, (acc, scores)
            # r0：无历史 → 先验预测正常；僵尸实际异常 → 3 实体只对 1
            assert abs(acc[0] - 1.0 / 3.0) < 1e-9, acc[0]
            assert abs(scores[0] - (2.0 / 3.0 - 1.0)) < 1e-9, scores[0]
            # r1 起：undead 组合经验概率 → 1，预测翻转，全对
            assert all(a == 1.0 for a in acc[1:]), acc
            assert all(s == 1.0 for s in scores[1:]), scores
            assert report["converged_final"] is True, report
            assert report["disintegrated"] is False, report
            assert report["virtual_lifespan"] == 8
            # r0 的归纳结论：两个 undead 组合各 1 次观测、异常概率 1.0
            concl0 = report["round_details"][0]["induction_conclusions"]
            undead = [c for c in concl0 if "undead" in c["combo"]]
            assert len(undead) == 2, concl0
            assert all(c["n_observed"] == 1 and c["p_anomaly"] == 1.0
                       for c in undead), undead
        finally:
            teardown(connector, mock)


# ── 测试 4：判据② 同口径触发——功能死亡 + 虚拟寿命 ───────────


def test_criterion2_functional_death_same_calibration():
    base = StatisticalBaseline(rounds=8, functional_death_k=3)
    report = base.run(make_adversarial_batches(8))
    assert report["disintegrated"] is True, report
    assert report["criterion"] == "functional_death", report["criterion"]
    assert report["completed"] == 3, \
        f"K=3 → stop after r2, completed={report['completed']}"
    assert report["virtual_lifespan"] == 3
    assert report["accuracy_history"] == [0.0, 0.0, 0.0], \
        report["accuracy_history"]
    assert report["score_history"] == [-1.0, -1.0, -1.0], \
        report["score_history"]
    ca = report["criteria_applicability"]
    assert ca["semantic_death"] == "not_applicable", ca
    assert ca["functional_death"] == "applicable", ca
    assert ca["over_budget"] == "not_applicable", ca


# ── 测试 5：空实体批——优雅降级不计分 ─────────────────────────


def test_empty_batch_graceful():
    batches = [
        {"round_id": 0, "entities": [], "events": []},
        {"round_id": 1, "entities": [], "events": []},
    ]
    base = StatisticalBaseline(rounds=2, functional_death_k=3)
    report = base.run(batches)
    assert report["completed"] == 2, report
    assert report["score_history"] == [], report["score_history"]
    assert report["accuracy_history"] == [], report["accuracy_history"]
    for rd in report["round_details"]:
        assert rd["accuracy"] is None, rd
        assert rd["alignment_score"] is None, rd
    assert report["disintegrated"] is False, report
    assert report["virtual_lifespan"] == 2


# ── 测试 6：配置段与 from_config 装配 ─────────────────────────


def test_baseline_config_and_wiring():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "baseline" in raw, "config must declare baseline section"
    b = raw["baseline"]
    assert b["prior_anomaly_prob"] == 0.0, b
    assert b["anomaly_prob_threshold"] == 0.5, b

    base = StatisticalBaseline.from_config(
        config_path=str(CONFIG_PATH), rounds=6)
    assert base.prior_anomaly_prob == 0.0
    assert base.anomaly_prob_threshold == 0.5
    assert base.functional_death_k == 3, base.functional_death_k
    assert base.qs_kernel.convergence_threshold == 0.6
    assert base.qs_kernel.epsilon == 0.01
    report = base.run(make_normal_batches(6))
    assert report["completed"] == 6, report
    assert report["converged_final"] is True, report


# ── 测试 7：对比口径产物——compare_reports ─────────────────────


def test_compare_reports_alignment():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20812, log_path)
        connector, mock = env[0], env[1]
        try:
            exp_report, recorder = run_exp_group(env, rounds=8)
            base = StatisticalBaseline(rounds=8, functional_death_k=3)
            base_report = base.run_from_archive(recorder)
            cmp = compare_reports(exp_report, base_report)
            for key in ("exp_series", "baseline_series", "compared_rounds",
                        "exp_mean", "baseline_mean", "converged_exp",
                        "converged_baseline", "lifespan_exp",
                        "lifespan_baseline", "lifespan_delta",
                        "exp_disintegrated", "baseline_disintegrated",
                        "exp_criterion", "baseline_criterion"):
                assert key in cmp, f"compare missing key {key}"
            assert cmp["compared_rounds"] == 8, cmp
            assert len(cmp["exp_series"]) == 8
            assert len(cmp["baseline_series"]) == 8
            assert cmp["lifespan_exp"] == 8 and cmp["lifespan_baseline"] == 8
            assert cmp["lifespan_delta"] == 0, cmp
            # 对照组 r0 被异常拉低，均值必然低于实验组（全 1.0）
            assert cmp["baseline_mean"] < cmp["exp_mean"], cmp
            assert cmp["exp_disintegrated"] is False
            assert cmp["baseline_disintegrated"] is False
        finally:
            teardown(connector, mock)


# ── 测试 8：反套壳——对照组零模型依赖 ──────────────────────────


def test_no_model_imports():
    src = (Path(__file__).resolve().parents[1] / "src" / "w3"
           / "statistical_baseline.py").read_text(encoding="utf-8")
    imports = [ln.strip() for ln in src.splitlines()
               if re.match(r"^\s*(import|from)\s", ln)]
    banned = ("torch", "transformers", "model_adapter", "llm_assistant",
              "bitsandbytes")
    for ln in imports:
        for word in banned:
            assert word not in ln, \
                f"baseline must not import {word}: {ln}"


if __name__ == "__main__":
    check("normal_scenario_perfect_fit", test_normal_scenario_perfect_fit)
    check("archive_replay_same_observations", test_archive_replay_same_observations)
    check("anomaly_learning_dynamics", test_anomaly_learning_dynamics)
    check("criterion2_functional_death_same_calibration",
          test_criterion2_functional_death_same_calibration)
    check("empty_batch_graceful", test_empty_batch_graceful)
    check("baseline_config_and_wiring", test_baseline_config_and_wiring)
    check("compare_reports_alignment", test_compare_reports_alignment)
    check("no_model_imports", test_no_model_imports)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
