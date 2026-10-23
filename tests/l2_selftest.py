"""S29.9 L2 跨轮时序断言自测（200 候选池版本）。

L2 = 候选规则可声明跨轮时序语义（verification 模式），runner 维护
实体跨轮历史快照，rule_validator 据此判定时序违反。

验证（不依赖完整闭环，避免 200 候选池的反例爆炸干扰）：
1. 单元级——直接测 rule_validator._check_candidate_verification 的
   时序判定（disappear_within / persist_min / event_effect）正确触发。
2. 激活级——200 候选池中时序候选（C_G2_SUPP_TMP_*/C_G2_SUPP_STATUS_*）
   可被事件流激活（L2 能力已接入候选池）。

运行：py tests/l2_selftest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w1.rule_validator import RuleValidator

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


def _check(cand, entity, events, context):
    rv = RuleValidator()
    return rv._check_candidate_verification(cand, entity, events, context)


def test_unit_verification():
    rv = RuleValidator()
    entity = {"entity_id": "EID:V", "kind": "npc", "labels": ["hostile"]}
    # disappear_within：6 轮未消失 → 违反；3 轮内消失 → 不违反
    v1 = rv._check_candidate_verification(
        {"axiom_id": "T1", "verification": {"kind": "temporal",
                                            "condition": "disappear_within",
                                            "window_rounds": 5}},
        entity, [], {"first_seen_round": 0, "rounds_since_first_seen": 6,
                     "disappeared": False, "entity_in_later_scan": True})
    assert v1 and v1["rule_id"] == "T1", v1
    v1ok = rv._check_candidate_verification(
        {"axiom_id": "T1", "verification": {"kind": "temporal",
                                            "condition": "disappear_within",
                                            "window_rounds": 5}},
        entity, [], {"first_seen_round": 0, "rounds_since_first_seen": 3,
                     "disappeared": True, "entity_in_later_scan": False})
    assert v1ok is None, v1ok
    # persist_min：1 轮即消失 → 违反；持续 ≥2 → 不违反
    v2 = rv._check_candidate_verification(
        {"axiom_id": "T2", "verification": {"kind": "temporal",
                                            "condition": "persist_min",
                                            "min_rounds": 2}},
        entity, [], {"first_seen_round": 0, "rounds_since_first_seen": 1,
                     "disappeared": True, "entity_in_later_scan": False})
    assert v2 and v2["rule_id"] == "T2", v2
    v2ok = rv._check_candidate_verification(
        {"axiom_id": "T2", "verification": {"kind": "temporal",
                                            "condition": "persist_min",
                                            "min_rounds": 2}},
        entity, [], {"first_seen_round": 0, "rounds_since_first_seen": 3,
                     "disappeared": False, "entity_in_later_scan": True})
    assert v2ok is None, v2ok
    # event_effect：combat_attack 后 hp 未下降 → 违反；hp 下降 → 不违反
    v3 = rv._check_candidate_verification(
        {"axiom_id": "T3", "verification": {"kind": "event_effect",
                                            "event_kind": "combat_attack",
                                            "expect_hp_decrease": True}},
        entity, [{"kind": "combat_attack", "target_entity": "EID:V"}],
        {"hp_before": 80, "hp_after": 80})
    assert v3 and v3["rule_id"] == "T3", v3
    v3ok = rv._check_candidate_verification(
        {"axiom_id": "T3", "verification": {"kind": "event_effect",
                                            "event_kind": "combat_attack",
                                            "expect_hp_decrease": True}},
        entity, [{"kind": "combat_attack", "target_entity": "EID:V"}],
        {"hp_before": 80, "hp_after": 40})
    assert v3ok is None, v3ok
    # 前件未触发（无 combat_attack 事件）→ 不构成违反
    v4 = rv._check_candidate_verification(
        {"axiom_id": "T3", "verification": {"kind": "event_effect",
                                            "event_kind": "combat_attack",
                                            "expect_hp_decrease": True}},
        entity, [], {"hp_before": 80, "hp_after": 80})
    assert v4 is None, v4


def test_loop_activation():
    rv = RuleValidator()
    cands = rv.get_candidates()
    temporal = [c for c in cands if c.get("verification")]
    # 候选池缩减至 15 条后时序候选为 2 条（原 >=20 / C_G2_SUPP_STATUS_*
    # 断言为 200 条池时代旧口径，2026-08-26 同步为现实构成）
    assert len(temporal) == 2, f"expected 2 temporal candidates, got {len(temporal)}"
    assert {c["axiom_id"] for c in temporal} == \
        {"C_G2_LIFE_PERSIST", "C_G2_SUPP_TMP_036"}, \
        [c["axiom_id"] for c in temporal]

    # 激活：entity_appeared/entity_disappeared 事件流驱动时序候选激活
    from w2.axiom_activator import ActivationEngine

    class StubEngine:
        def __init__(self):
            self.added = []

        def add_axiom(self, aid, hyp, anchor, boundary_conditions=None):
            self.added.append(aid)

    eng = StubEngine()
    act = ActivationEngine(rv, eng, enabled=True)
    for r in range(6):
        act.feed({"events": [{"kind": "entity_appeared"},
                             {"kind": "entity_disappeared"},
                             {"kind": "entity_appeared"}]}, r)
        act.end_round()
    temporal_ids = {c["axiom_id"] for c in temporal}
    activated_temporal = [a for a in eng.added if a in temporal_ids]
    assert activated_temporal, "no temporal candidate activated"


def main():
    check("unit_verification", test_unit_verification)
    check("loop_activation", test_loop_activation)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
