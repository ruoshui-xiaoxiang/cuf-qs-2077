"""W2 降级管理器——反例驱动的公理降级与边界自动补全。

对应 CUF v7 W₂ 稳态自指体的公理降级面。
反套壳铁则 #1：降级触发全部由硬编码阈值判定，LLM 无裁决权。
锁定决策 #4：反例须来自 >=2 种不同标签组合的实体才累计触发。
锁定决策 #11：C_H,extend = min(累计反例数, 降级阈值)（逻辑学审查 A：
随反例数单调递增，饱和于阈值，保持 bounded 代理度量）。
锁定决策 #13：轮内判定正常计算，状态变更轮间生效。
"""

import logging
import time
import uuid

log = logging.getLogger("downgrade_manager")

_BC_KEY_FIELDS = ("kind", "labels", "event", "constraint", "note",
                  "before", "after")


def _boundary_semantic_key(bc):
    """边界条件的语义指纹（不含 source 等溯源标记）。

    全量结构比较：只比 (kind, labels) 时，事件/约束类边界两键皆
    None 会互相判重，语义不同的边界被静默丢弃（P0 修复）。
    """
    if not isinstance(bc, dict):
        return ("raw", str(bc))
    key = []
    for field in _BC_KEY_FIELDS:
        value = bc.get(field)
        if isinstance(value, list):
            value = tuple(value)
        key.append((field, value))
    return tuple(key)


class DowngradeManager:
    """降级管理器——协调 axiom_engine 与 qs_kernel 的降级流程。

    职责：
    1. 接收假设结果，通过 axiom_engine 记录反例
    2. 检查降级触发（3次预警 / 5次触发，多标签保护）
    3. 预警/降级时自动补全边界条件（从反例中提取；warn 也补全但
       不重算 g——见 process_counterexample 分支）
    4. 降级后通知 qs_kernel 重算目标向量 g
    5. 轮内判定、轮间生效（通过 axiom_engine 的 pending 机制）
    """

    STATUS_ACTIVE = "active"
    STATUS_WARNED = "warned"
    STATUS_DEPRECATED = "deprecated"

    def __init__(self, axiom_engine, qs_kernel=None,
                 warn_threshold=3, downgrade_threshold=5):
        self.axiom_engine = axiom_engine
        self.qs_kernel = qs_kernel
        self.warn_threshold = int(warn_threshold)
        self.downgrade_threshold = int(downgrade_threshold)
        self._downgrade_events = []
        self._warn_events = []
        self._boundary_auto_added = {}

    # ── 核心流程：反例处理 ────────────────────────────────

    def process_counterexample(self, axiom_id, hypothesis_result,
                               observation=None, qs_kernel=None):
        """处理一次反例观测——完整的降级判定流程。

        流程：
        1. 通过 axiom_engine 记录反例（含多标签保护）
        2. 检查是否触发预警/降级
        3. 降级时自动补全边界条件
        4. 降级后通知 qs_kernel 重算 g

        Args:
            axiom_id: 公理 ID
            hypothesis_result: axiom_engine.generate_hypothesis 的返回
            observation: 原始观测数据（用于提取边界条件）
            qs_kernel: 可选的 QS 内核（降级后重算 g）

        Returns:
            {
                "recorded": bool,
                "count": int,
                "label_variants": int,
                "action": "none" | "warn" | "downgrade",
                "new_status": str | None,
                "boundary_conditions_added": list,
                "g_recomputed": bool,
                "reason": str
            }
        """
        obs = observation or {}
        entity = obs.get("entity", {})
        label_combo = entity.get("labels", [])
        entity_kind = entity.get("kind", "unknown")

        # 审计 C1：入口显式契约（fail-loud）
        if not isinstance(axiom_id, str) or not axiom_id:
            raise TypeError("process_counterexample: axiom_id must be non-empty str")
        if not isinstance(hypothesis_result, dict):
            raise TypeError(
                f"process_counterexample: hypothesis_result must be dict, "
                f"got {type(hypothesis_result).__name__}")

        qs = qs_kernel or self.qs_kernel

        # 1. 通过 axiom_engine 记录反例（含多标签保护）
        result = self.axiom_engine.record_counterexample(
            axiom_id, hypothesis_result, label_combo=label_combo
        )

        if not result.get("recorded", False):
            return {
                "recorded": False,
                "count": result.get("count", 0),
                "label_variants": result.get("label_variants", 0),
                "action": "none",
                "new_status": None,
                "boundary_conditions_added": [],
                "g_recomputed": False,
                "reason": result.get("reason", "not recorded"),
            }

        count = result["count"]
        new_status = result.get("new_status")
        action = "none"

        # 2. 检查降级/预警
        if new_status == self.STATUS_DEPRECATED:
            action = "downgrade"
            # 安全检查 A4：事件记录 old/new 状态（五元组可逆推）
            old_status = (self.axiom_engine.get_axiom(axiom_id) or {}).get(
                "status", "unknown")
            self._downgrade_events.append({
                "axiom_id": axiom_id,
                "timestamp": time.time(),
                "count": count,
                "threshold": self.downgrade_threshold,
                "from_status": old_status,
                "to_status": new_status,
            })
            # 3. 降级时自动补全边界条件
            added = self._auto_complete_boundary(axiom_id, obs)
            # 4. 降级后通知 qs_kernel 重算 g
            g_recomputed = self._trigger_g_recompute(axiom_id, qs)
            return {
                "recorded": True,
                "count": count,
                "label_variants": result.get("label_variants", 0),
                "action": action,
                "new_status": new_status,
                "boundary_conditions_added": added,
                "g_recomputed": g_recomputed,
                "reason": f"downgraded: {count} >= {self.downgrade_threshold}",
            }

        elif new_status == self.STATUS_WARNED:
            action = "warn"
            # 安全检查 A4：事件记录 old/new 状态
            old_status = (self.axiom_engine.get_axiom(axiom_id) or {}).get(
                "status", "unknown")
            self._warn_events.append({
                "axiom_id": axiom_id,
                "timestamp": time.time(),
                "count": count,
                "threshold": self.warn_threshold,
                "from_status": old_status,
                "to_status": new_status,
            })
            # 预警时也补全边界条件（但不重算 g）
            added = self._auto_complete_boundary(axiom_id, obs)
            return {
                "recorded": True,
                "count": count,
                "label_variants": result.get("label_variants", 0),
                "action": action,
                "new_status": new_status,
                "boundary_conditions_added": added,
                "g_recomputed": False,
                "reason": f"warned: {count} >= {self.warn_threshold}",
            }

        return {
            "recorded": True,
            "count": count,
            "label_variants": result.get("label_variants", 0),
            "action": "none",
            "new_status": None,
            "boundary_conditions_added": [],
            "g_recomputed": False,
            "reason": f"recorded: count={count}, no threshold reached",
        }

    # ── 边界条件自动补全 ──────────────────────────────────

    def _auto_complete_boundary(self, axiom_id, observation):
        """从反例观测中自动提取边界条件并补全到公理。

        锁定决策 #4（M1）：规则边界单调细化，永不穷尽。
        从反例中提取 entity kind + labels 作为新边界条件。

        Args:
            axiom_id: 公理 ID
            observation: 反例观测数据

        Returns:
            新增的边界条件列表
        """
        entity = observation.get("entity", {})
        entity_kind = entity.get("kind", "unknown")
        entity_labels = entity.get("labels", [])
        entity_id = entity.get("entity_id", "")
        events = observation.get("events", [])
        context = observation.get("context", {})

        new_conditions = []

        # 从实体特征提取边界条件
        if entity_kind and entity_kind != "unknown":
            bc = {"kind": entity_kind}
            if entity_labels:
                bc["labels"] = list(entity_labels)
            # 标记来源
            bc["source"] = "auto_complete"
            bc["from_counterexample"] = True
            new_conditions.append(bc)

        # 证据事实（仅当观测上下文可支撑时才允许写入对应 note）
        hp_before = context.get("hp_before")
        hp_after = context.get("hp_after")
        hp_no_decrease = (hp_before is not None and hp_after is not None
                          and hp_after >= hp_before)
        still_present = (context.get("entity_in_later_scan",
                                     context.get("entity_in_scan")) is True)

        # 从事件特征提取边界条件（证据归属校验）：只认 target 指向本
        # 反例实体的事件，且 note 描述的事实必须有当轮观测证据支撑
        # （hp 未降 / 实体仍在扫描）——禁止拿整轮事件流做无归属的
        # 硬编码断言（捏造边界，P0 修复）。
        for ev in events:
            if not entity_id or ev.get("target_entity", "") != entity_id:
                continue
            ev_kind = ev.get("kind", "")
            if ev_kind == "combat_attack" and hp_no_decrease:
                bc = {"event": "combat_attack", "note": "hp not decreased"}
                bc["source"] = "auto_complete"
                new_conditions.append(bc)
            elif ev_kind == "entity_death" and still_present:
                bc = {"event": "entity_death", "note": "entity not gone"}
                bc["source"] = "auto_complete"
                new_conditions.append(bc)

        # 从上下文提取
        if hp_no_decrease:
            bc = {"constraint": "hp_no_decrease",
                  "before": hp_before, "after": hp_after}
            bc["source"] = "auto_complete"
            new_conditions.append(bc)

        # 去重后通过 axiom_engine 更新
        if new_conditions:
            added = []
            existing = self.axiom_engine.get_axiom(axiom_id)
            if existing:
                existing_bc = existing.get("boundary_conditions", [])
                for nc in new_conditions:
                    # 同公理内去重：全量语义指纹比较（P0 修复——旧
                    # (kind,labels) 键使无 kind 边界互相判重）
                    nc_key = _boundary_semantic_key(nc)
                    already = any(
                        _boundary_semantic_key(eb) == nc_key
                        for eb in existing_bc)
                    # 修复 3a（2026-08-25）：跨公理判重——真实世界标签少、
                    # 候选规则多时，不同公理会补出相同边界 → duplicate_coverage。
                    # 补全前检查其他公理是否已持有相同边界，若持有则跳过
                    # （缓解；根治靠主题红线 3 把候选池减至 15-20 条）。
                    # S29.17 F11：读生效边界（含同轮 pending），避免同轮
                    # 并行为两条公理补相同边界时漏判。
                    if not already:
                        for other in self.axiom_engine.get_all_axioms():
                            if other.get("id") == axiom_id:
                                continue
                            for ob in self.axiom_engine.get_effective_boundary(
                                    other.get("id")):
                                if _boundary_semantic_key(ob) == nc_key:
                                    already = True
                                    break
                            if already:
                                break
                    if not already:
                        added.append(nc)

                if added:
                    self.axiom_engine.update_axiom_boundary(axiom_id, added)
                    self._boundary_auto_added.setdefault(axiom_id, []).extend(added)
                    return added

        return []

    # ── 降级后重算 g ──────────────────────────────────────

    def _trigger_g_recompute(self, axiom_id, qs_kernel):
        """降级后通知 qs_kernel 重算目标向量 g。

        锁定决策 #1：g 用 7B 模型自身嵌入生成。
        降级后公理集变化，g 需要基于新的活跃公理集重算。
        在 S13 接通前，仅标记需要重算（返回 True/False）。

        Returns:
            True 如果已通知重算，False 如果 qs_kernel 未就绪
        """
        if qs_kernel is None:
            return False

        # 标记需要重算——实际重算在 S13 接通后由模型嵌入驱动
        # 当前阶段：清空历史，标记 g 需要重新生成
        # effective=True：轮内降级时正式状态尚未生效（锁定决策 #13），
        # 必须按生效状态判断剩余活跃公理，否则刚降级的公理仍被计入
        active_axioms = self.axiom_engine.get_active_axioms(effective=True)
        if not active_axioms:
            # 全部降级 → g 无效
            return False

        # 重置 QS 历史（降级后收敛基准变化）
        qs_kernel.reset()
        return True

    # ── 查询接口 ──────────────────────────────────────────

    def get_downgrade_events(self, axiom_id=None):
        """获取降级事件列表。"""
        if axiom_id is None:
            return list(self._downgrade_events)
        return [e for e in self._downgrade_events if e["axiom_id"] == axiom_id]

    def get_warn_events(self, axiom_id=None):
        """获取预警事件列表。"""
        if axiom_id is None:
            return list(self._warn_events)
        return [e for e in self._warn_events if e["axiom_id"] == axiom_id]

    def get_auto_added_boundary(self, axiom_id):
        """获取自动补全的边界条件。"""
        return list(self._boundary_auto_added.get(axiom_id, []))

    def get_c_h_extend(self, axiom_id=None):
        """计算 C_H,extend（锁定决策 #11 + 逻辑学审查 A）。

        C_H,extend = min(累计反例数, 降级阈值)
          —— 随反例数单调递增，饱和于阈值（bounded 代理度量）。
        """
        w2 = self.axiom_engine.export_w2_data()
        try:
            total_ce = int(w2["cumulative_counterexamples"])
            threshold = int(w2["downgrade_threshold"])
        except (TypeError, ValueError, KeyError) as e:
            # 安全检查 A1：不静默——核心度量异常必须留痕
            log.warning("C_H,extend computation failed, returning 0: %s", e)
            return 0
        if total_ce < 0:
            total_ce = 0
        return min(total_ce, threshold)

    # ── 轮次管理 ──────────────────────────────────────────

    def begin_round(self, round_id=None):
        """开始一轮——委托 axiom_engine 冻结快照。"""
        return self.axiom_engine.begin_round(round_id)

    def end_round(self):
        """结束一轮——委托 axiom_engine 应用 pending 变更。"""
        self.axiom_engine.end_round()

    # ── 状态导出 ──────────────────────────────────────────

    def get_status(self):
        """返回降级管理器状态快照。"""
        return {
            "warn_threshold": self.warn_threshold,
            "downgrade_threshold": self.downgrade_threshold,
            "total_downgrades": len(self._downgrade_events),
            "total_warnings": len(self._warn_events),
            "auto_added_boundary_count": sum(
                len(v) for v in self._boundary_auto_added.values()),
            "c_h_extend": self.get_c_h_extend(),
            "downgraded_axioms": list(set(
                e["axiom_id"] for e in self._downgrade_events
            )),
        }
