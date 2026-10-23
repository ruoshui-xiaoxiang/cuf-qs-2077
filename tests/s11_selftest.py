"""S11 降级管理器脱机自测。

验收标准：模拟反例按阈值触发降级并更新边界，轮内状态冻结正确。
覆盖：反例处理全流程 / 3次预警 / 5次降级 / 多标签保护 /
      边界条件自动补全 / 降级后重算g / 轮内判定轮间生效 / C_H,extend。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from w2.axiom_engine import AxiomEngine
from w2.qs_kernel import QSKernel
from w2.downgrade_manager import DowngradeManager
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


def make_obs(kind="npc", labels=None, hp_before=80, hp_after=80):
    """构造反例观测。"""
    return {
        "entity": {"entity_id": f"EID:{labels}", "kind": kind,
                    "labels": labels or ["hostile"], "hp": hp_after},
        "events": [{"kind": "combat_attack", "target_entity": f"EID:{labels}"}],
        "context": {"hp_before": hp_before, "hp_after": hp_after},
        "label_history": [["hostile"], ["civilian"]]
    }


def main():
    rv = RuleValidator()

    def make_engine_qs_dm():
        engine = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
        engine.add_axiom("A_M1", "攻击→HP下降", "P_M1",
                         boundary_conditions=[{"kind": "npc"}])
        qs = QSKernel()
        qs.set_target_g([1.0, 0.0])
        dm = DowngradeManager(engine, qs, warn_threshold=3, downgrade_threshold=5)
        return engine, qs, dm

    # ── 1. 基本构造 ──
    print("\n=== 1. 基本构造 ===")
    engine, qs, dm = make_engine_qs_dm()
    check("warn_threshold == 3", dm.warn_threshold == 3)
    check("downgrade_threshold == 5", dm.downgrade_threshold == 5)
    check("no downgrades initially", len(dm.get_downgrade_events()) == 0)
    check("no warnings initially", len(dm.get_warn_events()) == 0)

    # ── 2. 反例处理：未达阈值 ──
    print("\n=== 2. 反例处理：未达阈值 ===")
    engine, qs, dm = make_engine_qs_dm()
    obs1 = make_obs(labels=["hostile"])
    h1 = engine.generate_hypothesis("A_M1", obs1, rule_validator=rv)
    r1 = dm.process_counterexample("A_M1", h1, obs1)
    check("ce1 not recorded (single label)", r1["recorded"] is False)
    check("ce1 action none", r1["action"] == "none")

    obs2 = make_obs(labels=["civilian"])
    h2 = engine.generate_hypothesis("A_M1", obs2, rule_validator=rv)
    r2 = dm.process_counterexample("A_M1", h2, obs2)
    check("ce2 recorded", r2["recorded"] is True)
    check("ce2 count == 1", r2["count"] == 1)
    check("ce2 action none", r2["action"] == "none")
    check("ce2 no boundary added", len(r2["boundary_conditions_added"]) == 0)
    check("ce2 no g recompute", r2["g_recomputed"] is False)

    # ── 3. 反例处理：3次预警 ──
    print("\n=== 3. 反例处理：3次预警 ===")
    engine, qs, dm = make_engine_qs_dm()
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm.process_counterexample("A_M1", h, obs)
    check("ce3 count == 3", r["count"] == 3, f"count={r['count']}")
    check("ce3 action warn", r["action"] == "warn", r["action"])
    check("ce3 new_status warned", r["new_status"] == "warned")
    check("ce3 boundary auto added", len(r["boundary_conditions_added"]) > 0,
          str(r["boundary_conditions_added"]))
    check("ce3 no g recompute (warn)", r["g_recomputed"] is False)
    check("warn events count == 1", len(dm.get_warn_events()) == 1)

    # ── 4. 反例处理：5次降级 ──
    print("\n=== 4. 反例处理：5次降级 ===")
    engine = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine.add_axiom("A_M1", "攻击→HP下降", "P_M1",
                     boundary_conditions=[{"kind": "npc"}])
    engine.add_axiom("A_W1", "采样限制", "P_W1",
                     boundary_conditions=[{"kind": "npc"}])
    qs = QSKernel()
    qs.set_target_g([1.0, 0.0])
    dm = DowngradeManager(engine, qs, warn_threshold=3, downgrade_threshold=5)
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm.process_counterexample("A_M1", h, obs)
    check("ce5 count == 5", r["count"] == 5)
    check("ce5 action downgrade", r["action"] == "downgrade", r["action"])
    check("ce5 new_status deprecated", r["new_status"] == "deprecated")
    check("ce5 boundary auto added", len(r["boundary_conditions_added"]) > 0)
    check("ce5 g recomputed", r["g_recomputed"] is True, f"g_recomputed={r['g_recomputed']}")
    check("downgrade events count == 1", len(dm.get_downgrade_events()) == 1)

    # ── 5. 多标签保护（锁定决策 #4）──
    print("\n=== 5. 多标签保护 ===")
    engine, qs, dm = make_engine_qs_dm()
    # 连续6次同标签反例——不累计
    for _ in range(6):
        obs = make_obs(labels=["hostile"])
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm.process_counterexample("A_M1", h, obs)
    check("all same label: not recorded", r["recorded"] is False)
    check("all same label: no downgrade", len(dm.get_downgrade_events()) == 0)
    check("all same label: count == 0", r["count"] == 0)

    # ── 6. 边界条件自动补全 ──
    print("\n=== 6. 边界条件自动补全 ===")
    engine, qs, dm = make_engine_qs_dm()
    # 触发预警以补全边界（需4种combo让第3个被记录）
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"]]:
        obs = make_obs(labels=combo, kind="npc")
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm.process_counterexample("A_M1", h, obs)

    auto_added = dm.get_auto_added_boundary("A_M1")
    check("auto added boundary not empty", len(auto_added) > 0, str(auto_added))
    has_kind = any(isinstance(bc, dict) and bc.get("kind") for bc in auto_added)
    check("auto added has kind", has_kind, str(auto_added))
    has_source = any(isinstance(bc, dict) and bc.get("source") == "auto_complete"
                     for bc in auto_added)
    check("auto added has source tag", has_source)

    # ── 7. 降级后重算 g ──
    print("\n=== 7. 降级后重算 g ===")
    engine = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine.add_axiom("A_M1", "攻击→HP下降", "P_M1",
                     boundary_conditions=[{"kind": "npc"}])
    engine.add_axiom("A_W1", "采样限制", "P_W1",
                     boundary_conditions=[{"kind": "npc"}])
    qs = QSKernel()
    qs.set_target_g([1.0, 0.0])
    dm = DowngradeManager(engine, qs, warn_threshold=3, downgrade_threshold=5)
    qs.compute_alignment_score([0.95, 0.0])
    qs.compute_alignment_score([0.96, 0.0])
    check("qs history before downgrade == 2", len(qs.get_history()) == 2)

    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm.process_counterexample("A_M1", h, obs)
    check("g recomputed after downgrade", r["g_recomputed"] is True)
    check("qs history reset", len(qs.get_history()) == 0)

    # ── 8. 轮内判定轮间生效（锁定决策 #13）──
    print("\n=== 8. 轮内判定轮间生效 ===")
    engine, qs, dm = make_engine_qs_dm()
    dm.begin_round(round_id=10)
    check("round active", engine._round_active is True)

    # 轮内累计5次反例
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm.process_counterexample("A_M1", h, obs)
    check("round count == 5", r["count"] == 5)
    check("round action downgrade", r["action"] == "downgrade")
    check("round pending deprecated", engine._pending_status.get("A_M1") == "deprecated")
    # 轮内正式状态仍 active
    check("round axiom still active (frozen)",
          engine.get_axiom("A_M1")["status"] == "active")

    dm.end_round()
    check("after round status deprecated",
          engine.get_axiom("A_M1")["status"] == "deprecated")

    # ── 9. C_H,extend 计算（锁定决策 #11 + 逻辑学审查 A）──
    print("\n=== 9. C_H,extend (min scheme, monotonic increase) ===")
    engine, qs, dm = make_engine_qs_dm()
    # 逻辑学审查 A：0 反例 → extend=min(0,5)=0（旧语义是 5）
    check("c_h_extend initial == 0", dm.get_c_h_extend() == 0)

    # 锁定决策 #4 降级保护：label_count < 2 时 recorded=False、不计入计数。
    # 3 个不同的单标签 combo [hostile, civilian, armed]：
    #   第 1 个 (hostile) → label_count=1 → 不计数
    #   第 2 个 (civilian) → label_count=2 → 通过，count=1
    #   第 3 个 (armed) → label_count=3 → 通过，count=2
    for combo in [["hostile"], ["civilian"], ["armed"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        dm.process_counterexample("A_M1", h, obs)
    # 2 个有效反例 → extend=min(2,5)=2
    ce = dm.get_c_h_extend()
    check("c_h_extend after 3 combos (2 effective CE) == 2", ce == 2, f"got {ce}")

    # 再加 3 个 combo (police, gang, netrunner) → 累计 5 个有效 CE → 饱和
    for combo in [["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        dm.process_counterexample("A_M1", h, obs)
    ce2 = dm.get_c_h_extend()
    check("c_h_extend after 6 combos (5 effective CE) == 5 (saturated)",
          ce2 == 5, f"got {ce2}")

    # 超过阈值继续加 → extend 保持 5（bounded 代理度量不越界）
    for combo in [["cyberpsycho"], ["militech"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        dm.process_counterexample("A_M1", h, obs)
    check("c_h_extend after 8 combos capped at 5", dm.get_c_h_extend() == 5,
          f"got {dm.get_c_h_extend()}")

    # ── 10. 状态导出 ──
    print("\n=== 10. 状态导出 ===")
    engine, qs, dm = make_engine_qs_dm()
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = engine.generate_hypothesis("A_M1", obs, rule_validator=rv)
        dm.process_counterexample("A_M1", h, obs)
    status = dm.get_status()
    check("status total_downgrades == 1", status["total_downgrades"] == 1)
    check("status total_warnings == 1", status["total_warnings"] == 1)
    # 逻辑学审查 A：6 个 CE → extend=min(6,5)=5（饱和于阈值；旧语义=0）
    check("status c_h_extend == 5 (6 CE capped at threshold 5)",
          status["c_h_extend"] == 5)
    check("status downgraded_axioms has A_M1",
          "A_M1" in status["downgraded_axioms"])
    check("status auto_added_boundary_count > 0",
          status["auto_added_boundary_count"] > 0)

    # ── 11. 全部公理降级 → g 无效 ──
    print("\n=== 11. 全部公理降级 ===")
    engine2 = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine2.add_axiom("A_M1", "test", "P_M1", boundary_conditions=[{"kind": "npc"}])
    qs2 = QSKernel()
    qs2.set_target_g([1.0, 0.0])
    dm2 = DowngradeManager(engine2, qs2, warn_threshold=3, downgrade_threshold=5)
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = engine2.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm2.process_counterexample("A_M1", h, obs)
    check("single axiom downgraded", r["action"] == "downgrade")
    check("no active axioms", engine2.get_axiom_count(active_only=True) == 0)
    # g 重算应返回 False（无活跃公理）
    check("g recompute False (no active)", r["g_recomputed"] is False)

    # ── 12. 无 qs_kernel 时降级 ──
    print("\n=== 12. 无 qs_kernel 降级 ===")
    engine3 = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    engine3.add_axiom("A_M1", "test", "P_M1")
    dm3 = DowngradeManager(engine3, qs_kernel=None,
                           warn_threshold=3, downgrade_threshold=5)
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = engine3.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm3.process_counterexample("A_M1", h, obs)
    check("downgrade without qs_kernel", r["action"] == "downgrade")
    check("g_recomputed False (no qs)", r["g_recomputed"] is False)

    # ── 13. 审计修复回归：轮内降级 g 重算按生效状态判断 ──
    print("\n=== 13. 审计修复回归：轮内 g 重算 effective 状态 ===")

    # 场景 A：轮内唯一公理被降级 → 生效状态无活跃公理 → g 重算应返回 False
    eng_a = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    eng_a.add_axiom("A_M1", "test", "P_M1", boundary_conditions=[{"kind": "npc"}])
    qs_a = QSKernel()
    qs_a.set_target_g([1.0, 0.0])
    dm_a = DowngradeManager(eng_a, qs_a, warn_threshold=3, downgrade_threshold=5)
    dm_a.begin_round(round_id=20)
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = eng_a.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm_a.process_counterexample("A_M1", h, obs)
    check("A: round downgrade action", r["action"] == "downgrade")
    check("A: live status frozen active",
          eng_a.get_axiom("A_M1")["status"] == "active")
    check("A: g recompute False (all effectively deprecated)",
          r["g_recomputed"] is False, f"got {r['g_recomputed']}")
    dm_a.end_round()

    # 场景 B：轮内双公理，其一被降级 → 剩余一条生效活跃 → g 重算应返回 True
    eng_b = AxiomEngine(warn_threshold=3, downgrade_threshold=5)
    eng_b.add_axiom("A_M1", "test", "P_M1", boundary_conditions=[{"kind": "npc"}])
    eng_b.add_axiom("A_W1", "test2", "P_W1", boundary_conditions=[])
    qs_b = QSKernel()
    qs_b.set_target_g([1.0, 0.0])
    dm_b = DowngradeManager(eng_b, qs_b, warn_threshold=3, downgrade_threshold=5)
    dm_b.begin_round(round_id=21)
    for combo in [["hostile"], ["civilian"], ["armed"], ["police"], ["gang"], ["netrunner"]]:
        obs = make_obs(labels=combo)
        h = eng_b.generate_hypothesis("A_M1", obs, rule_validator=rv)
        r = dm_b.process_counterexample("A_M1", h, obs)
    check("B: round downgrade action", r["action"] == "downgrade")
    check("B: g recompute True (A_W1 still effectively active)",
          r["g_recomputed"] is True, f"got {r['g_recomputed']}")
    dm_b.end_round()

    # ── 总结 ──
    print(f"\n{'='*50}")
    print(f"S11 降级管理器自测: {PASS} passed, {FAIL} failed")
    print(f"{'='*50}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
