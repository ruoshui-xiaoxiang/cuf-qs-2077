"""S16 四层数据流串通离线自测（RED→GREEN 契约）。

闭环主链：公理注入 → W0 采集 → W3 动作 → 观测构造 → W1 反例判定
→ W2 降级（3预警/5触发 + 双门控保护）→ g 重注入 → QS 二次收敛
→ Γ脆三判据轮末检查（外层 OR，锁定决策 #12）→ 虚拟寿命记录。

离线双路嵌入（锁定决策 #1 在 S13 前的注入口径）：
- embedding_fn: deterministic_embedding（crc32+LCG，32维，跨进程确定）
- state_fn: 默认返回当前 g（完美对齐 1.0），判据②测试用反相向量

僵尸 NPC 剧本（机械触发 A_W2_5 反例）：
- hp=0 + 无 entity_death + 持续在扫描中 → R_W2_1（+R_W2_2，hostile 者 +R_W2_3）
- 双僵尸标签 ["undead","hostile"] / ["undead","neutral"] 满足
  rule_validator label_history 闸门 与 axiom_engine _label_variants 闸门
- 计数时序（每轮两实体按扫描序 z1→z2 处理，label_history 在建观测前
  已并入当轮全部实体标签）：
  r0: z1 被 _label_variants 闸门拒（variants=1）→ z2 recorded count=1
  r1: count=2 → count=3 warn
  r2: count=4 → count=5 downgrade → QS reset + g 重注入（剩余活跃公理）
  r3-r7: A_W2_5 已 deprecated 被跳过，count 饱和于 5
  c_extend_series = [1,3,5,5,5,5,5,5]
"""
import json
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
    read_disintegration_config,
)
from common.utils import ContextOverflowError
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


# ── mock 游戏侧 ─────────────────────────────────────────────


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


def setup_env(port, log_path, zombies=True, extend_budget=None, engine=None):
    """装配 connector + mock + W0/W2/W3 模块，返回八元组。"""
    connector = GameConnector(port=port)
    connector.start()
    mock = LoopMock(port=port, zombies=zombies)
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
    engine = engine or AxiomEngine()
    qs = QSKernel(window_size=5, epsilon=0.01, min_rounds=5,
                  convergence_threshold=0.95)
    dm = DowngradeManager(engine, qs_kernel=qs)
    return connector, mock, sampler, executor, recorder, validator, engine, dm, qs


def teardown(connector, mock):
    connector.stop()
    mock.close()


def make_runner(env, rounds, state_fn=None):
    connector, mock, sampler, executor, recorder, validator, engine, dm, qs = env
    return ClosedLoopRunner(
        connector, sampler, executor, recorder,
        axiom_engine=engine, downgrade_manager=dm, qs_kernel=qs,
        rule_validator=validator,
        embedding_fn=deterministic_embedding,
        state_fn=state_fn or default_state_fn,
        rounds=rounds,
        functional_death_k=3, h_l_window_n=5,
    )


def inject_three_axioms(engine, validator):
    for a in validator.get_axioms():
        engine.add_axiom(a["axiom_id"], a["hypothesis"], a["anchor"])


# ── 测试 1：确定性嵌入 ──────────────────────────────────────


def test_embedding_determinism():
    v1 = deterministic_embedding(["A_M1", "A_W1"])
    v2 = deterministic_embedding(["A_W1", "A_M1"])
    assert v1 == v2, "embedding must be order-independent"
    v3 = deterministic_embedding(["A_M1"])
    assert v1 != v3, "different axiom sets must give different vectors"
    assert len(v1) == 32, f"expected 32 dims, got {len(v1)}"
    assert all(isinstance(x, float) for x in v1), "all components must be float"
    v4 = deterministic_embedding(["A_M1", "A_W1"])
    assert v1 == v4, "embedding must be deterministic across calls"
    assert any(x != 0.0 for x in v1), "embedding must not be zero vector"


# ── 测试 2：disintegration 配置段 ────────────────────────────


def test_disintegration_config():
    cfg = read_disintegration_config(str(CONFIG_PATH))
    assert cfg["functional_death_k"] == 3, cfg
    assert cfg["h_l_window_n"] == 5, cfg
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "disintegration" in raw, "config must declare disintegration section"


# ── 测试 3：主链——降级 + 二次收敛（8 轮）───────────────────


def test_full_loop_downgrade_and_secondary_convergence():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20790, log_path, zombies=True)
        connector, mock = env[0], env[1]
        engine = env[6]
        try:
            runner = make_runner(env, rounds=8)
            runner.inject_axioms_from_w1()
            report = runner.run()

            # —— 公理终态：A_W2_5 降级，其余存活 ——
            assert report["axiom_final_status"] == {
                "A_M1": "active", "A_W1": "active", "A_W2_5": "deprecated",
            }, report["axiom_final_status"]

            rds = report["round_details"]
            assert len(rds) == 8, f"expected 8 rounds, got {len(rds)}"

            # —— r0：g 注入 + 双门控首拒（z1 拒、z2 记 count=1）——
            rd0 = rds[0]
            assert rd0["g_injected"] is True and rd0["g_vector"] is not None
            proc0 = rd0["counterexample_processing"]
            assert len(proc0) == 2, f"r0 expected 2 processings, got {len(proc0)}"
            assert proc0[0]["recorded"] is False, proc0[0]
            assert "label diversity" in proc0[0].get("reason", ""), proc0[0]
            assert proc0[1]["recorded"] is True and proc0[1]["count"] == 1, proc0[1]

            # —— r1：count=3 触发 warn ——
            actions1 = [p["action"] for p in rds[1]["counterexample_processing"]]
            assert "warn" in actions1, actions1

            # —— r2：count=5 触发 downgrade + g 重注入 ——
            warn1 = [p for p in rds[1]["counterexample_processing"]
                     if p["action"] == "warn"]
            assert warn1 and warn1[0]["boundary_conditions_added"], \
                f"warn round must auto-complete boundary: {rds[1]}"
            proc2 = rds[2]["counterexample_processing"]
            actions2 = [p["action"] for p in proc2]
            assert "downgrade" in actions2, actions2
            dg = [p for p in proc2 if p["action"] == "downgrade"][0]
            assert dg["g_recomputed"] is True, dg
            assert dg["boundary_conditions_added"] == [], \
                f"downgrade round dedups same combo (warn already added): {dg}"
            rd2 = rds[2]
            assert rd2["g_reinjected"] is True, rd2
            assert rd2["g_vector_after"] is not None
            assert rd2["g_vector_after"] != rd0["g_vector"], \
                "recomputed g must differ (active axiom set shrank)"

            # —— C_H,extend 序列：饱和于降级阈值（锁定决策 #11）——
            assert report["c_extend_series"] == [1, 3, 5, 5, 5, 5, 5, 5], \
                report["c_extend_series"]

            # —— 二次收敛：reset 后 r2..r7 共 6 个对齐分 ——
            hist = report["qs_history"]
            assert len(hist) == 6, f"post-reset history len {len(hist)} != 6"
            assert all(s >= 0.99 for s in hist), hist
            assert report["converged_final"] is True, \
                f"secondary convergence failed: {report}"

            # —— 边界自动补全（降级触发）——
            bcs = engine.get_axiom("A_W2_5")["boundary_conditions"]
            assert any(isinstance(b, dict) and b.get("source") == "auto_complete"
                       for b in bcs), bcs

            # —— 动作：attack r1/r5，move r2/r6，全部 completed ——
            for idx in (1, 5):
                assert rds[idx]["action"].startswith("attack"), rds[idx]
                assert rds[idx]["action_status"] == "completed", rds[idx]
            for idx in (2, 6):
                assert rds[idx]["action"].startswith("move"), rds[idx]
                assert rds[idx]["action_status"] == "completed", rds[idx]
                assert rds[idx]["action_evidence_count"] >= 1, \
                    f"move evidence missing at r{idx} (S5 观测窗口)"

            # —— 不解体：完整跑满 8 轮 ——
            assert report["disintegrated"] is False, report["criterion"]
            assert report["criterion"] is None
            assert report["virtual_lifespan"] == 8
            assert report["interruption"] is False, report["interruption_reason"]
            assert report["completed"] == 8
        finally:
            teardown(connector, mock)


# ── 测试 4：Γ脆判据③ 超预算解体（extend_budget=2）──────────


def test_criterion3_over_budget():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20791, log_path, zombies=True, extend_budget=2)
        connector, mock = env[0], env[1]
        recorder = env[4]
        try:
            runner = make_runner(env, rounds=8)
            runner.inject_axioms_from_w1()
            report = runner.run()
            assert report["disintegrated"] is True, report
            assert report["criterion"] == "over_budget", report["criterion"]
            assert report["completed"] == 2, \
                f"should stop after r1, completed={report['completed']}"
            assert report["virtual_lifespan"] == 2
            assert len(recorder.list_rounds()) == 2, recorder.list_rounds()
            entry_r1 = recorder.load_round(1)
            assert entry_r1["c_h"]["within_budget"] is False, entry_r1["c_h"]
            assert entry_r1["c_h"]["extend"] == 3, entry_r1["c_h"]
        finally:
            teardown(connector, mock)


# ── 测试 5：Γ脆判据② 功能死亡（反相 state_fn）+ H_L 证伪 ───


def test_criterion2_functional_death():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20792, log_path, zombies=False)
        connector, mock = env[0], env[1]

        def anti_state(round_index, ctx):
            g = ctx.get("target_g") or []
            return [-x for x in g]

        try:
            runner = make_runner(env, rounds=8, state_fn=anti_state)
            runner.inject_axioms_from_w1()
            report = runner.run()
            assert report["disintegrated"] is True, report
            assert report["criterion"] == "functional_death", report["criterion"]
            assert report["completed"] == 3, \
                f"K=3 → stop after r2, completed={report['completed']}"
            assert report["virtual_lifespan"] == 3
            hist = report["qs_history"]
            assert all(s < 0.95 for s in hist), hist
            # H_L：判据②触发但此前 c_extend 从未超 B_extend → 证伪
            h_l = report["h_l"]
            assert h_l["evaluated"] is True, h_l
            assert h_l["violated"] is True, h_l
            # 无反例：extend 恒 0
            assert report["c_extend_series"] == [0, 0, 0], report["c_extend_series"]
        finally:
            teardown(connector, mock)


# ── 测试 6：Γ脆判据① 语义死亡（预置 2 条 deprecated）────────


def test_criterion1_semantic_death():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        engine = AxiomEngine()
        validator = RuleValidator()
        defs = {a["axiom_id"]: a for a in validator.get_axioms()}
        engine.add_axiom("A_M1", defs["A_M1"]["hypothesis"], defs["A_M1"]["anchor"],
                         status="deprecated")
        engine.add_axiom("A_W1", defs["A_W1"]["hypothesis"], defs["A_W1"]["anchor"],
                         status="deprecated")
        engine.add_axiom("A_W2_5", defs["A_W2_5"]["hypothesis"],
                         defs["A_W2_5"]["anchor"])
        env = setup_env(20793, log_path, zombies=True, engine=engine)
        connector, mock = env[0], env[1]
        try:
            runner = make_runner(env, rounds=8)
            report = runner.run()
            assert report["disintegrated"] is True, report
            assert report["criterion"] == "semantic_death", report["criterion"]
            assert report["completed"] == 3, \
                f"A_W2_5 downgrades at r2 → stop, completed={report['completed']}"
            assert report["virtual_lifespan"] == 3
            assert all(s == "deprecated"
                       for s in report["axiom_final_status"].values()), \
                report["axiom_final_status"]
        finally:
            teardown(connector, mock)


# ── 测试 7：归档可追溯（反套壳铁则 #4）──────────────────────


def test_log_replay_traceability():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20794, log_path, zombies=True)
        connector, mock = env[0], env[1]
        recorder = env[4]
        try:
            runner = make_runner(env, rounds=8)
            runner.inject_axioms_from_w1()
            runner.run()
            rids = recorder.list_rounds()
            assert len(rids) == 8, rids
            for rid in rids:
                entry = recorder.load_round(rid)
                for k in ("w0", "w1", "w2", "w3", "c_h"):
                    assert k in entry, f"round {rid} missing {k}"
                assert "entities" in entry["w0"] and "events" in entry["w0"]
                assert "axioms" in entry["w2"]
                assert "hypotheses" in entry["w3"]
                assert "counterexample_processing" in entry["w3"]
            # r2：降级事件归档 + 反例假设携带 R_W2_1 + 累计反例=5
            e2 = recorder.load_round(2)
            dge = e2["w3"].get("downgrade_events", [])
            assert any(ev.get("axiom_id") == "A_W2_5" and ev.get("count") == 5
                       for ev in dge), dge
            ces = [h for h in e2["w3"]["hypotheses"] if h.get("is_counterexample")]
            assert ces, "r2 must archive counterexample hypotheses"
            assert any("R_W2_1" in h.get("violated_rules", []) for h in ces), ces
            assert e2["w2"]["cumulative_counterexamples"] == 5, e2["w2"]
            # r7：饱和后不再新增
            e7 = recorder.load_round(7)
            assert e7["w2"]["cumulative_counterexamples"] == 5, e7["w2"]
        finally:
            teardown(connector, mock)


# ── 测试 8：公理注入（W1 → W2）─────────────────────────────


def test_inject_axioms_from_w1():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20795, log_path, zombies=False)
        connector, mock = env[0], env[1]
        engine, validator = env[6], env[5]
        try:
            runner = make_runner(env, rounds=1)
            runner.inject_axioms_from_w1()
            axioms = engine.get_all_axioms()
            assert len(axioms) == 3, f"expected 3 axioms, got {len(axioms)}"
            ids = {a["id"] for a in axioms}
            assert ids == {"A_M1", "A_W1", "A_W2_5"}, ids
            for a in axioms:
                assert a["status"] == "active", a
                w1_def = {x["axiom_id"]: x for x in validator.get_axioms()}[a["id"]]
                assert a["anchor"] == w1_def["anchor"], a
        finally:
            teardown(connector, mock)


# ── 测试 9：from_config 装配 ────────────────────────────────


def test_from_config_wiring():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        connector = GameConnector(port=20796)
        connector.start()
        mock = LoopMock(port=20796, zombies=True)
        mock.connect_with_retry()
        try:
            assert connector.wait_for_game(timeout=5.0)
            sampler = DataSampler(connector)
            executor = ActionExecutor.from_config(connector, path=None)
            recorder = LogRecorder(log_path)
            runner = ClosedLoopRunner.from_config(
                connector, sampler, executor, recorder,
                config_path=str(CONFIG_PATH), rounds=3)
            assert runner.functional_death_k == 3, runner.functional_death_k
            assert runner.h_l_window_n == 5, runner.h_l_window_n
            axioms = runner.axiom_engine.get_all_axioms()
            assert len(axioms) == 3, f"from_config must inject 3 axioms, got {len(axioms)}"
            report = runner.run()
            assert report["completed"] == 3, report
            assert report["disintegrated"] is False, report
            assert report["interruption"] is False, report["interruption_reason"]
        finally:
            connector.stop()
            mock.close()


# ── 测试 10：LLM 辅助接入闭环（审计 A1）──────────────────────


class FakeAdapter:
    """mock 模型适配器：loaded=True + 固定 generate_text（供接入测试）。"""

    def __init__(self, text="闭环LLM辅助测试文本。"):
        self.loaded = True
        self._text = text

    def generate_text(self, prompt):
        return self._text


def test_llm_advisory_notes_in_loop():
    from w2.llm_assistant import LLMAssistant
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20797, log_path, zombies=True)
        connector, mock = env[0], env[1]
        recorder = env[4]
        try:
            llm = LLMAssistant(use_model=True,
                               model_adapter=FakeAdapter())
            runner = make_runner(env, rounds=3)
            runner.inject_axioms_from_w1()
            runner.llm_assistant = llm
            report = runner.run()
            assert report["completed"] == 3, report

            # —— advisory_notes 归档且来源齐全 ——
            e0 = recorder.load_round(0)
            notes0 = e0["w3"].get("advisory_notes") or []
            assert len(notes0) >= 1, notes0
            assert any(n["source"] == "hypothesis_nl"
                       and "闭环LLM辅助测试文本"
                           in n["output"]["text"]
                       for n in notes0), notes0
            # r1 触发 warn → summarize_downgrade 出现
            e1 = recorder.load_round(1)
            notes1 = e1["w3"].get("advisory_notes") or []
            assert any(n["source"] == "summarize_downgrade"
                       for n in notes1), notes1

            # —— 判定隔离：advisory 不参与任何裁决，轨迹与无 LLM 一致 ——
            # 3 轮 zombies：r0 双门控 count=1 → r1 warn count=3 → r2 降级 count=5
            assert report["axiom_final_status"]["A_W2_5"] == "deprecated", \
                report["axiom_final_status"]
            assert report["c_extend_series"] == [1, 3, 5], \
                report["c_extend_series"]
        finally:
            teardown(connector, mock)


# ── 测试 11：F3 安全检查——state_fn 抛错不中断、无伪高分 ──────


def test_model_state_unavailable_marks_round():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20798, log_path, zombies=False)
        connector, mock = env[0], env[1]
        try:
            def broken_state_fn(round_index, ctx):
                raise RuntimeError("simulated CUDA OOM")

            runner = make_runner(env, rounds=3, state_fn=broken_state_fn)
            runner.inject_axioms_from_w1()
            report = runner.run()
            # F3 双层防御：轮不中断，实验跑满
            assert report["interruption"] is False, report
            assert report["completed"] == 3, report
            # 无伪高分：每轮对齐分 None + 显式标记
            for rd in report["round_details"]:
                assert rd["alignment_score"] is None, rd
                assert rd["model_state_unavailable"] is True, rd
            # QS 历史为空（未追加任何 1.0 伪分）
            assert report["qs_history"] == [], report["qs_history"]
        finally:
            teardown(connector, mock)


def test_system_untrusted_marks_round():
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "loop.jsonl"
        env = setup_env(20799, log_path, zombies=False)
        connector, mock = env[0], env[1]
        try:
            def overflow_state_fn(round_index, ctx):
                raise ContextOverflowError(
                    "context overflow: 600 tokens > max_length 512")

            runner = make_runner(env, rounds=3, state_fn=overflow_state_fn)
            runner.inject_axioms_from_w1()
            report = runner.run()
            # 前置条件②破坏：轮不中断，实验跑满
            assert report["interruption"] is False, report
            assert report["completed"] == 3, report
            # 每轮显式标记「系统不可信」，区别于「系统不可用」
            for rd in report["round_details"]:
                assert rd["system_untrusted"] is True, rd
                assert rd["model_state_unavailable"] is False, rd
                assert rd["alignment_score"] is None, rd
            # 对齐分不可采信 → 不进 QS 历史（不进判据②）
            assert report["qs_history"] == [], report["qs_history"]
        finally:
            teardown(connector, mock)


if __name__ == "__main__":
    check("embedding_determinism", test_embedding_determinism)
    check("disintegration_config", test_disintegration_config)
    check("full_loop_downgrade_and_secondary_convergence",
          test_full_loop_downgrade_and_secondary_convergence)
    check("criterion3_over_budget", test_criterion3_over_budget)
    check("criterion2_functional_death", test_criterion2_functional_death)
    check("criterion1_semantic_death", test_criterion1_semantic_death)
    check("log_replay_traceability", test_log_replay_traceability)
    check("inject_axioms_from_w1", test_inject_axioms_from_w1)
    check("from_config_wiring", test_from_config_wiring)
    check("llm_advisory_notes_in_loop", test_llm_advisory_notes_in_loop)
    check("model_state_unavailable_marks_round",
          test_model_state_unavailable_marks_round)
    check("system_untrusted_marks_round",
          test_system_untrusted_marks_round)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
