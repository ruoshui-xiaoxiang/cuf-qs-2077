"""S10 公理引擎脱机自测。

验收标准：公理注入、查询、更新正常。
覆盖：add_axiom / get_axiom / generate_hypothesis / update_axiom_boundary /
      反例记录与降级判定 / 轮内快照冻结 / W2 数据导出 / log_recorder 对齐。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from w2.axiom_engine import AxiomEngine
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

    # ── 1. 公理注入 ──
    print("\n=== 1. 公理注入 ===")
    engine = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    a1 = engine.add_axiom("A_M1", "攻击NPC会降低HP", "P_M1",
                          boundary_conditions=[{"kind": "npc", "labels": ["hostile"]}])
    check("A_M1 injected", a1["id"] == "A_M1")
    check("A_M1 status active", a1["status"] == "active")
    check("A_M1 has boundary", len(a1["boundary_conditions"]) == 1)
    check("A_M1 anchor", a1["anchor"] == "P_M1")

    a2 = engine.add_axiom("A_W2_5", "HP归零→死亡消失", "P_W2_5",
                          boundary_conditions=[])
    check("A_W2_5 injected", a2["id"] == "A_W2_5")
    check("A_W2_5 empty boundary", len(a2["boundary_conditions"]) == 0)

    a3 = engine.add_axiom("A_W1", "采样范围限制", "P_W1",
                          boundary_conditions=[{"kind": "npc"}])
    check("A_W1 injected", a3["id"] == "A_W1")

    # 重复注入 → 报错
    try:
        engine.add_axiom("A_M1", "dup", "P_M1")
        check("duplicate raises", False, "no exception")
    except ValueError:
        check("duplicate raises", True)

    # ── 2. 查询 ──
    print("\n=== 2. 查询 ===")
    check("get_axiom A_M1", engine.get_axiom("A_M1") is not None)
    check("get_axiom unknown None", engine.get_axiom("NONEXIST") is None)
    check("get_all count == 3", len(engine.get_all_axioms()) == 3)
    check("get_active count == 3", len(engine.get_active_axioms()) == 3)
    check("get_axiom_count == 3", engine.get_axiom_count() == 3)
    check("get_axiom_count active == 3", engine.get_axiom_count(active_only=True) == 3)

    # 返回副本（修改不影响内部）
    a1_copy = engine.get_axiom("A_M1")
    a1_copy["status"] = "HACKED"
    check("get_axiom returns copy", engine.get_axiom("A_M1")["status"] == "active")

    # ── 3. 假设生成：确认观测 ──
    print("\n=== 3. 假设生成：确认观测 ===")
    obs_confirm = {
        "entity": {"entity_id": "EID:123", "kind": "npc", "labels": ["hostile"], "hp": 50},
        "events": [{"kind": "combat_attack", "target_entity": "EID:123"}],
        "context": {"hp_before": 80, "hp_after": 50},
        "label_history": [["hostile"], ["civilian"]]
    }
    h = engine.generate_hypothesis("A_M1", obs_confirm, rule_validator=rv)
    check("hypothesis_id not None", h["hypothesis_id"] is not None)
    check("type confirm", h["type"] == "confirm", h["type"])
    check("not counterexample", h["is_counterexample"] is False)

    # ── 4. 假设生成：反例 ──
    print("\n=== 4. 假设生成：反例 ===")
    obs_ce = {
        "entity": {"entity_id": "EID:456", "kind": "npc", "labels": ["hostile"], "hp": 80},
        "events": [{"kind": "combat_attack", "target_entity": "EID:456"}],
        "context": {"hp_before": 80, "hp_after": 80},
        "label_history": [["hostile"], ["civilian"]]
    }
    h_ce = engine.generate_hypothesis("A_M1", obs_ce, rule_validator=rv)
    check("type counterexample", h_ce["type"] == "counterexample", h_ce["type"])
    check("is_counterexample", h_ce["is_counterexample"] is True)
    check("rule_validation present", h_ce["rule_validation"] is not None)
    check("violated_rules found", len(h_ce["rule_validation"]["violated_rules"]) > 0)

    # ── 5. 假设生成：边界细化建议 ──
    print("\n=== 5. 假设生成：边界细化 ===")
    obs_new_kind = {
        "entity": {"entity_id": "EID:789", "kind": "vehicle", "labels": ["armed"], "hp": 800},
        "events": [{"kind": "combat_attack", "target_entity": "EID:789"}],
        "context": {"hp_before": 1000, "hp_after": 800},
        "label_history": [["hostile"], ["civilian"]]
    }
    h_refine = engine.generate_hypothesis("A_M1", obs_new_kind, rule_validator=rv)
    check("type boundary_refine", h_refine["type"] == "boundary_refine",
          h_refine["type"])

    # ── 6. 边界更新 ──
    print("\n=== 6. 边界更新 ===")
    new_bc = [{"kind": "vehicle", "labels": ["armed"]}]
    updated = engine.update_axiom_boundary("A_M1", new_bc)
    check("boundary updated", len(updated) == 2, f"got {len(updated)}")
    check("vehicle in boundary",
          any(isinstance(bc, dict) and bc.get("kind") == "vehicle"
              for bc in updated))

    # 重复条件不追加
    updated2 = engine.update_axiom_boundary("A_M1", new_bc)
    check("no duplicate boundary", len(updated2) == 2)

    # ── 7. 反例记录与降级 ──
    print("\n=== 7. 反例记录与降级 ===")
    engine2 = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine2.add_axiom("A_M1", "攻击→HP下降", "P_M1",
                      boundary_conditions=[{"kind": "npc"}])

    # 第1次反例：标签多样性不足 → 不记录
    h1 = engine2.generate_hypothesis("A_M1", obs_ce, rule_validator=rv)
    r1 = engine2.record_counterexample("A_M1", h1, label_combo=["hostile"])
    check("ce1 not recorded (single label)", r1["recorded"] is False)
    check("ce1 label_variants == 1", r1["label_variants"] == 1)

    # 第2次反例：不同标签组合 → 记录
    obs_ce2 = {
        "entity": {"entity_id": "EID:555", "kind": "npc", "labels": ["civilian"], "hp": 80},
        "events": [{"kind": "combat_attack", "target_entity": "EID:555"}],
        "context": {"hp_before": 80, "hp_after": 80},
        "label_history": [["hostile"], ["civilian"]]
    }
    h2 = engine2.generate_hypothesis("A_M1", obs_ce2, rule_validator=rv)
    r2 = engine2.record_counterexample("A_M1", h2, label_combo=["civilian"])
    check("ce2 recorded (diverse labels)", r2["recorded"] is True)
    check("ce2 count == 1", r2["count"] == 1)
    check("ce2 label_variants == 2", r2["label_variants"] == 2)
    check("ce2 still active", r2["status"] == "active")

    # 第3次反例 → warn（阈值=3）
    for i in range(2):
        obs_ce_i = {
            "entity": {"entity_id": f"EID:{i}", "kind": "npc", "labels": ["armed"], "hp": 80},
            "events": [{"kind": "combat_attack", "target_entity": f"EID:{i}"}],
            "context": {"hp_before": 80, "hp_after": 80},
            "label_history": [["hostile"], ["civilian"]]
        }
        h_i = engine2.generate_hypothesis("A_M1", obs_ce_i, rule_validator=rv)
        r_i = engine2.record_counterexample("A_M1", h_i, label_combo=["armed"])
    check("ce3 count == 3", r_i["count"] == 3)
    check("ce3 status warned", r_i["new_status"] == "warned",
          f"got {r_i.get('new_status')}")

    # 第5次反例 → deprecated
    for i in range(2):
        obs_ce_j = {
            "entity": {"entity_id": f"EID:{i+10}", "kind": "npc", "labels": ["police"], "hp": 80},
            "events": [{"kind": "combat_attack", "target_entity": f"EID:{i+10}"}],
            "context": {"hp_before": 80, "hp_after": 80},
            "label_history": [["hostile"], ["civilian"]]
        }
        h_j = engine2.generate_hypothesis("A_M1", obs_ce_j, rule_validator=rv)
        r_j = engine2.record_counterexample("A_M1", h_j, label_combo=["police"])
    check("ce5 count == 5", r_j["count"] == 5)
    check("ce5 status deprecated", r_j["new_status"] == "deprecated",
          f"got {r_j.get('new_status')}")

    # ── 8. 轮内快照冻结（锁定决策 #13）──
    print("\n=== 8. 轮内快照冻结 ===")
    engine3 = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine3.add_axiom("A_M1", "attack→hp", "P_M1",
                      boundary_conditions=[{"kind": "npc"}])

    rid = engine3.begin_round(round_id=42)
    check("round_id == 42", rid == 42)
    check("round active", engine3._round_active is True)

    snapshot = engine3.get_round_snapshot()
    check("snapshot has A_M1", "A_M1" in snapshot)
    check("snapshot A_M1 status active", snapshot["A_M1"]["status"] == "active")

    # 轮内记录反例（达到降级阈值）
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = {
            "entity": {"entity_id": "EID:x", "kind": "npc", "labels": combo, "hp": 80},
            "events": [{"kind": "combat_attack", "target_entity": "EID:x"}],
            "context": {"hp_before": 80, "hp_after": 80},
            "label_history": [["hostile"], ["civilian"]]
        }
        h = engine3.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = engine3.record_counterexample("A_M1", h, label_combo=combo)

    check("5 counterexamples recorded", r["count"] == 5)
    check("pending status deprecated", engine3._pending_status.get("A_M1") == "deprecated")

    # 但轮内正式状态仍是 active（快照冻结）
    check("round status still active (frozen)",
          engine3.get_axiom("A_M1")["status"] == "active")

    # end_round → 状态变更生效
    engine3.end_round()
    check("after round status deprecated",
          engine3.get_axiom("A_M1")["status"] == "deprecated")
    check("round not active after end", engine3._round_active is False)
    check("snapshot cleared", engine3.get_round_snapshot() is None)

    # ── 9. 轮内边界更新延迟 ──
    print("\n=== 9. 轮内边界更新延迟 ===")
    engine4 = AxiomEngine()
    engine4.add_axiom("A_W1", "scan limit", "P_W1",
                      boundary_conditions=[{"kind": "npc"}])
    engine4.begin_round()
    engine4.update_axiom_boundary("A_W1", [{"kind": "vehicle"}])
    # 轮内查询仍看到旧边界
    check("round boundary frozen (1 item)",
          len(engine4.get_axiom("A_W1")["boundary_conditions"]) == 1)
    engine4.end_round()
    check("after round boundary updated (2 items)",
          len(engine4.get_axiom("A_W1")["boundary_conditions"]) == 2)

    # ── 10. W2 数据导出（log_recorder 对齐）──
    print("\n=== 10. W2 数据导出 ===")
    engine5 = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine5.add_axiom("A_M1", "test", "P_M1",
                      boundary_conditions=[{"kind": "npc"}, {"kind": "player"}])
    engine5.add_axiom("A_W2_5", "test2", "P_W2_5",
                      boundary_conditions=[])
    engine5.increment_model_update()
    engine5.increment_model_update()

    w2 = engine5.export_w2_data()
    check("axioms list len == 2", len(w2["axioms"]) == 2)
    check("cumulative_counterexamples == 0", w2["cumulative_counterexamples"] == 0)
    check("downgrade_threshold == 5", w2["downgrade_threshold"] == 5)
    check("model_update_count == 2", w2["model_update_count"] == 2)

    # 验证 C_H,struct 计算（与 log_recorder._compute_c_h 对齐，
    # 活跃口径 = active + warned，2026-08-25 用户批准）
    active = sum(1 for a in w2["axioms"] if a["status"] in ("active", "warned"))
    bc_total = sum(len(a["boundary_conditions"]) for a in w2["axioms"]
                   if a["status"] in ("active", "warned"))
    check("active_axioms == 2", active == 2)
    check("boundary_conditions_total == 2", bc_total == 2)

    # ── 11. 未知公理假设生成 ──
    print("\n=== 11. 未知公理 ===")
    h_unknown = engine5.generate_hypothesis("NONEXIST", {}, rule_validator=rv)
    check("unknown axiom returns error", h_unknown.get("error") is not None)

    # ── 12. reset ──
    print("\n=== 12. reset ===")
    engine5.reset()
    check("after reset empty", engine5.get_axiom_count() == 0)
    check("after reset model_update 0", engine5.export_w2_data()["model_update_count"] == 0)

    # ── 13. 审计修复回归：轮内多次边界更新累加 ──
    print("\n=== 13. 审计修复回归：轮内边界累加 ===")
    engine6 = AxiomEngine()
    engine6.add_axiom("A_M1", "attack→hp", "P_M1",
                      boundary_conditions=[{"kind": "npc"}])
    engine6.begin_round()
    engine6.update_axiom_boundary("A_M1", [{"kind": "vehicle"}])
    engine6.update_axiom_boundary("A_M1", [{"kind": "player"}])
    engine6.update_axiom_boundary("A_M1", [{"kind": "container"}])
    # 轮内正式状态仍冻结为 1 条
    check("frozen boundary still 1",
          len(engine6.get_axiom("A_M1")["boundary_conditions"]) == 1)
    engine6.end_round()
    bc6 = engine6.get_axiom("A_M1")["boundary_conditions"]
    check("all 4 batches survived (no overwrite)", len(bc6) == 4, f"got {len(bc6)}")
    kinds6 = [b["kind"] for b in bc6]
    check("vehicle survived", "vehicle" in kinds6)
    check("player survived", "player" in kinds6)
    check("container survived", "container" in kinds6)

    # ── 14. 审计修复回归：effective_status ──
    print("\n=== 14. 审计修复回归：effective_status ===")
    engine7 = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine7.add_axiom("A_M1", "attack→hp", "P_M1", boundary_conditions=[])
    engine7.add_axiom("A_W1", "scan limit", "P_W1", boundary_conditions=[])
    engine7.begin_round()
    # 制造 A_M1 轮内降级（6 种标签组合 → 5 次反例）
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = {
            "entity": {"entity_id": "EID:y", "kind": "npc", "labels": combo, "hp": 80},
            "events": [{"kind": "combat_attack", "target_entity": "EID:y"}],
            "context": {"hp_before": 80, "hp_after": 80},
            "label_history": [["hostile"], ["civilian"]]
        }
        h = engine7.generate_hypothesis("A_M1", obs, rule_validator=rv)
        engine7.record_counterexample("A_M1", h, label_combo=combo)

    check("live status frozen active", engine7.get_axiom("A_M1")["status"] == "active")
    check("effective status deprecated",
          engine7.get_effective_status("A_M1") == "deprecated")
    check("effective status A_W1 active",
          engine7.get_effective_status("A_W1") == "active")

    active_frozen = engine7.get_active_axioms()
    active_eff = engine7.get_active_axioms(effective=True)
    check("frozen active count == 2", len(active_frozen) == 2)
    check("effective active count == 1 (A_M1 excluded)",
          len(active_eff) == 1, f"got {len(active_eff)}")
    check("effective active is A_W1", active_eff[0]["id"] == "A_W1")

    engine7.end_round()
    check("after round effective == live",
          engine7.get_effective_status("A_M1") == "deprecated"
          and engine7.get_axiom("A_M1")["status"] == "deprecated")

    # ── 总结 ──
    print(f"\n{'='*50}")
    print(f"S10 公理引擎自测: {PASS} passed, {FAIL} failed")
    print(f"{'='*50}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
