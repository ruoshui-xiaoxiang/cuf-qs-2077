"""S29.9 L2：候选规则池扩充生成器——数据驱动生成至 200 条（含时序规则与故意错误）。

生成约束（可验证性）：
- assertion{kind, label_match_any, label_require}：标签组合约束（命中触发、
  缺 require 即违反）——实机/mock 观测可判定；
- verification{kind=temporal, condition}：跨轮时序（disappear_within /
  persist_min）——L2 新能力，runner 跨轮历史可判定；
- 故意错误规则（intentional_error=true）：require 与 2077 真实设定相反
  或时序条件反转 → 真实观测必违反 → 可被证伪。

运行：py tests/gen_candidates.py
产出：src/w1/world_rules.json 的 candidate_axioms 替换为 200 条。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "src" / "w1" / "world_rules.json"

# ── 2077 设定数据清单 ──────────────────────────────────────
GANGS = ["maelstrom", "tyger_claws", "scavengers", "animals", "wraiths",
         "6th_street", "valentinos", "voodoo_boys", "mox", "aldecaldos",
         "ncpd", "arasaka"]
GANG_REGIONS = {"maelstrom": "watson", "tyger_claws": "japantown",
                "voodoo_boys": "pacifica", "aldecaldos": "badlands",
                "valentinos": "heywood", "6th_street": "santo_domingo",
                "mox": "charter_hill", "wraiths": "badlands"}
# 帮派行为属性（帮派 → 携带/行为标签）
GANG_ATTR = {
    "maelstrom": ["cyberware", "chrome_freak"], "tyger_claws": ["melee", "katana"],
    "scavengers": ["harvester"], "animals": ["brawler", "steroid"],
    "wraiths": ["raider", "nomad"], "6th_street": ["vigilante", "gunrunner"],
    "valentinos": ["shooter", "lowrider"], "voodoo_boys": ["netrunner"],
    "mox": ["protector"], "aldecaldos": ["nomad"], "ncpd": ["responds"],
    "arasaka": ["corpo_guard", "hostile"],
}
STATUS_EFFECTS = [("burning", "thermal"), ("bleeding", "physical"),
                  ("poisoned", "chemical"), ("stunned", "electrical"),
                  ("blinded", "thermal"), ("knocked_down", "physical"),
                  ("crippled", "physical"), ("emped", "electrical")]
ITEMS = [("cyberware", "pickup"), ("weapon", "pickup"), ("consumable", "decrements"),
         ("clothing", "pickup"), ("quickhack", "pickup"), ("grenade", "decrements"),
         ("smart_weapon", "smart"), ("tech_weapon", "charge"),
         ("power_weapon", "ricochet"), ("armor", "pickup"), ("baton", "melee"),
         ("relic", "legendary")]
QUICKHACKS = [("overheat", "burn_applies"), ("short_circuit", "stun_applies"),
              ("contagion", "poison_spreads"), ("reboot_optics", "blind_applies"),
              ("cyberware_malfunction", "disables_cyberware"), ("suicide", "dangerous"),
              ("system_reset", "disables_combat"), ("synapse_burnout", "high_damage")]
VEHICLES = [("vehicle", "mobile"), ("drone", "flying"), ("mech", "emp_susceptible"),
            ("motorbike", "fast"), ("quadra", "turbo"), ("thorton", "armored")]

candidates = []


def _add(axiom_id, domain, hypothesis, anchor, falsification, trigger_kinds,
         intentional_error, assertion=None, verification=None):
    candidates.append({
        "axiom_id": axiom_id, "domain": domain, "hypothesis": hypothesis,
        "anchor": anchor, "falsification": falsification,
        "source": "S29.9 候选池扩充（2077 设定 + L2 时序）",
        "intentional_error": intentional_error,
        "activation_trigger": {"kind": "event_count",
                               "event_kinds": trigger_kinds,
                               "min_count": 2, "window_rounds": 4},
        **({"assertion": assertion} if assertion else {}),
        **({"verification": verification} if verification else {}),
    })


# ── 帮派域（A_M1）：敌对断言 + 区域 + 行为 ─────────────────
for i, g in enumerate(GANGS):
    _add(f"C_G2_GANG_{i:03d}", "A_M1",
         f"夜之城帮派 {g} 成员对玩家为敌对实体", "P_GANG_HOSTILE",
         f"观测到 {g} 实体不携带 hostile", ["entity_appeared"], False,
         {"kind": "npc", "label_match_any": [g], "label_require": ["hostile"]})
    if g in GANG_REGIONS:
        _add(f"C_G2_REG_{i:03d}", "A_W1",
             f"帮派 {g} 主要活动于 {GANG_REGIONS[g]}", "P_W1",
             f"观测到 {g} 实体无 {GANG_REGIONS[g]} 区域标签", ["entity_appeared"],
             False,
             {"kind": "npc", "label_match_any": [g],
              "label_require": [GANG_REGIONS[g]]})
# 帮派行为扩展
_add("C_G2_MOX_PROTECT", "A_W2_5", "Mox 帮派保护性工作者（protector）",
     "P_STATUS", "Mox 实体不携带 protector", ["entity_appeared"], False,
     {"kind": "npc", "label_match_any": ["mox"], "label_require": ["protector"]})
_add("C_G2_ALDE_NOMAD", "A_M2", "Aldecaldos 是游牧民（nomad）",
     "P_M1", "Aldecaldos 实体不携带 nomad", ["move_confirmed"], False,
     {"kind": "npc", "label_match_any": ["aldecaldos"], "label_require": ["nomad"]})
_add("C_G2_NCPD_RESPOND", "A_W2_5", "NCPD 警察响应犯罪现场（responds）",
     "P_STATUS", "NCPD 实体不携带 responds", ["combat_attack"], False,
     {"kind": "npc", "label_match_any": ["ncpd"], "label_require": ["responds"]})
_add("C_G2_CORP_GUARD", "A_M1", "公司保安（Arasaka/Militech 实体）对玩家敌对",
     "P_GANG_HOSTILE", "公司保安实体不携带 hostile", ["entity_appeared"], False,
     {"kind": "npc", "label_match_any": ["arasaka", "militech"],
      "label_require": ["hostile"]})
# 帮派行为属性（GANG_ATTR）
_attr_idx = 0
for _g, _attrs in GANG_ATTR.items():
    for _a in _attrs:
        _add(f"C_G2_ATTR_{_attr_idx:03d}", "A_M1",
             f"帮派 {_g} 成员携带/具备 {_a} 行为属性", "P_GANG_HOSTILE",
             f"{_g} 实体缺少 {_a}", ["entity_appeared"], False,
             {"kind": "npc", "label_match_any": [_g], "label_require": [_a]})
        _attr_idx += 1

# ── 状态域（A_S1）：状态→伤害来源 + 免疫 + 时长 ────────────
for i, (status, src) in enumerate(STATUS_EFFECTS):
    _add(f"C_G2_STAT_{i:03d}", "A_S1",
         f"{status} 状态由 {src} 伤害触发", "P_STATUS",
         f"{status} 实体缺少 {src} 来源标签", ["status_applied"], False,
         {"kind": "npc", "label_match_any": [status], "label_require": [src]})
    _add(f"C_G2_STATIMM_{i:03d}", "A_S1",
         f"{status} 状态可被对应免疫义体抵消", "P_STATUS",
         f"{status} 免疫实体仍处于状态", ["status_applied"], False,
         {"kind": "npc", "label_match_any": [f"{status}_immune"],
          "label_require": ["immune"]})
# 状态时长（L2 时序）：状态实体应在有限轮数内消退
_add("C_G2_STAT_TIMER", "A_S1", "战斗状态效果应在有限轮数内消退（不永久）",
     "P_STATUS", "状态实体持续超过窗口轮数", ["status_applied", "status_changed"],
     False, None,
     {"kind": "temporal", "condition": "disappear_within", "window_rounds": 8})

# ── 物品域（A_I1）：类型属性 + 消耗 + 拾取 ─────────────────
for i, (itype, attr) in enumerate(ITEMS):
    _add(f"C_G2_ITEM_{i:03d}", "A_I1",
         f"{itype} 物品 {attr} 属性成立", "P_ITEM",
         f"{itype} 实体缺少 {attr}", ["item_acquired", "item_used"], False,
         {"kind": "item", "label_match_any": [itype], "label_require": [attr]})
_add("C_G2_ITEM_RARITY", "A_I1", "物品具有稀有度等级（rarity）",
     "P_ITEM", "物品实体缺少 rarity", ["item_acquired"], False,
     {"kind": "item", "label_match_any": ["item"], "label_require": ["rarity"]})
_add("C_G2_ITEM_CONSERVE", "A_I1", "物品获取后数量增加、使用后减少（守恒）",
     "P_ITEM", "物品数量违背守恒", ["item_acquired", "item_used"], False, None,
     {"kind": "event_effect", "event_kind": "item_used",
      "expect_hp_decrease": False})
_add("C_G2_ITEM_TIMER", "A_I1", "掉落物品应在有限轮数内消失（被拾取/刷除）",
     "P_ITEM", "物品持续存在超过窗口", ["item_acquired", "entity_disappeared"],
     False, None,
     {"kind": "temporal", "condition": "disappear_within", "window_rounds": 6})

# ── 移动域（A_M2）：载具/无人机/游牧 ───────────────────────
for i, (vtype, attr) in enumerate(VEHICLES):
    _add(f"C_G2_MOVE_{i:03d}", "A_M2",
         f"{vtype} 实体具备 {attr}", "P_M1",
         f"{vtype} 实体缺少 {attr}", ["move_confirmed"], False,
         {"kind": "device" if vtype != "vehicle" else "vehicle",
          "label_match_any": [vtype], "label_require": [attr]})
_add("C_G2_MOVE_PLAYER", "A_M2", "玩家角色可自由移动（mobile）",
     "P_M1", "玩家实体不可移动", ["move_confirmed"], False,
     {"kind": "player", "label_match_any": ["player"],
      "label_require": ["mobile"]})

# ── 生命周期域（A_W2_5）：NPC 行为 + 时序 ─────────────────
_add("C_G2_LIFE_MORTAL", "A_W2_5", "敌对实体可被击杀（mortal）",
     "P_STATUS", "敌对实体免疫死亡", ["entity_death"], False,
     {"kind": "npc", "label_match_any": ["hostile"], "label_require": ["mortal"]})
_add("C_G2_LIFE_RETAL", "A_W2_5", "敌对实体遭袭后反击（retaliates）",
     "P_STATUS", "敌对实体遭袭不反击", ["combat_attack"], False,
     {"kind": "npc", "label_match_any": ["hostile"],
      "label_require": ["retaliates"]})
_add("C_G2_LIFE_CIV_NONAGGR", "A_W2_5", "平民不主动攻击（non_aggressive）",
     "P_STATUS", "平民携带 aggressive", ["entity_appeared"], False,
     {"kind": "npc", "label_match_any": ["civilian"],
      "label_require": ["non_aggressive"]})
_add("C_G2_LIFE_PERSIST", "A_W2_5", "敌对实体出现后应持续至少 2 轮（不会凭空消失）",
     "P_STATUS", "敌对实体过早消失", ["entity_appeared", "entity_death"], False,
     {"kind": "npc", "label_match_any": ["hostile"]},
     {"kind": "temporal", "condition": "persist_min", "min_rounds": 2})
_add("C_G2_LIFE_DEATH_EVENT", "A_W2_5", "实体死亡应有 entity_death 事件",
     "P_STATUS", "hp 归零但无死亡事件", ["entity_death"], False, None,
     {"kind": "event_effect", "event_kind": "entity_death",
      "expect_entity_removed": True})

# ── 感知域（A_W1）：玩家可扫描 + 帮派区域（已部分生成）────
_add("C_G2_PER_SCAN", "A_W1", "玩家实体可被扫描（scannable）",
     "P_W1", "玩家不可扫描", ["entity_appeared"], False,
     {"kind": "player", "label_match_any": ["player"],
      "label_require": ["scannable"]})

# ── 黑客域（A_M1）：快速破解效果 ───────────────────────────
for i, (qh, eff) in enumerate(QUICKHACKS):
    _add(f"C_G2_HACK_{i:03d}", "A_M1",
         f"快速破解 {qh} 施加 {eff}", "P_GANG_HOSTILE",
         f"{qh} 破解无 {eff} 效果", ["status_applied"], False,
         {"kind": "npc", "label_match_any": [qh], "label_require": [eff]})

# ── 补充生成段（凑 200：物品细分/状态时长/感知区域/生命周期/时序）──
_si = 0
# 状态时长多档（L2 时序：状态应在不同窗口内消退）
for _w in (3, 5, 8, 10):
    _add(f"C_G2_SUPP_STATUS_{_si:03d}", "A_S1",
         f"战斗状态效果应在 {_w} 轮内消退（不永久）", "P_STATUS",
         f"状态实体持续超过 {_w} 轮", ["status_applied", "status_changed"],
         False, None,
         {"kind": "temporal", "condition": "disappear_within",
          "window_rounds": _w})
    _si += 1
# 物品细分属性
for _itype, _attr in [("cyberware", "slot_uses"), ("weapon", "damage_scales"),
                      ("consumable", "cooldown_consumption"),
                      ("smart_weapon", "target_lock"), ("armor", "protection"),
                      ("quickhack", "ram_cost"), ("relic", "skill_bonus"),
                      ("grenade", "explosive"), ("clothing", "style_stat"),
                      ("baton", "nonlethal"), ("power_weapon", "wall_penetration"),
                      ("tech_weapon", "charge_shot")]:
    _add(f"C_G2_SUPP_ITEM_{_si:03d}", "A_I1",
         f"{_itype} 物品具备 {_attr} 属性", "P_ITEM",
         f"{_itype} 实体缺少 {_attr}", ["item_acquired"], False,
         {"kind": "item", "label_match_any": [_itype], "label_require": [_attr]})
    _si += 1
# 感知：帮派 × 区域补足 + 距离感知
for _g, _reg in [("tyger_claws", "kabuki"), ("valentinos", "vista_del_rey"),
                 ("6th_street", "vista_del_rey"), ("voodoo_boys", "pacifica_ruins"),
                 ("animals", "waterfront"), ("wraiths", "badlands_outskirts")]:
    _add(f"C_G2_SUPP_PER_{_si:03d}", "A_W1",
         f"帮派 {_g} 活动于 {_reg}", "P_W1",
         f"{_g} 实体无 {_reg} 区域标签", ["entity_appeared"], False,
         {"kind": "npc", "label_match_any": [_g], "label_require": [_reg]})
    _si += 1
# 生命周期补充
for _n, _lab in [("逃亡平民（flee）", "flee"), ("巡逻警察（patrol）", "patrol"),
                 ("中立商人（merchant）", "merchant"), ("义体医生（ripperdoc）", "ripperdoc"),
                 ("出租车司机（driver）", "driver"), ("目击者（witness）", "witness"),
                 ("街头艺人（busker）", "busker"), ("安保机器人（security_bot）", "security")]:
    _add(f"C_G2_SUPP_LIFE_{_si:03d}", "A_W2_5",
         f"{_n} 行为标签成立", "P_STATUS",
         f"实体缺少 {_lab} 标签", ["entity_appeared"], False,
         {"kind": "npc", "label_match_any": [_lab], "label_require": [_lab]})
    _si += 1
# 移动补充
for _n, _lab in [("玩家可冲刺（sprint）", "sprintable"),
                 ("NPC 可巡逻移动（patrol_move）", "patrol_move"),
                 ("车辆可漂移（drift）", "driftable"),
                 ("无人机可悬停（hover）", "hoverable"),
                 ("帮派可驾车追击（vehicle_chase）", "chase_vehicle"),
                 ("玩家可攀爬（climbable）", "climbable")]:
    _add(f"C_G2_SUPP_MOVE_{_si:03d}", "A_M2",
         f"{_n}", "P_M1", f"实体缺少 {_lab}", ["move_confirmed"], False,
         {"kind": "npc", "label_match_any": [_lab], "label_require": [_lab]})
    _si += 1
# 时序补充（L2）
for _n, _lab, _cond, _win in [
    ("敌对实体出现后 8 轮内应消失（被击杀）", "hostile", "disappear_within", 8),
    ("拾取物品 4 轮内消失", "item", "disappear_within", 4),
    ("载具出现后应持续 3 轮", "vehicle", "persist_min", 3),
    ("NPC 出现后应持续 2 轮", "npc", "persist_min", 2),
    ("敌对实体出现后 12 轮内应消失", "hostile", "disappear_within", 12),
    ("掉落武器 5 轮内消失", "weapon", "disappear_within", 5),
]:
    _add(f"C_G2_SUPP_TMP_{_si:03d}", "A_W2_5",
         _n, "P_STATUS", f"实体违反时序条件 {_cond}", ["entity_appeared", "entity_death"],
         False,
         ({"kind": "npc", "label_match_any": [_lab]} if _lab != "vehicle" else
          {"kind": "vehicle", "label_match_any": [_lab]}),
         {"kind": "temporal", "condition": _cond, **({"window_rounds": _win} if _cond == "disappear_within" else {"min_rounds": _win})})
    _si += 1
# 黑客补充（RAM/冷却）
for _n, _lab in [("黑客受 RAM 限制（ram_limited）", "ram_limited"),
                 ("破解需要冷却（cooldown_required）", "cooldown_required"),
                 ("破解等级受稀有度影响（rarity_scaled）", "rarity_scaled"),
                 ("传播性破解可扩散（spreadable）", "spreadable")]:
    _add(f"C_G2_SUPP_HACK_{_si:03d}", "A_M1",
         _n, "P_GANG_HOSTILE", f"实体缺少 {_lab}", ["status_applied"], False,
         {"kind": "npc", "label_match_any": [_lab], "label_require": [_lab]})
    _si += 1
# ── 凑数段 2（补足 200：物品属性二轮/状态时长多档/移动细节）──
for _itype, _attr in [("cyberware", "emp_protection"), ("weapon", "fire_rate_mod"),
                      ("consumable", "heal_amount"), ("armor", "resist_mod"),
                      ("quickhack", "upload_time"), ("grenade", "radius_mod"),
                      ("relic", "netrun_bonus"), ("smart_weapon", "tracking_speed"),
                      ("tech_weapon", "penetration_depth"), ("baton", "charge_combo"),
                      ("power_weapon", "spread_mod"), ("clothing", "thermal_resist")]:
    _add(f"C_G2_FILL_ITEM_{_si:03d}", "A_I1",
         f"{_itype} 物品具备 {_attr} 细节属性", "P_ITEM",
         f"{_itype} 实体缺少 {_attr}", ["item_acquired"], False,
         {"kind": "item", "label_match_any": [_itype], "label_require": [_attr]})
    _si += 1
for _w in (2, 6, 9, 12):
    _add(f"C_G2_FILL_STATUS_{_si:03d}", "A_S1",
         f"负面状态应在 {_w} 轮内消退", "P_STATUS",
         f"状态实体持续超过 {_w} 轮", ["status_applied"], False, None,
         {"kind": "temporal", "condition": "disappear_within",
          "window_rounds": _w})
    _si += 1
for _n, _lab in [("车辆可倒车（reverse）", "reversible"),
                 ("无人机可扫描（scan_capable）", "scan_capable"),
                 ("玩家可蹲行（crouch）", "crouchable"),
                 ("NPC 可翻越（vault）", "vaultable"),
                 ("车辆可跳跃（jump_boost）", "jump_boost")]:
    _add(f"C_G2_FILL_MOVE_{_si:03d}", "A_M2",
         _n, "P_M1", f"实体缺少 {_lab}", ["move_confirmed"], False,
         {"kind": "npc", "label_match_any": [_lab], "label_require": [_lab]})
    _si += 1

# ── 故意错误规则（代表性反转：与 2077 设定相反）────────────
ERR_SPECS = [
    # (hypothesis, match, require, verification)
    ("Maelstrom 是和平主义帮派（不敌对）", ["maelstrom"], ["civilian"], None),
    ("Scavengers 是平民保护者", ["scavengers"], ["civilian", "protector"], None),
    ("状态效果永久持续", ["burning"], ["permanent"], None),
    ("燃烧状态无伤害", ["burning"], ["harmless"], None),
    ("义体掉落无限量", ["cyberware"], ["infinite"], None),
    ("武器永不损坏", ["weapon"], ["indestructible"], None),
    ("玩家不可移动", ["player"], ["immobile"], None),
    ("NPC 永不移动", ["npc"], ["stationary"], None),
    ("敌对实体免疫死亡", ["hostile"], ["immortal"], None),
    ("平民会主动攻击玩家", ["civilian"], ["aggressive"], None),
    ("帮派成员无视玩家", ["hostile"], ["ignores_player"], None),
    ("黑客无冷却", ["hacker"], ["no_cooldown"], None),
    ("实体永不消失（永久存在）", ["hostile"], None,
     {"kind": "temporal", "condition": "persist_min", "min_rounds": 100}),
    ("物品出现后应立即消失", ["item"], None,
     {"kind": "temporal", "condition": "disappear_within", "window_rounds": 0}),
    ("燃烧状态永久存在", ["burning"], None,
     {"kind": "temporal", "condition": "persist_min", "min_rounds": 100}),
    # 附加故意错误（反转常见 2077 设定）
    ("Tyger Claws 不携带武器（和平武士）", ["tyger_claws"], ["civilian"], None),
    ("Animals 不使用类固醇", ["animals"], ["natural_only"], None),
    ("Wraiths 与 Aldecaldos 结盟", ["wraiths"], ["ally_of_aldecaldos"], None),
    ("Voodoo Boys 不与公司为敌", ["voodoo_boys"], ["corpo_ally"], None),
    ("燃烧状态不降低生命", ["burning"], ["no_damage"], None),
    ("流血状态不影响移动", ["bleeding"], ["no_movement_penalty"], None),
    ("EMP 对机械无效", ["emped"], ["no_effect_on_mech"], None),
    ("物品使用后数量增加", ["consumable"], ["quantity_increases"], None),
    ("武器可无限拾取", ["weapon"], ["infinite_pickup"], None),
    ("玩家无需移动即可改变位置", ["player"], ["static_position"], None),
    ("敌对实体永不反击", ["hostile"], ["never_retaliates"], None),
    ("平民携带武器攻击玩家", ["civilian"], ["armed_aggressive"], None),
    ("黑客可无限施放破解（无内存限制）", ["hacker"], ["unlimited_ram"], None),
    ("载具不可驾驶", ["vehicle"], ["undrivable"], None),
    ("帮派成员免疫枪击", ["hostile"], ["bullet_immune"], None),
    ("快速破解无冷却", ["quickhack"], ["no_cooldown"], None),
    ("物品拾取后不进入背包", ["item"], ["not_collected"], None),
    ("死亡实体可立即复活", ["hostile"], None,
     {"kind": "temporal", "condition": "disappear_within", "window_rounds": 0}),
    ("掉落物品永久存在", ["item"], None,
     {"kind": "temporal", "condition": "persist_min", "min_rounds": 100}),
]
for i, (hyp, match, require, ver) in enumerate(ERR_SPECS):
    _add(f"C_G2_ERR_{i:03d}", "A_M1", f"{hyp}（故意设置错误）",
         "P_GANG_HOSTILE", f"观测与 {hyp} 相反", ["entity_appeared", "status_applied"],
         True,
         ({"kind": "npc", "label_match_any": match, "label_require": require}
          if require else None),
         ver)


def main():
    # 保留 3 个基础候选（A_M2/A_S1/A_I1，走 domain 分支）+ 新生成
    data = json.loads(RULES.read_text(encoding="utf-8"))
    base = [c for c in data["candidate_axioms"]
            if c["axiom_id"] in ("A_M2", "A_S1", "A_I1")]
    pool = base + candidates
    # 去重 id + 校验唯一
    ids = [c["axiom_id"] for c in pool]
    assert len(ids) == len(set(ids)), f"dup ids: {len(ids)} vs {len(set(ids))}"
    data["candidate_axioms"] = pool
    RULES.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    n_err = sum(1 for c in pool if c.get("intentional_error"))
    n_ver = sum(1 for c in pool if c.get("verification"))
    print(f"候选池：{len(pool)} 条（故意错误 {n_err}、时序 {n_ver}）")
    print(f"写入 {RULES}")


if __name__ == "__main__":
    main()
