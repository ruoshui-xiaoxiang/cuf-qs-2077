"""S8 W1 规则库脱机自测。

验收标准：脱离游戏与 LLM，输入任意标签输出稳定真值。
覆盖：get_ground_truth 各规则、is_valid_counterexample 三条公理反例判定、
      降级保护（标签组合多样性）、未知 axiom_id 处理。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from w1.rule_validator import RuleValidator

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    rv = RuleValidator()

    # ── 1. 规则库加载 ──
    print("\n=== 1. 规则库加载 ===")
    axioms = rv.get_axioms()
    check("axioms count == 3", len(axioms) == 3, f"got {len(axioms)}")
    check("A_M1 present", any(a["axiom_id"] == "A_M1" for a in axioms))
    check("A_W2_5 present", any(a["axiom_id"] == "A_W2_5" for a in axioms))
    check("A_W1 present", any(a["axiom_id"] == "A_W1" for a in axioms))

    rules = rv.get_rules()
    check("rules count == 20", len(rules) == 20, f"got {len(rules)}")
    check("scan_radius == 50.0", rv.scan_radius == 50.0)
    check("scan_limit == 30", rv.scan_limit == 30)

    # ── 2. 元数据字段完整性 ──
    print("\n=== 2. 元数据字段完整性 ===")
    required_meta = ["rule_id", "axiom_id", "description", "conditions",
                     "ground_truth", "verified_version", "confidence", "sample_count"]
    for r in rules:
        missing = [f for f in required_meta if f not in r]
        check(f"{r['rule_id']} meta complete", not missing,
              f"missing: {missing}")

    # ── 3. get_ground_truth: combat_attack + npc ──
    print("\n=== 3. get_ground_truth: combat_attack + npc ===")
    q = {
        "entity": {"kind": "npc", "labels": ["hostile"], "hp": 50},
        "event": {"kind": "combat_attack", "target_entity": "EID:123"},
        "context": {"hp_before": 80, "hp_after": 50}
    }
    gt = rv.get_ground_truth(q)
    check("truths found", len(gt["truths"]) > 0)
    rids = [t["rule_id"] for t in gt["truths"]]
    check("R_M1_1 matched", "R_M1_1" in rids)
    check("R_M1_2 matched", "R_M1_2" in rids)
    check("R_M1_3 matched", "R_M1_3" in rids)
    check("R_M1_4 matched", "R_M1_4" in rids)
    check("R_M1_2 matches (hp decreased)", any(
        t["rule_id"] == "R_M1_2" and t["matches"] for t in gt["truths"]))

    # ── 4. get_ground_truth: hp=0 entity ──
    print("\n=== 4. get_ground_truth: hp=0 entity ===")
    q2 = {
        "entity": {"kind": "npc", "labels": ["hostile"], "hp": 0},
        "event": {"kind": "entity_death"},
        "context": {}
    }
    gt2 = rv.get_ground_truth(q2)
    rids2 = [t["rule_id"] for t in gt2["truths"]]
    check("R_W2_1 matched (should_die)", "R_W2_1" in rids2)
    check("R_W2_2 matched (entity_death)", "R_W2_2" in rids2)

    # ── 5. get_ground_truth: distance check ──
    print("\n=== 5. get_ground_truth: distance ===")
    q3 = {
        "entity": {"kind": "npc", "labels": ["civilian"], "hp": 100},
        "event": {},
        "context": {"distance_to_player": 15.0}
    }
    gt3 = rv.get_ground_truth(q3)
    rids3 = [t["rule_id"] for t in gt3["truths"]]
    check("R_W1_1 matched (beyond radius)", "R_W1_1" in rids3)
    check("R_W1_2 matched (within radius)", "R_W1_2" in rids3)

    # player always visible
    q4 = {
        "entity": {"kind": "player", "labels": ["player"], "hp": 100},
        "event": {},
        "context": {"distance_to_player": 0}
    }
    gt4 = rv.get_ground_truth(q4)
    rids4 = [t["rule_id"] for t in gt4["truths"]]
    check("R_W1_3 matched (player)", "R_W1_3" in rids4)

    # ── 6. is_valid_counterexample: A_M1 valid (hp not decreased) ──
    print("\n=== 6. is_valid_counterexample: A_M1 ===")
    obs = {
        "entity": {"entity_id": "EID:123", "kind": "npc", "labels": ["hostile"], "hp": 80},
        "events": [{"kind": "combat_attack", "target_entity": "EID:123"}],
        "context": {"hp_before": 80, "hp_after": 80},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce = rv.is_valid_counterexample("A_M1", obs)
    check("A_M1 violation found", len(ce["violated_rules"]) > 0)
    check("A_M1 label diversity OK", ce["label_diversity_ok"] is True)
    check("A_M1 is_valid", ce["is_valid"] is True, ce["reason"])

    # ── 7. is_valid_counterexample: A_M1 invalid (no violation) ──
    print("\n=== 7. is_valid_counterexample: A_M1 no violation ===")
    obs_ok = {
        "entity": {"entity_id": "EID:456", "kind": "npc", "labels": ["hostile"], "hp": 50},
        "events": [{"kind": "combat_attack", "target_entity": "EID:456"}],
        "context": {"hp_before": 80, "hp_after": 50},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_ok = rv.is_valid_counterexample("A_M1", obs_ok)
    check("A_M1 no violation", len(ce_ok["violated_rules"]) == 0)
    check("A_M1 not valid (no violation)", ce_ok["is_valid"] is False)

    # ── 7.5 is_valid_counterexample: A_M1 R_M1_5 误伤平民 ──
    print("\n=== 7.5 is_valid_counterexample: A_M1 civilian attack ===")
    obs_civ = {
        "entity": {"entity_id": "EID:C1", "kind": "npc",
                   "labels": ["civilian"], "hp": 50},
        "events": [{"kind": "combat_attack", "target_entity": "EID:C1"}],
        "context": {"hp_before": 50, "hp_after": 0},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_civ = rv.is_valid_counterexample("A_M1", obs_civ)
    rids_civ = [v.get("rule_id") for v in ce_civ["violated_rules"]]
    check("R_M1_5 triggered on civilian", "R_M1_5" in rids_civ, rids_civ)
    check("R_M1_5 is_valid", ce_civ["is_valid"] is True, ce_civ["reason"])

    # ── 8. is_valid_counterexample: A_M1 label diversity insufficient ──
    print("\n=== 8. is_valid_counterexample: A_M1 label diversity ===")
    obs_single = {
        "entity": {"entity_id": "EID:789", "kind": "npc", "labels": ["hostile"], "hp": 80},
        "events": [{"kind": "combat_attack", "target_entity": "EID:789"}],
        "context": {"hp_before": 80, "hp_after": 80},
        "label_history": [["hostile"]]
    }
    ce_single = rv.is_valid_counterexample("A_M1", obs_single)
    check("A_M1 violation found", len(ce_single["violated_rules"]) > 0)
    check("A_M1 label diversity NOT OK", ce_single["label_diversity_ok"] is False)
    check("A_M1 not valid (single label)", ce_single["is_valid"] is False,
          ce_single["reason"])

    # ── 9. is_valid_counterexample: A_W2_5 valid (hp<=0 no death event) ──
    print("\n=== 9. is_valid_counterexample: A_W2_5 ===")
    obs_w2 = {
        "entity": {"entity_id": "EID:999", "kind": "npc", "labels": ["hostile"], "hp": 0},
        "events": [],
        "context": {"entity_in_later_scan": True},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_w2 = rv.is_valid_counterexample("A_W2_5", obs_w2)
    check("A_W2_5 R_W2_1 violated (no death event)", any(
        v["rule_id"] == "R_W2_1" for v in ce_w2["violated_rules"]))
    check("A_W2_5 R_W2_2 violated (still in scan)", any(
        v["rule_id"] == "R_W2_2" for v in ce_w2["violated_rules"]))
    check("A_W2_5 is_valid", ce_w2["is_valid"] is True, ce_w2["reason"])

    # ── 10. is_valid_counterexample: A_W2_5 no violation ──
    print("\n=== 10. is_valid_counterexample: A_W2_5 no violation ===")
    obs_w2_ok = {
        "entity": {"entity_id": "EID:888", "kind": "npc", "labels": ["hostile"], "hp": 0},
        "events": [{"kind": "entity_death", "target_entity": "EID:888"}],
        "context": {"entity_in_later_scan": False},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_w2_ok = rv.is_valid_counterexample("A_W2_5", obs_w2_ok)
    check("A_W2_5 no violation", len(ce_w2_ok["violated_rules"]) == 0)
    check("A_W2_5 not valid", ce_w2_ok["is_valid"] is False)

    # ── 11. is_valid_counterexample: A_W1 valid (beyond radius in scan) ──
    print("\n=== 11. is_valid_counterexample: A_W1 ===")
    obs_w1 = {
        "entity": {"entity_id": "EID:777", "kind": "npc", "labels": ["hostile"], "hp": 100},
        "events": [],
        "context": {"distance_to_player": 80.0, "entity_in_scan": True},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_w1 = rv.is_valid_counterexample("A_W1", obs_w1)
    check("A_W1 R_W1_1 violated", any(
        v["rule_id"] == "R_W1_1" for v in ce_w1["violated_rules"]),
        str(ce_w1["violated_rules"]))
    check("A_W1 is_valid", ce_w1["is_valid"] is True, ce_w1["reason"])

    # ── 12. is_valid_counterexample: A_W1 within radius not in scan ──
    print("\n=== 12. is_valid_counterexample: A_W1 missing ===")
    obs_w1_missing = {
        "entity": {"entity_id": "EID:666", "kind": "npc", "labels": ["hostile"], "hp": 100},
        "events": [],
        "context": {"distance_to_player": 10.0, "entity_in_scan": False},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_w1_missing = rv.is_valid_counterexample("A_W1", obs_w1_missing)
    check("A_W1 R_W1_2 violated (not in scan)", any(
        v["rule_id"] == "R_W1_2" for v in ce_w1_missing["violated_rules"]))
    check("A_W1 is_valid (missing)", ce_w1_missing["is_valid"] is True)

    # ── 13. is_valid_counterexample: unknown axiom_id ──
    print("\n=== 13. is_valid_counterexample: unknown axiom ===")
    ce_unknown = rv.is_valid_counterexample("A_UNKNOWN", {
        "entity": {}, "events": [], "context": {}, "label_history": []
    })
    check("unknown axiom not valid", ce_unknown["is_valid"] is False)
    check("unknown axiom reason", "unknown" in ce_unknown["reason"].lower())

    # ── 14. 辅助函数 ──
    print("\n=== 14. 辅助函数 ===")
    d = rv.compute_distance({"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 4, "z": 0})
    check("distance == 5.0", abs(d - 5.0) < 0.001, f"got {d}")
    check("within scan (10m)", rv.is_within_scan(
        {"x": 0, "y": 0, "z": 0}, {"x": 10, "y": 0, "z": 0}) is True)
    check("beyond scan (80m)", rv.is_within_scan(
        {"x": 0, "y": 0, "z": 0}, {"x": 80, "y": 0, "z": 0}) is False)

    # ── 15. W1 只读性验证（反套壳铁则 #2）──
    print("\n=== 15. W1 只读性 ===")
    rules_before = rv.get_rules()
    rv._rules.append({"rule_id": "HACKED"})
    rv2 = RuleValidator()
    check("reload from disk restores rules", len(rv2.get_rules()) == 20,
          f"got {len(rv2.get_rules())}")

    # ── 16. 审计修复回归：matches 真实比对 + observable 语义 ──
    print("\n=== 16. 审计修复回归：get_ground_truth matches/observable ===")

    # R_W2_1: hp=0 但实体仍在后续扫描中 → 违反（matches=False）
    gt_w21_bad = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["hostile"], "hp": 0},
        "event": {},
        "context": {"entity_in_later_scan": True}
    })
    t_w21_bad = next(t for t in gt_w21_bad["truths"] if t["rule_id"] == "R_W2_1")
    check("R_W2_1 hp=0 still present → matches False", t_w21_bad["matches"] is False)
    check("R_W2_1 observable", t_w21_bad["observable"] is True)

    # R_W2_1: hp=0 且有 entity_death 事件 → 符合（有实证）
    gt_w21_ok = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["hostile"], "hp": 0},
        "event": {"kind": "entity_death"},
        "context": {}
    })
    t_w21_ok = next(t for t in gt_w21_ok["truths"] if t["rule_id"] == "R_W2_1")
    check("R_W2_1 hp=0 + death event → matches True", t_w21_ok["matches"] is True)
    check("R_W2_1 death evidence observable", t_w21_ok["observable"] is True)

    # R_W2_1: hp>0 → 前件未触发，空满足且不可观测
    gt_w21_vac = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["hostile"], "hp": 50},
        "event": {},
        "context": {}
    })
    t_w21_vac = next(t for t in gt_w21_vac["truths"] if t["rule_id"] == "R_W2_1")
    check("R_W2_1 hp>0 vacuous True", t_w21_vac["matches"] is True)
    check("R_W2_1 hp>0 not observable", t_w21_vac["observable"] is False)

    # R_W2_2: entity_death 后仍在扫描中 → 违反
    gt_w22_bad = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["hostile"], "hp": 0},
        "event": {"kind": "entity_death"},
        "context": {"entity_in_later_scan": True}
    })
    t_w22_bad = next(t for t in gt_w22_bad["truths"] if t["rule_id"] == "R_W2_2")
    check("R_W2_2 still in scan → matches False", t_w22_bad["matches"] is False)

    # R_W2_2: 死亡后不在扫描中 → 符合
    gt_w22_ok = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["hostile"], "hp": 0},
        "event": {"kind": "entity_death"},
        "context": {"entity_in_later_scan": False}
    })
    t_w22_ok = next(t for t in gt_w22_ok["truths"] if t["rule_id"] == "R_W2_2")
    check("R_W2_2 gone from scan → matches True", t_w22_ok["matches"] is True)
    check("R_W2_2 observable", t_w22_ok["observable"] is True)

    # R_W1_1: 超出半径却出现在扫描中 → 违反
    gt_w11_bad = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["civilian"], "hp": 100},
        "event": {},
        "context": {"distance_to_player": 80.0, "entity_in_scan": True}
    })
    t_w11_bad = next(t for t in gt_w11_bad["truths"] if t["rule_id"] == "R_W1_1")
    check("R_W1_1 beyond but in scan → matches False", t_w11_bad["matches"] is False)

    # R_W1_1: 超出半径且不在扫描中 → 符合（有实证）
    gt_w11_ok = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["civilian"], "hp": 100},
        "event": {},
        "context": {"distance_to_player": 80.0, "entity_in_scan": False}
    })
    t_w11_ok = next(t for t in gt_w11_ok["truths"] if t["rule_id"] == "R_W1_1")
    check("R_W1_1 beyond and not in scan → matches True", t_w11_ok["matches"] is True)
    check("R_W1_1 observable", t_w11_ok["observable"] is True)

    # R_W1_2: 半径内却不在扫描中 → 违反
    gt_w12_bad = rv.get_ground_truth({
        "entity": {"kind": "npc", "labels": ["civilian"], "hp": 100},
        "event": {},
        "context": {"distance_to_player": 10.0, "entity_in_scan": False}
    })
    t_w12_bad = next(t for t in gt_w12_bad["truths"] if t["rule_id"] == "R_W1_2")
    check("R_W1_2 within but not in scan → matches False", t_w12_bad["matches"] is False)

    # R_W1_3: 玩家不在扫描中 → 违反
    gt_w13_bad = rv.get_ground_truth({
        "entity": {"kind": "player", "labels": ["player"], "hp": 100},
        "event": {},
        "context": {"entity_in_scan": False}
    })
    t_w13_bad = next(t for t in gt_w13_bad["truths"] if t["rule_id"] == "R_W1_3")
    check("R_W1_3 player not in scan → matches False", t_w13_bad["matches"] is False)

    # ── 17. 审计修复回归：伪反例删除 + hp 检查去重 ──
    print("\n=== 17. 审计修复回归：A_M1 伪反例/去重 ===")

    # 无攻击事件的观测（即使带 hp 上下文）不构成 A_M1 反例
    obs_no_attack = {
        "entity": {"entity_id": "EID:555", "kind": "npc", "labels": ["hostile"], "hp": 80},
        "events": [],
        "context": {"hp_before": 80, "hp_after": 80},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_no_attack = rv.is_valid_counterexample("A_M1", obs_no_attack)
    check("no attack event → no violations", len(ce_no_attack["violated_rules"]) == 0,
          str(ce_no_attack["violated_rules"]))
    check("no attack event → not valid counterexample", ce_no_attack["is_valid"] is False)

    # 多个攻击事件 + hp 未下降 → R_M1_2 只记一次（不重复累加）
    obs_multi = {
        "entity": {"entity_id": "EID:556", "kind": "npc", "labels": ["hostile"], "hp": 80},
        "events": [
            {"kind": "combat_attack", "target_entity": "EID:556"},
            {"kind": "combat_attack", "target_entity": "EID:556"},
            {"kind": "combat_attack", "target_entity": "EID:556"}
        ],
        "context": {"hp_before": 80, "hp_after": 80},
        "label_history": [["hostile"], ["civilian"]]
    }
    ce_multi = rv.is_valid_counterexample("A_M1", obs_multi)
    n_m1_2 = sum(1 for v in ce_multi["violated_rules"] if v["rule_id"] == "R_M1_2")
    check("multi events → R_M1_2 counted once", n_m1_2 == 1, f"got {n_m1_2}")
    check("multi events → still valid counterexample", ce_multi["is_valid"] is True)

    # ── 总结 ──
    print(f"\n{'='*50}")
    print(f"S8 W1 规则库自测: {PASS} passed, {FAIL} failed")
    print(f"{'='*50}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
