"""W1 因果物理规则层——唯一真值，全程只读。

对应 CUF v7 D 层（度规因果结构）+ M 静态部分。
W2 层通过 get_ground_truth 查询客观真值，通过 is_valid_counterexample 判定反例有效性。
W1 永远不接受 W2 写回（反套壳铁则 #2）。
"""

import json
import os

from common.utils import dist3

_RULES_PATH = os.path.join(os.path.dirname(__file__), "world_rules.json")


def _load_rules(path=None):
    p = path or _RULES_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


class RuleValidator:
    """W1 规则校验器——硬编码判定，无 LLM 参与（反套壳铁则 #1）。

    两个核心接口：
    - get_ground_truth(query): 输入标签/事件/上下文，返回客观真值
    - is_valid_counterexample(axiom_id, observation): 判定观测是否构成有效反例
    """

    def __init__(self, rules_path=None):
        self._data = _load_rules(rules_path)
        self._rules = self._data.get("rules", [])
        self._axioms = self._data.get("axioms", [])
        self.scan_radius = self._data.get("scan_radius", 50.0)
        self.scan_limit = self._data.get("scan_limit", 30)
        # S29.17 P2 性能：候选池静态缓存 + id 索引——此前每次
        # is_valid_counterexample 全量拷贝列表并线性扫描，候选池×实体
        # 双层循环下是热点。
        self._candidates = list(self._data.get("candidate_axioms", []))
        self._candidate_index = {c.get("axiom_id"): c
                                 for c in self._candidates}

    # ── 查询接口 ──────────────────────────────────────────

    def get_axioms(self):
        """返回全部实验公理定义。"""
        return list(self._axioms)

    def get_candidates(self):
        """返回候选公理池（S28 自主激活机制：候选由 ActivationEngine
        依据观测证据自主激活，激活后进入活跃公理集参与正常实验）。"""
        return list(self._candidates)

    def get_candidate(self, axiom_id):
        """按 id 查找候选公理（O(1) 索引，S29.17 P2）。"""
        return self._candidate_index.get(axiom_id)

    def get_rules(self, axiom_id=None):
        """返回规则列表，可按 axiom_id 过滤。"""
        if axiom_id is None:
            return list(self._rules)
        return [r for r in self._rules if r.get("axiom_id") == axiom_id]

    def get_ground_truth(self, query):
        """输入查询，返回客观真值。

        query 格式：
        {
            "entity": {"kind": "npc", "labels": ["hostile"], "hp": 50},
            "event": {"kind": "combat_attack", "target_entity": "EID:123"},
            "context": {
                "distance_to_player": 15.0,
                "hp_before": 80,
                "hp_after": 50,
                "entity_in_scan": true,
                "entity_in_later_scan": false,
                "death_observed": true
            }
        }

        返回：
        {
            "truths": [{"rule_id": "R_M1_1", "description": "...",
                        "expected": {...}, "matches": bool, "observable": bool}],
            "summary": "..."
        }

        observable 语义：True 表示本条查询携带了足以判定该规则
        符合/违反的观测证据；False 表示前件未触发或证据缺失
        （此时 matches 为真值空满足，不构成实证支持）。
        """
        entity = query.get("entity", {})
        event = query.get("event", {})
        context = query.get("context", {})
        results = []

        event_kind = event.get("kind", "")
        entity_kind = entity.get("kind", "")
        entity_hp = entity.get("hp")
        distance = context.get("distance_to_player")
        hp_before = context.get("hp_before")
        hp_after = context.get("hp_after")
        entity_in_scan = context.get("entity_in_scan")
        entity_in_later_scan = context.get("entity_in_later_scan")
        death_observed = context.get("death_observed")

        for rule in self._rules:
            rid = rule["rule_id"]
            conditions = rule.get("conditions", {})
            truth = rule.get("ground_truth", {})

            # R_M1_1: combat_attack target kind check
            if rid == "R_M1_1" and event_kind == "combat_attack":
                target_kind = entity_kind
                damagable = target_kind in conditions.get("target_kind_in", [])
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"target_kind": target_kind, "is_damagable": damagable},
                    "matches": damagable,
                    "observable": True,
                })

            # R_M1_2: hp should decrease after combat_attack
            if rid == "R_M1_2" and event_kind == "combat_attack":
                if hp_before is not None and hp_after is not None:
                    decreased = hp_after < hp_before
                    results.append({
                        "rule_id": rid,
                        "description": rule["description"],
                        "expected": truth,
                        "actual": {"hp_before": hp_before, "hp_after": hp_after, "decreased": decreased},
                        "matches": decreased,
                        "observable": True,
                    })

            # R_M1_3: hp >= 0
            if rid == "R_M1_3" and entity_hp is not None:
                non_negative = entity_hp >= 0
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"hp": entity_hp, "non_negative": non_negative},
                    "matches": non_negative,
                    "observable": True,
                })

            # R_M1_4: hp max by kind
            if rid == "R_M1_4" and entity_kind and entity_hp is not None:
                hp_max = truth.get("hp_max", {}).get(entity_kind, 100)
                within_max = entity_hp <= hp_max
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"kind": entity_kind, "hp": entity_hp, "hp_max": hp_max, "within_max": within_max},
                    "matches": within_max,
                    "observable": True,
                })

            # R_W2_1: hp <= 0 should trigger entity_death
            if rid == "R_W2_1" and entity_hp is not None:
                should_die = entity_hp <= 0
                if not should_die:
                    matches, observable = True, False
                else:
                    death_evidence = (event_kind == "entity_death") or (death_observed is True)
                    still_present = entity_in_later_scan is True
                    if still_present or death_observed is False:
                        matches, observable = False, True
                    elif death_evidence:
                        matches, observable = True, True
                    else:
                        matches, observable = True, False
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"hp": entity_hp, "should_die": should_die,
                               "death_evidence": should_die and (event_kind == "entity_death" or death_observed is True),
                               "still_present_after": entity_in_later_scan},
                    "matches": matches,
                    "observable": observable,
                })

            # R_W2_2: entity_death → entity gone from scan
            if rid == "R_W2_2" and event_kind == "entity_death":
                gone = entity_in_later_scan if entity_in_later_scan is not None else entity_in_scan
                if gone is None:
                    matches, observable = True, False
                else:
                    matches, observable = (not gone), True
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"event_kind": event_kind,
                               "entity_still_in_scan": gone},
                    "matches": matches,
                    "observable": observable,
                })

            # R_W1_1: distance > scan_radius → not visible
            if rid == "R_W1_1" and distance is not None:
                beyond = distance > self.scan_radius
                if not beyond:
                    matches, observable = True, False
                elif entity_in_scan is None:
                    matches, observable = True, False
                else:
                    matches, observable = (not entity_in_scan), True
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"distance": distance, "beyond_radius": beyond,
                               "should_be_visible": not beyond,
                               "entity_in_scan": entity_in_scan},
                    "matches": matches,
                    "observable": observable,
                })

            # R_W1_2: distance <= scan_radius → visible
            if rid == "R_W1_2" and distance is not None:
                within = distance <= self.scan_radius
                if not within:
                    matches, observable = True, False
                elif entity_in_scan is None:
                    matches, observable = True, False
                else:
                    matches, observable = bool(entity_in_scan), True
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"distance": distance, "within_radius": within,
                               "should_be_visible": within,
                               "entity_in_scan": entity_in_scan},
                    "matches": matches,
                    "observable": observable,
                })

            # R_W1_3: player always visible
            if rid == "R_W1_3" and entity_kind == "player":
                if entity_in_scan is None:
                    matches, observable = True, False
                else:
                    matches, observable = bool(entity_in_scan), True
                results.append({
                    "rule_id": rid,
                    "description": rule["description"],
                    "expected": truth,
                    "actual": {"kind": "player", "always_visible": True,
                               "entity_in_scan": entity_in_scan},
                    "matches": matches,
                    "observable": observable,
                })

        summary = f"{len(results)} truths matched for entity_kind={entity_kind}, event_kind={event_kind}"
        return {"truths": results, "summary": summary}

    # ── 反例校验 ──────────────────────────────────────────

    def _check_candidate_assertion(self, cand, entity):
        """候选公理断言检查（S29.7：2077 设定候选池）。

        候选规则声明 assertion{kind, label_match_any, label_require}：
        - label_match_any：命中任一标签即触发该断言的验证（帮派等）
        - label_require：断言该类实体必须携带的标签；缺失任一 → 违反
        故意错误规则（intentional_error=true）的 label_require 与 2077
        真实设定相反 → 真实观测必违反 → 反例 → 证伪/降级。
        """
        a = cand.get("assertion") or {}
        if not a:
            return None
        # S29.17 P2：两侧标签统一小写归一——此前实体侧小写、
        # 断言侧不归一，大小写漂移时判定口径矛盾。
        labels = [str(x).lower() for x in (entity.get("labels") or [])]
        match_any = [str(m).lower() for m in (a.get("label_match_any") or [])]
        kind = a.get("kind")
        if match_any and not any(m in labels for m in match_any):
            return None
        if kind and entity.get("kind") != kind:
            return None
        require = [str(r).lower() for r in (a.get("label_require") or [])]
        missing = [r for r in require if r not in labels]
        if missing:
            return {
                "rule_id": cand["axiom_id"],
                "reason": (f"{cand['axiom_id']} 断言违反：实体 labels 缺 "
                           f"{missing}（假设：{cand['hypothesis']}）"),
            }
        return None

    def _check_candidate_verification(self, cand, entity, events, context):
        """候选规则时序/因果 verification 判定（S29.9 L2 跨轮时序）。

        在 assertion（纯标签约束）之外，候选规则可声明 verification：
        - kind=temporal：跨轮窗口语义（disappear_within / persist_min），
          依赖 runner 提供的跨轮 context（first_seen_round /
          rounds_since_first_seen / disappeared）；
        - kind=event_effect：事件→状态因果（如事件后 hp 应下降、
          事件后实体应移除），依赖 context 的 hp_before/hp_after 与
          entity_in_later_scan。
        """
        v = cand.get("verification") or {}
        if not v:
            return None
        kind = v.get("kind")
        if kind == "temporal":
            return self._check_temporal(cand, v, entity, context)
        if kind == "event_effect":
            return self._check_event_effect(cand, v, events, context)
        return None

    def _check_temporal(self, cand, v, entity, context):
        cond = v.get("condition")
        first_seen = context.get("first_seen_round")
        elapsed = context.get("rounds_since_first_seen")
        disappeared = context.get("disappeared") is True
        eid = entity.get("entity_id")
        if cond == "disappear_within":
            # 持续语义说明（S29.17 P2 落档）：实体超窗仍存在的**每一轮**
            # 都新记一次违反——持续状态可反复取证是设计语义，与
            # warn=3/downgrade=5 小阈值叠加会使长期滞留实体快速累计
            # 反例（有意为之：对"应消失而不消失"施加强压力）。
            window = int(v.get("window_rounds", 5))
            if first_seen is not None and elapsed is not None:
                if not disappeared and elapsed > window:
                    return {
                        "rule_id": cand["axiom_id"],
                        "reason": (f"{cand['axiom_id']} 时序违反：实体 "
                                   f"{eid} 出现 {elapsed} 轮未消失"
                                   f"（应在 {window} 轮内）"),
                    }
        elif cond == "persist_min":
            min_r = int(v.get("min_rounds", 3))
            if disappeared and elapsed is not None and elapsed < min_r:
                return {
                    "rule_id": cand["axiom_id"],
                    "reason": (f"{cand['axiom_id']} 时序违反：实体 {eid} "
                               f"第 {elapsed} 轮即消失（应持续 ≥{min_r} 轮）"),
                }
        return None

    def _check_event_effect(self, cand, v, events, context):
        ek = v.get("event_kind")
        if not ek:
            return None
        evs = [e for e in events if e.get("kind") == ek]
        if not evs:
            return None  # 前件未触发，不构成违反
        if v.get("expect_hp_decrease"):
            hb = context.get("hp_before")
            ha = context.get("hp_after")
            if hb is not None and ha is not None and ha >= hb:
                return {
                    "rule_id": cand["axiom_id"],
                    "reason": (f"{cand['axiom_id']} 因果违反：事件 {ek} 后 "
                               f"hp 未下降（{hb} -> {ha}）"),
                }
        if v.get("expect_entity_removed"):
            if context.get("entity_in_later_scan") is not False:
                return {
                    "rule_id": cand["axiom_id"],
                    "reason": (f"{cand['axiom_id']} 因果违反：事件 {ek} 后 "
                               f"实体仍在扫描中（应被移除）"),
                }
        return None

    def is_valid_counterexample(self, axiom_id, observation):
        """判定观测是否构成有效反例。

        反套壳铁则 #4：每次判定必须对应明确的观测数据与阈值触发记录。
        降级保护（锁定决策 #4）：反例须来自 >=2 种不同标签组合的实体。

        observation 格式：
        {
            "entity": {"entity_id": "EID:123", "kind": "npc", "labels": ["hostile"], "hp": 0},
            "events": [{"kind": "combat_attack", "target_entity": "EID:123", ...}],
            "context": {
                "distance_to_player": 15.0,
                "hp_before": 80,
                "hp_after": 80,
                "entity_in_scan": true,
                "entity_in_later_scan": true
            },
            "label_history": [["hostile"], ["civilian"]]
        }

        返回：
        {
            "is_valid": bool,
            "axiom_id": str,
            "violated_rules": [{"rule_id": str, "reason": str}],
            "label_diversity_ok": bool,
            "reason": str
        }
        """
        entity = observation.get("entity", {})
        events = observation.get("events", [])
        context = observation.get("context", {})
        label_history = observation.get("label_history", [])

        violated = []

        # ── A_M1 反例判定：攻击后 HP 未下降 ──
        # 注意：无 combat_attack 事件的观测不构成 A_M1 反例（前件未触发），
        # 此前的"无攻击事件→记违反"逻辑会产生伪反例，已删除。
        if axiom_id == "A_M1":
            hp_before = context.get("hp_before")
            hp_after = context.get("hp_after")
            combat_events = [e for e in events if e.get("kind") == "combat_attack"]
            labels = [str(x).lower() for x in (entity.get("labels") or [])]
            attacked = False

            for ce in combat_events:
                target = ce.get("target_entity", "")
                ent_id = entity.get("entity_id", "")
                # 前件：只有以当前实体为目标的攻击才构成该实体的反例。
                # （修复 2026-08-25：R_M1_1 曾误判为「target != entity」的
                # 旁观 mismatch，导致遍历到的非目标实体全被记反例、A_M1
                # 被冤枉降级；现回到定义语义 = 目标 kind 是否可伤害。）
                if not target or target != ent_id:
                    continue
                attacked = True
                if entity.get("kind", "") not in ("npc", "player", "vehicle"):
                    # R_M1_1：攻击目标必须是可受伤害类型（device/item/unknown 违规）
                    violated.append({
                        "rule_id": "R_M1_1",
                        "reason": (f"attack target kind {entity.get('kind', '')} "
                                   f"not damagable"),
                    })
                elif "civilian" in labels and "hostile" not in labels:
                    # R_M1_5：误伤非敌对平民（S29.5 planner 批次补全）
                    violated.append({
                        "rule_id": "R_M1_5",
                        "reason": f"combat_attack on civilian: {target}",
                    })

            # R_M1_2/R_M1_3（HP 变化）只对「被攻击实体」判定——非目标实体
            # 不构成 A_M1 反例（修复 2026-08-25：原 `if combat_events:` 全局
            # 判定，遍历旁观实体时也会带 hp 上下文误判）。
            if attacked:
                if hp_before is not None and hp_after is not None:
                    if hp_after >= hp_before:
                        violated.append({
                            "rule_id": "R_M1_2",
                            "reason": f"hp not decreased: before={hp_before}, after={hp_after}"
                        })

                if hp_after is not None and hp_after < 0:
                    violated.append({
                        "rule_id": "R_M1_3",
                        "reason": f"hp negative: {hp_after}"
                    })

        # ── A_W2_5 反例判定：HP 归零但未死亡/未消失 ──
        elif axiom_id == "A_W2_5":
            hp = entity.get("hp")
            death_events = [e for e in events if e.get("kind") == "entity_death"]
            disappear_events = [e for e in events if e.get("kind") == "entity_disappeared"]
            entity_in_later_scan = context.get("entity_in_later_scan")

            if hp is not None and hp <= 0:
                if not death_events:
                    violated.append({
                        "rule_id": "R_W2_1",
                        "reason": f"hp={hp} <= 0 but no entity_death event"
                    })
                if entity_in_later_scan is True:
                    violated.append({
                        "rule_id": "R_W2_2",
                        "reason": "entity still in scan after death"
                    })
                labels = entity.get("labels", [])
                if "hostile" in labels and entity_in_later_scan is True:
                    violated.append({
                        "rule_id": "R_W2_3",
                        "reason": "hostile NPC respawned after death"
                    })

                # entity_death target mismatch（证据归属校验）：本实体
                # 已处死亡状态（hp<=0）且轮内存在死亡事件，但没有一条
                # 指向本实体 → 死亡记录归属错误。仅对死亡实体自身判定，
                # 不得对旁观实体记违反（与 R_M1_1 修复同理：整轮事件流
                # 判实体级反例必须校验 target 归属）。
                if death_events:
                    targets = {de.get("target_entity", "")
                               for de in death_events}
                    ent_id = entity.get("entity_id", "")
                    if ent_id and ent_id not in targets:
                        violated.append({
                            "rule_id": "R_W2_4",
                            "reason": (f"entity {ent_id} hp<=0 but death "
                                       f"events target {sorted(targets)}")
                        })

        # ── A_W1 反例判定：超出半径的实体出现在 scan 中 ──
        elif axiom_id == "A_W1":
            # S29.17：感知规则以实体存在为前提——消失实体（不在本轮
            # 扫描）只为时序验证而参与遍历，不得做感知成员判定，否则
            # 「半径内但不在扫描」对消失实体必然成立、构成伪反例
            # （消失即不在扫描，是定义而非感知失效）。
            if context.get("disappeared") is not True:
                distance = context.get("distance_to_player")
                entity_in_scan = context.get("entity_in_scan")
                entity_kind = entity.get("kind", "")

                if distance is not None and entity_in_scan is True and entity_kind != "player":
                    if distance > self.scan_radius:
                        violated.append({
                            "rule_id": "R_W1_1",
                            "reason": f"entity at distance {distance} > radius {self.scan_radius} but in scan"
                        })

                if distance is not None and entity_in_scan is False and entity_kind != "player":
                    if distance <= self.scan_radius:
                        violated.append({
                            "rule_id": "R_W1_2",
                            "reason": f"entity at distance {distance} <= radius {self.scan_radius} but not in scan"
                        })

                if entity_kind == "player" and entity_in_scan is False:
                    violated.append({
                        "rule_id": "R_W1_3",
                        "reason": "player not in scan"
                    })

        # ── A_M2 反例判定：移动后位置应更新（S28 候选公理）──
        elif axiom_id == "A_M2":
            moved = context.get("moved")
            entity_in_old_scan = context.get("entity_in_old_scan")
            entity_in_goal_scan = context.get("entity_in_goal_scan")

            if moved:
                if entity_in_old_scan is True:
                    violated.append({
                        "rule_id": "R_M2_2",
                        "reason": "moved but entity still in old position scan"
                    })
                if entity_in_goal_scan is False:
                    violated.append({
                        "rule_id": "R_M2_1",
                        "reason": "moved but entity not appearing at goal scan"
                    })

        # ── A_S1 反例判定：状态效果生命周期（S28 候选公理）──
        elif axiom_id == "A_S1":
            status_applied = context.get("status_applied")
            status_present = context.get("status_present")
            elapsed = context.get("elapsed_rounds")
            max_expected = context.get("max_expected_rounds")

            if status_applied and status_present is False:
                violated.append({
                    "rule_id": "R_S1_1",
                    "reason": "status applied but no presence evidence"
                })
            if status_present and elapsed is not None and max_expected is not None:
                if elapsed > max_expected:
                    violated.append({
                        "rule_id": "R_S1_2",
                        "reason": (f"status persists {elapsed} rounds > "
                                   f"expected max {max_expected} (permanent)")
                    })

        # ── A_I1 反例判定：物品数量守恒（S28 候选公理）──
        elif axiom_id == "A_I1":
            item_action = context.get("item_action")
            qty_before = context.get("qty_before")
            qty_after = context.get("qty_after")

            if item_action == "acquired" and qty_before is not None \
                    and qty_after is not None:
                if qty_after <= qty_before:
                    violated.append({
                        "rule_id": "R_I1_1",
                        "reason": (f"acquired but qty not increased: "
                                   f"{qty_before} -> {qty_after}")
                    })
            if item_action == "used" and qty_before is not None \
                    and qty_after is not None:
                if qty_after >= qty_before:
                    violated.append({
                        "rule_id": "R_I1_2",
                        "reason": (f"used but qty not decreased: "
                                   f"{qty_before} -> {qty_after}")
                    })
            if qty_after is not None and qty_after < 0:
                violated.append({
                    "rule_id": "R_I1_3",
                    "reason": f"negative inventory qty: {qty_after}"
                })

        else:
            # ── 候选公理断言路由（S29.7：2077 设定候选池）──
            # 候选 axiom_id 不在已知分支 → 查候选池断言检查；
            # 仍不认识 → unknown（fail-loud，禁静默忽略）。
            cand = self.get_candidate(axiom_id)
            if cand is not None:
                violation = (self._check_candidate_assertion(cand, entity)
                             or self._check_candidate_verification(
                                 cand, entity, events, context))
                if violation:
                    violated.append(violation)
            else:
                return {
                    "is_valid": False,
                    "axiom_id": axiom_id,
                    "violated_rules": [],
                    "label_diversity_ok": False,
                    "reason": f"unknown axiom_id: {axiom_id}"
                }

        # ── 降级保护：标签组合多样性检查（锁定决策 #4）──
        # S29.17 P2：组合归一（小写 + 排序）与候选断言口径一致；
        # 空组合不计为有效变体（与公理引擎闸门 B 对齐）。
        distinct_label_sets = set()
        for ls in label_history:
            if isinstance(ls, list):
                combo = tuple(sorted(str(x).lower() for x in ls))
                if combo:
                    distinct_label_sets.add(combo)
            elif isinstance(ls, str) and ls:
                distinct_label_sets.add((ls.lower(),))
        label_diversity_ok = len(distinct_label_sets) >= 2

        is_valid = len(violated) > 0 and label_diversity_ok

        if not violated:
            reason = "no rule violations detected"
        elif not label_diversity_ok:
            reason = (f"violations found but label diversity insufficient: "
                      f"{len(distinct_label_sets)} < 2 (降级保护)")
        else:
            reason = f"{len(violated)} rule violation(s), label diversity OK"

        return {
            "is_valid": is_valid,
            "axiom_id": axiom_id,
            "violated_rules": violated,
            "label_diversity_ok": label_diversity_ok,
            "reason": reason,
        }

    # ── 辅助 ──────────────────────────────────────────────

    def compute_distance(self, pos_a, pos_b):
        """计算两个 position 的 3D 距离。"""
        return dist3((pos_a.get("x", 0), pos_a.get("y", 0), pos_a.get("z", 0)),
                     (pos_b.get("x", 0), pos_b.get("y", 0), pos_b.get("z", 0)))

    def is_within_scan(self, entity_pos, player_pos):
        """判定实体是否在扫描半径内。"""
        return self.compute_distance(entity_pos, player_pos) <= self.scan_radius
