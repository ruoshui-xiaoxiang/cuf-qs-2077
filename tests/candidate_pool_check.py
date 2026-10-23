"""S29.7 候选规则池验证——2077 官方设定候选（50 条，含 12 条故意错误）。

验证目标：
1. 候选池加载 50 条、ID 唯一、故意错误标记齐全。
2. 激活机制：事件流驱动候选激活（entity_appeared/status_applied 等）。
3. **证伪能力（核心）**：在符合 2077 真实设定的观测下——
   - 正确规则（intentional_error=false）→ 观测满足断言 → 无 violation（确认）
   - 故意错误规则（intentional_error=true）→ 断言与真实设定相反 → 观测必违反
     → 构成反例 → 可被降级/弃用（系统自主发现并淘汰错误规则）

运行：py tests/candidate_pool_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w1.rule_validator import RuleValidator

# ── 2077 真实设定观测（实体标签 = 官方设定事实）────────────────
# 每条对应候选规则的 assertion 触发实体，标签符合 2077 实际
TRUE_OBSERVATIONS = [
    # 帮派敌对域（A_M1）
    {"entity": {"entity_id": "EID:G1", "kind": "npc",
                "labels": ["maelstrom", "hostile", "cyberware",
                           "mortal", "retaliates", "watson"]}},
    {"entity": {"entity_id": "EID:G2", "kind": "npc",
                "labels": ["tyger_claws", "hostile", "japantown"]}},
    {"entity": {"entity_id": "EID:G3", "kind": "npc",
                "labels": ["scavengers", "hostile", "harvester"]}},
    {"entity": {"entity_id": "EID:G4", "kind": "npc",
                "labels": ["animals", "hostile"]}},
    {"entity": {"entity_id": "EID:G5", "kind": "npc",
                "labels": ["wraiths", "hostile"]}},
    {"entity": {"entity_id": "EID:G6", "kind": "npc",
                "labels": ["6th_street", "hostile"]}},
    {"entity": {"entity_id": "EID:G7", "kind": "npc",
                "labels": ["valentinos", "hostile"]}},
    {"entity": {"entity_id": "EID:G8", "kind": "npc",
                "labels": ["voodoo_boys", "hostile", "pacifica"]}},
    {"entity": {"entity_id": "EID:G9", "kind": "npc",
                "labels": ["arasaka", "hostile", "corpo_guard"]}},
    # 状态效果域（A_S1）
    {"entity": {"entity_id": "EID:S1", "kind": "npc",
                "labels": ["burning", "thermal", "temporary"]}},
    {"entity": {"entity_id": "EID:S2", "kind": "npc",
                "labels": ["bleeding", "physical", "temporary"]}},
    {"entity": {"entity_id": "EID:S3", "kind": "npc",
                "labels": ["poisoned", "chemical", "temporary"]}},
    {"entity": {"entity_id": "EID:S4", "kind": "npc",
                "labels": ["stunned", "electrical", "temporary"]}},
    {"entity": {"entity_id": "EID:S5", "kind": "device",
                "labels": ["emped", "mech", "drone", "flying"]}},
    {"entity": {"entity_id": "EID:S6", "kind": "npc",
                "labels": ["fireproof", "burn_immune"]}},
    # 物品域（A_I1）
    {"entity": {"entity_id": "EID:I1", "kind": "item",
                "labels": ["item", "cyberware", "pickup", "rarity"]}},
    {"entity": {"entity_id": "EID:I2", "kind": "item",
                "labels": ["item", "weapon", "pickup", "rarity"]}},
    {"entity": {"entity_id": "EID:I3", "kind": "item",
                "labels": ["item", "consumable", "decrements"]}},
    # 移动域（A_M2）
    {"entity": {"entity_id": "EID:M1", "kind": "npc",
                "labels": ["aldecaldos", "nomad", "badlands"]}},
    {"entity": {"entity_id": "EID:M2", "kind": "vehicle",
                "labels": ["vehicle", "mobile"]}},
    {"entity": {"entity_id": "EID:M3", "kind": "player",
                "labels": ["player", "scannable", "mobile"]}},
    {"entity": {"entity_id": "EID:M4", "kind": "npc",
                "labels": ["npc", "moving", "confirmed_movement"]}},
    {"entity": {"entity_id": "EID:M5", "kind": "device",
                "labels": ["drone", "flying", "mech"]}},
    # 生命周期域（A_W2_5）
    {"entity": {"entity_id": "EID:L1", "kind": "npc",
                "labels": ["hostile", "mortal", "retaliates"]}},
    {"entity": {"entity_id": "EID:L2", "kind": "npc",
                "labels": ["civilian", "non_aggressive"]}},
    {"entity": {"entity_id": "EID:L3", "kind": "npc",
                "labels": ["mox", "protector"]}},
    {"entity": {"entity_id": "EID:L4", "kind": "npc",
                "labels": ["ncpd", "responds"]}},
    # 感知域（A_W1）
    {"entity": {"entity_id": "EID:P1", "kind": "npc",
                "labels": ["maelstrom", "hostile", "watson"]}},
    # 黑客域（A_M1）
    {"entity": {"entity_id": "EID:H1", "kind": "npc",
                "labels": ["hacker", "can_blind", "cooldown"]}},
    {"entity": {"entity_id": "EID:H2", "kind": "npc",
                "labels": ["overheat", "burn_applies"]}},
]

# 故意错误规则断言要求但真实观测必缺的标签（证伪对照）
ERROR_EXPECTED = {
    "C_GANG_101": ["civilian"], "C_GANG_102": ["civilian", "protector"],
    "C_STAT_005": ["permanent"], "C_STAT_006": ["harmless"],
    "C_ITEM_005": ["infinite"], "C_ITEM_006": ["indestructible"],
    "C_MOVE_004": ["immobile"], "C_MOVE_005": ["stationary"],
    "C_LIFE_004": ["immortal"], "C_LIFE_005": ["aggressive"],
    "C_PER_004": ["ignores_player"], "C_HACK_003": ["no_cooldown"],
}


def main():
    rv = RuleValidator()
    cands = rv.get_candidates()
    by_id = {c["axiom_id"]: c for c in cands}
    errors = [c for c in cands if c.get("intentional_error")]
    correct = [c for c in cands if not c.get("intentional_error")]

    print(f"候选池：{len(cands)} 条（正确 {len(correct)} / 故意错误 {len(errors)}）")

    # 1. 正确规则验证：首个匹配观测（该候选断言专属）应无违反
    correct_confirmed = 0
    correct_falsified = []
    for c in correct:
        a = c.get("assertion") or {}
        if not a:
            continue
        match_any = a.get("label_match_any") or []
        triggered = [o for o in TRUE_OBSERVATIONS
                     if any(m in (o["entity"].get("labels") or [])
                            for m in match_any)
                     and (not a.get("kind")
                          or o["entity"].get("kind") == a.get("kind"))]
        if not triggered:
            print(f"  [未触发] {c['axiom_id']}（无匹配观测，暂无法验证）")
            continue
        res = rv.is_valid_counterexample(c["axiom_id"], triggered[0])
        if res["violated_rules"]:
            correct_falsified.append(c["axiom_id"])
        else:
            correct_confirmed += 1

    # 2. 故意错误规则验证：真实观测必违反（断言与设定相反）
    error_falsified = 0
    error_escaped = []
    for c in errors:
        a = c.get("assertion") or {}
        match_any = a.get("label_match_any") or []
        triggered = [o for o in TRUE_OBSERVATIONS
                     if any(m in (o["entity"].get("labels") or [])
                            for m in match_any)
                     and (not a.get("kind")
                          or o["entity"].get("kind") == a.get("kind"))]
        if not triggered:
            print(f"  [未触发] {c['axiom_id']}（故意错误但无匹配观测）")
            error_escaped.append(c["axiom_id"])
            continue
        violated_any = any(
            rv.is_valid_counterexample(c["axiom_id"], o)["violated_rules"]
            for o in triggered)
        if violated_any:
            error_falsified += 1
        else:
            error_escaped.append(c["axiom_id"])

    print(f"\n正确规则确认：{correct_confirmed}/{len(correct)}"
          f"{'（被误判违反：' + str(correct_falsified) + '）' if correct_falsified else ''}")
    print(f"故意错误规则证伪：{error_falsified}/{len(errors)}"
          f"{'（逃逸：' + str(error_escaped) + '）' if error_escaped else ''}")

    # 3. 激活机制抽查：entity_appeared 事件流激活帮派候选
    from w2.axiom_activator import ActivationEngine

    class StubEngine:
        def __init__(self):
            self.added = []

        def add_axiom(self, aid, hyp, anchor, boundary_conditions=None):
            self.added.append(aid)

    eng = StubEngine()
    act = ActivationEngine(rv, eng, enabled=True)
    # 4 轮各出现 1 个帮派实体（entity_appeared → min_count=2 达标激活）
    for r in range(4):
        act.feed({"events": [{"kind": "entity_appeared"},
                             {"kind": "entity_appeared"}]}, r)
        act.end_round()
    print(f"\n激活抽查：entity_appeared ×8 事件 → 激活候选数 = "
          f"{len(eng.added)}（含 C_GANG_001: "
          f"{'C_GANG_001' in eng.added}）")


if __name__ == "__main__":
    main()
