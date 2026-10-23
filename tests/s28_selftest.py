"""S28 候选公理自主激活机制离线自测。

契约（S28 锁定）：
- 候选公理池（world_rules.candidate_axioms）不直接注入引擎；
  ActivationEngine 依据观测证据（事件计数 + 滑动窗口）自主激活。
- 激活 ≠ 宣告真/假：只做"值得验证"的选择；激活后与现有公理同权
  参与（真值判定仍归 rule_validator）。激活幂等、轮间生效。
- 反套壳：激活判定为硬编码统计阈值，零 LLM。

S29.16（议题3 方案 B，2026-08-26 用户批准）：A_M2/A_S1/A_I1 三个
基础候选已移出候选池——触发事件不在 W0 观测词表、裁决依赖的
context 主链路从不注入（双重死锁）。激活机制测试改用现存代表
候选 C_G2_GANG_000（entity_appeared 触发，min_count=2/window=4）；
对应规则定义保留在 W1 rules 真值层（本测试落档验证）。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w1.rule_validator import RuleValidator
from w2.axiom_engine import AxiomEngine
from w2.axiom_activator import ActivationEngine

RESULTS = []

# 方案 B 后的候选池构成（15 条代表候选：含 2 条故意错误 + 2 条时序）
POOL_SIZE = 15
TRIAL_CANDIDATE = "C_G2_GANG_000"
REMOVED_BASE_CANDIDATES = {"A_M2", "A_S1", "A_I1"}


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


def make_w0(events):
    return {"events": events, "entities": []}


def appeared_event():
    return {"event_id": "a", "kind": "entity_appeared",
            "source_entity": "EID:1", "target_entity": "EID:1",
            "game_ts": 1.0}


def make_engine():
    rv = RuleValidator()
    ae = AxiomEngine()
    act = ActivationEngine(rv, ae)
    return rv, ae, act


# ── 测试 1：候选池加载（方案 B 后 15 条）────────────────────


def test_candidate_pool_loaded():
    rv, _, _ = make_engine()
    cands = rv.get_candidates()
    ids = {c["axiom_id"] for c in cands}
    assert len(ids) == POOL_SIZE, f"expected {POOL_SIZE} candidates, got {len(ids)}"
    # S29.16 方案 B：三个基础候选不在池内（观测词表不可表达）
    assert not (REMOVED_BASE_CANDIDATES & ids), \
        f"removed base candidates must stay out: {REMOVED_BASE_CANDIDATES & ids}"
    for c in cands:
        tr = c["activation_trigger"]
        assert "event_kinds" in tr and "min_count" in tr \
            and "window_rounds" in tr, c
        assert tr["min_count"] >= 1 and tr["window_rounds"] >= 1, c
        assert c["hypothesis"] and c["anchor"], c
    # 故意错误规则标记存在（用于证伪实验对照）
    n_err = sum(1 for c in cands if c.get("intentional_error"))
    assert n_err == 2, f"expected 2 intentional errors, got {n_err}"
    # 候选不直接注入引擎
    _, ae, _ = make_engine()
    assert ae.get_all_axioms() == [], "candidates must not be injected"
    # 激活状态初始：0 激活 / 15 未激活
    _, _, act = make_engine()
    st = act.get_status()
    assert st["activated"] == [] and len(st["inactive"]) == POOL_SIZE, st


# ── 测试 2：激活触发（窗口计数达阈值）────────────────────────


def test_activation_trigger():
    rv, ae, act = make_engine()
    # C_G2_GANG_000：min_count=2, window=4 —— 2 轮各 1 次
    # entity_appeared → 第 2 轮 end_round 激活。注意：多条候选共享
    # entity_appeared 触发事件，同一证据流会并行激活全部达标候选
    # （引擎正常语义），本测试只断言目标候选在列且证据计数正确。
    activated = []
    for r in range(2):
        act.feed(make_w0([appeared_event()]), r)
        activated += act.end_round()
    by_id = {a["axiom_id"]: a for a in activated}
    assert TRIAL_CANDIDATE in by_id, \
        f"{TRIAL_CANDIDATE} not activated: {sorted(by_id)}"
    assert by_id[TRIAL_CANDIDATE]["evidence_count"] == 2, activated
    assert ae.get_axiom(TRIAL_CANDIDATE) is not None, "must be injected"
    assert ae.get_axiom(TRIAL_CANDIDATE)["status"] == "active"


# ── 测试 3：轮间生效（end_round 前不激活）────────────────────


def test_activation_between_rounds():
    rv, ae, act = make_engine()
    # 第 1 轮 1 事件不足阈值（min_count=2）→ 不激活
    act.feed(make_w0([appeared_event()]), 0)
    assert act.end_round() == [], "must not activate before threshold"
    assert ae.get_axiom(TRIAL_CANDIDATE) is None
    # 第 2 轮达标 → end_round 激活（轮间生效语义）
    act.feed(make_w0([appeared_event()]), 1)
    act.end_round()
    assert ae.get_axiom(TRIAL_CANDIDATE) is not None
    # 跨轮窗口累计：2 空轮 + 1 轮 2 事件（window=4 内累计 ≥2）→ 激活
    rv2, ae2, act2 = make_engine()
    act2.feed(make_w0([]), 0)
    act2.end_round()
    act2.feed(make_w0([]), 1)
    act2.end_round()
    act2.feed(make_w0([appeared_event()] * 2), 2)
    act2.end_round()
    assert ae2.get_axiom(TRIAL_CANDIDATE) is not None, \
        "window accumulates across rounds"


# ── 测试 4：幂等（已激活不重复）─────────────────────────────


def test_activation_idempotent():
    rv, ae, act = make_engine()
    for r in range(4):
        act.feed(make_w0([appeared_event()]), r)
        act.end_round()
    assert ae.get_axiom(TRIAL_CANDIDATE) is not None
    n_events = len(act.get_activation_events())
    # 再喂更多证据事件，不再重复激活
    act.feed(make_w0([appeared_event()] * 5), 5)
    act.end_round()
    assert len(act.get_activation_events()) == n_events, \
        "activation must be idempotent"
    assert ae.get_axiom(TRIAL_CANDIDATE)["counterexample_count"] == 0


# ── 测试 5：无证据不激活 ────────────────────────────────────


def test_no_evidence_no_activation():
    rv, ae, act = make_engine()
    for r in range(6):
        act.feed(make_w0([]), r)  # 无任何候选事件
        act.end_round()
    st = act.get_status()
    assert st["activated"] == [], st
    assert len(st["inactive"]) == POOL_SIZE, st
    assert ae.get_all_axioms() == [], "no axiom should be injected"
    # 事件种类不匹配（如 hp_changed）也不激活
    rv2, ae2, act2 = make_engine()
    for r in range(5):
        act2.feed(make_w0([{"event_id": "h", "kind": "hp_changed",
                            "source_entity": "EID:1",
                            "target_entity": "EID:1", "game_ts": 1.0}]), r)
        act2.end_round()
    assert ae2.get_all_axioms() == [], "unrelated events must not activate"


# ── 测试 6：激活后正常参与（候选断言路由可证伪）──────────────


def test_activated_axiom_participates():
    rv, ae, act = make_engine()
    for r in range(2):
        act.feed(make_w0([appeared_event()]), r)
        act.end_round()
    a = ae.get_axiom(TRIAL_CANDIDATE)
    assert a is not None
    assert a["hypothesis"] and a["anchor"] == "P_GANG_HOSTILE", a
    # 可生成假设（走候选断言路由：maelstrom 应携带 hostile）
    base_obs = {"entity": {"entity_id": "EID:N1", "kind": "npc",
                           "labels": ["maelstrom"], "hp": 100,
                           "position": {"x": 1, "y": 0, "z": 0}},
                "events": [],
                "context": {},
                "label_history": [["maelstrom"], ["civilian"]]}
    # 标签符合断言（带 hostile）→ 无违规、非反例
    obs_ok = dict(base_obs)
    obs_ok["entity"] = dict(base_obs["entity"],
                            labels=["maelstrom", "hostile"])
    hyp = ae.generate_hypothesis(TRIAL_CANDIDATE, obs_ok, rule_validator=rv)
    assert hyp.get("is_counterexample") is False, hyp
    # 缺 hostile → 候选断言违反 → 构成反例（可证伪性成立）
    rv_res = rv.is_valid_counterexample(TRIAL_CANDIDATE, base_obs)
    assert rv_res["is_valid"] is True, rv_res
    assert any(v["rule_id"] == TRIAL_CANDIDATE
               for v in rv_res["violated_rules"]), rv_res


# ── 测试 7：方案 B 落档——移除候选的规则仍在 W1 真值层 ──────


def test_removed_candidates_rules_kept_in_w1():
    rules_path = (Path(__file__).resolve().parents[1] / "src" / "w1"
                  / "world_rules.json")
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    rule_ids = {r["rule_id"] for r in data["rules"]}
    kept = {"R_M2_1", "R_M2_2", "R_S1_1", "R_S1_2",
            "R_I1_1", "R_I1_2", "R_I1_3"}
    assert kept <= rule_ids, f"W1 must keep rules: {kept - rule_ids}"
    cand_ids = {c["axiom_id"] for c in data["candidate_axioms"]}
    assert not (REMOVED_BASE_CANDIDATES & cand_ids), cand_ids


# ── 测试 8：反套壳——激活引擎零 LLM / 零模型 ─────────────────


def test_no_model_imports():
    src = (Path(__file__).resolve().parents[1] / "src" / "w2"
           / "axiom_activator.py").read_text(encoding="utf-8")
    imports = [ln.strip() for ln in src.splitlines()
               if re.match(r"^\s*(import|from)\s", ln)]
    banned = ("torch", "transformers", "model_adapter", "llm_assistant",
              "bitsandbytes")
    for ln in imports:
        for word in banned:
            assert word not in ln, \
                f"axiom_activator must not import {word}: {ln}"


if __name__ == "__main__":
    check("candidate_pool_loaded", test_candidate_pool_loaded)
    check("activation_trigger", test_activation_trigger)
    check("activation_between_rounds", test_activation_between_rounds)
    check("activation_idempotent", test_activation_idempotent)
    check("no_evidence_no_activation", test_no_evidence_no_activation)
    check("activated_axiom_participates", test_activated_axiom_participates)
    check("removed_candidates_rules_kept_in_w1",
          test_removed_candidates_rules_kept_in_w1)
    check("no_model_imports", test_no_model_imports)

    passed = sum(1 for _, ok, _ in RESULTS)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
