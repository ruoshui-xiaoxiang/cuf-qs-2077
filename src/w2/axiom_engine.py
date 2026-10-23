"""W2 公理引擎——公理注入、假设生成、边界更新。

对应 CUF v7 W₂ 稳态自指体的公理自治面。
反套壳铁则 #1：公理引擎为硬编码结构化存储与判定，LLM 仅做语义转译（S14）。
锁定决策 #13：轮内快照冻结——一轮内公理集合冻结，降级判定在轮内正常计算，
状态变更仅在轮间生效。
"""

import json
import os
import time
import uuid


class AxiomEngine:
    """公理引擎——管理公理生命周期。

    公理状态机：active → warned → deprecated
    - active: 正常运行，参与 QS 收敛
    - warned: 反例达到预警阈值（3次），仍运行但标记风险
    - deprecated: 反例达到降级阈值（5次），停止参与收敛

    轮内快照冻结（锁定决策 #13）：
    - begin_round() 冻结当前公理集合快照
    - 轮内可计算降级判定，但状态变更写入 pending
    - end_round() 将 pending 变更应用到正式状态
    """

    STATUS_ACTIVE = "active"
    STATUS_WARNED = "warned"
    STATUS_DEPRECATED = "deprecated"

    WARN_THRESHOLD = 3
    DOWNGRADE_THRESHOLD = 5

    def __init__(self, warn_threshold=WARN_THRESHOLD,
                 downgrade_threshold=DOWNGRADE_THRESHOLD):
        self.warn_threshold = int(warn_threshold)
        self.downgrade_threshold = int(downgrade_threshold)
        self._axioms = {}
        self._counterexamples = {}  # axiom_id → list of counterexample records
        self._label_variants = {}   # axiom_id → set of label tuples (降级保护)
        self._round_active = False
        self._round_snapshot = None
        self._pending_status = {}
        self._pending_boundary = {}
        self._model_update_count = 0
        self._round_id = 0

    # ── 公理注入 ──────────────────────────────────────────

    def add_axiom(self, axiom_id, hypothesis, anchor, boundary_conditions=None,
                  status=STATUS_ACTIVE):
        """注入一条公理。

        Args:
            axiom_id: 公理唯一标识（如 "A_M1"）
            hypothesis: 自然语言假设描述
            anchor: 对应的可证伪锚点（如 "P_M1"）
            boundary_conditions: 边界条件列表（每项为 dict 或 str）
            status: 初始状态

        Returns:
            公理对象 dict
        """
        if axiom_id in self._axioms:
            raise ValueError(f"axiom {axiom_id} already exists")

        axiom = {
            "id": axiom_id,
            "hypothesis": hypothesis,
            "anchor": anchor,
            "status": status,
            "boundary_conditions": list(boundary_conditions) if boundary_conditions else [],
            "created_at": time.time(),
            "updated_at": time.time(),
            "counterexample_count": 0,
            "label_variants": [],
        }
        self._axioms[axiom_id] = axiom
        self._counterexamples[axiom_id] = []
        self._label_variants[axiom_id] = set()
        return dict(axiom)

    # ── 查询 ──────────────────────────────────────────────

    def get_axiom(self, axiom_id):
        """获取单条公理（返回副本）。"""
        a = self._axioms.get(axiom_id)
        return dict(a) if a else None

    def get_all_axioms(self):
        """获取全部公理列表（返回副本）。"""
        return [dict(a) for a in self._axioms.values()]

    def get_active_axioms(self, effective=False):
        """获取活跃公理（status=active 或 warned）。

        Args:
            effective: True 时按"生效状态"（轮内优先读 pending 状态）过滤，
                       供降级后 g 重算等需要即时反映轮内判定的场景使用；
                       False（默认）按正式状态过滤，保持轮内快照冻结语义。
        """
        result = []
        for a in self._axioms.values():
            status = self.get_effective_status(a["id"]) if effective else a["status"]
            if status in (self.STATUS_ACTIVE, self.STATUS_WARNED):
                result.append(dict(a))
        return result

    def get_effective_status(self, axiom_id):
        """获取公理的生效状态：轮内存在 pending 变更时优先返回 pending。"""
        axiom = self._axioms.get(axiom_id)
        if not axiom:
            return None
        return self._pending_status.get(axiom_id, axiom["status"])

    def get_effective_boundary(self, axiom_id):
        """获取公理的生效边界条件：轮内存在 pending 边界时优先返回
        pending（供跨公理判重等需要看见同轮未落盘变更的场景，
        S29.17 F11）。"""
        axiom = self._axioms.get(axiom_id)
        if not axiom:
            return []
        if axiom_id in self._pending_boundary:
            return list(self._pending_boundary[axiom_id])
        return list(axiom.get("boundary_conditions", []) or [])

    def get_axiom_count(self, active_only=False):
        """获取公理数量。

        active_only 口径与 get_active_axioms/C_H struct 计数一致：
        active + warned（warned 仍是生效公理，S29.17 P2 口径统一）。
        """
        if active_only:
            return sum(1 for a in self._axioms.values()
                       if a["status"] in (self.STATUS_ACTIVE,
                                          self.STATUS_WARNED))
        return len(self._axioms)

    # ── 假设生成 ──────────────────────────────────────────

    def generate_hypothesis(self, axiom_id, observation, rule_validator=None):
        """基于观测生成假设（结构化，非 LLM）。

        反套壳铁则 #1：假设生成是结构化的规则匹配，不由 LLM 决定。
        LLM 仅在 S14 中将结构化假设自然语言化（标注"仅建议"）。

        Args:
            axiom_id: 关联的公理 ID
            observation: 观测数据（entity/events/context/label_history）
            rule_validator: W1 规则校验器（可选，用于反例验证）

        Returns:
            {
                "hypothesis_id": str,
                "axiom_id": str,
                "type": "confirm" | "counterexample" | "boundary_refine",
                "description": str,
                "evidence": dict,
                "is_counterexample": bool,
                "rule_validation": dict | None
            }
        """
        aid = axiom_id
        axiom = self._axioms.get(aid)
        if not axiom:
            return {"hypothesis_id": None, "error": f"unknown axiom: {aid}"}

        entity = observation.get("entity", {})
        events = observation.get("events", [])
        context = observation.get("context", {})
        # 注：label_history 由 rule_validator 的多样性闸门直接读取，
        # 此处无需取值（S29.17：移除死变量）。

        # 通过 W1 规则校验器判定是否为反例
        rule_result = None
        is_counterexample = False
        if rule_validator is not None:
            rule_result = rule_validator.is_valid_counterexample(aid, observation)
            is_counterexample = rule_result.get("is_valid", False)

        # 生成假设
        hid = f"H_{aid}_{uuid.uuid4().hex[:8]}"
        if is_counterexample:
            h_type = "counterexample"
            violated = rule_result.get("violated_rules", [])
            desc = (f"观测到 {aid} 的反例：{len(violated)} 条规则被违反"
                   f"（{', '.join(v['rule_id'] for v in violated)}）")
        else:
            # 检查是否需要边界细化
            entity_kind = entity.get("kind", "unknown")
            entity_labels = entity.get("labels", [])
            existing_bc = axiom.get("boundary_conditions", [])

            # 如果观测到的实体 kind/labels 不在现有边界条件中，建议补充
            new_label_combo = {"kind": entity_kind, "labels": list(entity_labels)}
            already_covered = False
            for bc in existing_bc:
                if isinstance(bc, dict):
                    # 标签集合比较与序无关（sorted 归一，S29.17 P2 修复：
                    # 此前顺序敏感导致同一标签集被判"未覆盖"反复建议）
                    if (bc.get("kind") == entity_kind and
                        sorted(bc.get("labels") or []) ==
                            sorted(entity_labels or [])):
                        already_covered = True
                        break
                elif isinstance(bc, str):
                    if entity_kind in bc:
                        already_covered = True
                        break

            if not already_covered and entity_kind != "unknown":
                h_type = "boundary_refine"
                desc = (f"建议为 {aid} 补充边界条件：kind={entity_kind}, "
                        f"labels={entity_labels}")
            else:
                h_type = "confirm"
                desc = f"观测确认 {aid} 假设成立，无反例"

        return {
            "hypothesis_id": hid,
            "axiom_id": aid,
            "type": h_type,
            "description": desc,
            "evidence": {
                "entity_id": entity.get("entity_id"),
                "entity_kind": entity.get("kind"),
                "event_count": len(events),
                "context": context,
            },
            "is_counterexample": is_counterexample,
            "rule_validation": rule_result,
        }

    # ── 边界更新 ──────────────────────────────────────────

    def update_axiom_boundary(self, axiom_id, new_conditions):
        """更新公理边界条件。

        锁定决策 #13：轮内调用写入 pending，轮间生效。
        锁定决策 #4（M1）：规则边界单调细化，永不穷尽——
        边界条件只增不减（除非公理降级）。

        Args:
            axiom_id: 公理 ID
            new_conditions: 新增边界条件列表

        Returns:
            更新后的边界条件列表
        """
        axiom = self._axioms.get(axiom_id)
        if not axiom:
            raise ValueError(f"unknown axiom: {axiom_id}")

        # 轮内多次更新必须基于已有 pending 累加，否则前批边界会被覆盖丢失
        if self._round_active and axiom_id in self._pending_boundary:
            existing = list(self._pending_boundary[axiom_id])
        else:
            existing = list(axiom.get("boundary_conditions", []))
        for nc in new_conditions:
            if nc not in existing:
                existing.append(nc)

        if self._round_active:
            self._pending_boundary[axiom_id] = list(existing)
        else:
            axiom["boundary_conditions"] = existing
            axiom["updated_at"] = time.time()

        return list(existing)

    # ── 反例记录与降级判定 ────────────────────────────────

    def record_counterexample(self, axiom_id, hypothesis_result, label_combo=None):
        """记录反例并检查是否触发降级。

        锁定决策 #4：反例须来自 >=2 种不同标签组合的实体才累计触发。
        轮内判定正常计算，状态变更轮间生效（锁定决策 #13）。

        Args:
            axiom_id: 公理 ID
            hypothesis_result: generate_hypothesis 的返回
            label_combo: 本次反例的标签组合（list[str]）

        Returns:
            {
                "recorded": bool,
                "count": int,
                "label_variants": int,
                "status": str,
                "status_changed": bool,
                "new_status": str | None
            }
        """
        axiom = self._axioms.get(axiom_id)
        if not axiom:
            return {"recorded": False, "error": f"unknown axiom: {axiom_id}"}

        # 审计 C1：入口显式契约（fail-loud，禁止静默忽略非法输入）
        if not isinstance(hypothesis_result, dict):
            raise TypeError(
                f"record_counterexample: hypothesis_result must be dict, "
                f"got {type(hypothesis_result).__name__}")
        if not isinstance(hypothesis_result.get("is_counterexample"), bool):
            raise TypeError(
                "record_counterexample: hypothesis_result.is_counterexample "
                "must be bool")
        if label_combo is not None and not (
                isinstance(label_combo, list)
                and all(isinstance(x, str) for x in label_combo)):
            raise TypeError(
                "record_counterexample: label_combo must be list[str] or None")
        # W1 溯源结构校验（S29.17 P2 加固）：合法入口必须携带
        # generate_hypothesis 产出的 rule_validation 键（值可为 None——
        # 未接校验器时），缺键说明绕过了假设生成链路，fail-loud。
        if "rule_validation" not in hypothesis_result:
            raise TypeError(
                "record_counterexample: hypothesis_result must come from "
                "generate_hypothesis (missing rule_validation provenance)")

        if not hypothesis_result.get("is_counterexample", False):
            return {"recorded": False, "count": axiom["counterexample_count"],
                    "label_variants": len(self._label_variants.get(axiom_id, set())),
                    "status": axiom["status"], "status_changed": False,
                    "new_status": None}

        # 降级保护：标签组合多样性
        if label_combo is not None:
            # 组合归一：小写 + 排序（S29.17 P2：与 W1 断言/闸门口径一致，
            # 大小写漂移不再虚增多样性）
            combo = (tuple(sorted(str(x).lower() for x in label_combo))
                     if isinstance(label_combo, list)
                     else (str(label_combo).lower(),))
            # S29.17 P2：空标签组合 () 不构成有效标签变体——无标签实体
            # 与有标签实体的反例不应凑满"2 种组合"稀释保护语义。
            if combo and combo != ("",):
                self._label_variants.setdefault(axiom_id, set()).add(combo)

        label_count = len(self._label_variants.get(axiom_id, set()))
        if label_count < 2:
            return {
                "recorded": False,
                "count": axiom["counterexample_count"],
                "label_variants": label_count,
                "status": axiom["status"],
                "status_changed": False,
                "new_status": None,
                "reason": "label diversity insufficient (降级保护)",
            }

        # 记录反例
        self._counterexamples.setdefault(axiom_id, []).append({
            "hypothesis_id": hypothesis_result.get("hypothesis_id"),
            "timestamp": time.time(),
            "rule_validation": hypothesis_result.get("rule_validation"),
        })
        axiom["counterexample_count"] = len(self._counterexamples[axiom_id])

        # 降级判定
        # S29.17 F9 修复：轮内第 6+ 个反例到来时，正式态尚未刷新
        # （pending 已 deprecated），读正式态会再次 status_changed →
        # 下游重复触发 downgrade 动作/重复 reset/重嵌入。改读生效态。
        if self._round_active:
            old_status = self.get_effective_status(axiom_id)
        else:
            old_status = axiom["status"]
        new_status = old_status
        count = axiom["counterexample_count"]

        if count >= self.downgrade_threshold:
            new_status = self.STATUS_DEPRECATED
        elif count >= self.warn_threshold:
            new_status = self.STATUS_WARNED

        status_changed = new_status != old_status

        if status_changed:
            if self._round_active:
                self._pending_status[axiom_id] = new_status
            else:
                axiom["status"] = new_status
                axiom["updated_at"] = time.time()

        return {
            "recorded": True,
            "count": count,
            "label_variants": label_count,
            "status": axiom["status"],
            "status_changed": status_changed,
            "new_status": new_status if status_changed else None,
        }

    # ── 轮内快照冻结（锁定决策 #13）──────────────────────

    def begin_round(self, round_id=None):
        """开始一轮——冻结公理集合快照。

        轮内公理集合冻结（对应 M 层相对稳定性）。
        降级判定在轮内正常计算，状态变更写入 pending。
        """
        # S29.17 F10：round_id=0 为合法轮号，不能被 falsy 吞掉
        self._round_id = round_id if round_id is not None else (self._round_id + 1)
        self._round_active = True
        self._round_snapshot = {aid: dict(a) for aid, a in self._axioms.items()}
        self._pending_status = {}
        self._pending_boundary = {}
        return self._round_id

    def end_round(self):
        """结束一轮——将 pending 变更应用到正式状态。

        状态变更仅在轮间生效（锁定决策 #13）。
        """
        # 应用 pending 状态变更
        for aid, new_status in self._pending_status.items():
            if aid in self._axioms:
                self._axioms[aid]["status"] = new_status
                self._axioms[aid]["updated_at"] = time.time()

        # 应用 pending 边界条件变更
        for aid, new_bc in self._pending_boundary.items():
            if aid in self._axioms:
                self._axioms[aid]["boundary_conditions"] = new_bc
                self._axioms[aid]["updated_at"] = time.time()

        self._round_active = False
        self._round_snapshot = None
        self._pending_status = {}
        self._pending_boundary = {}

    def get_round_snapshot(self):
        """获取当前轮冻结的公理快照（轮内只读）。"""
        if not self._round_snapshot:
            return None
        return {aid: dict(a) for aid, a in self._round_snapshot.items()}

    # ── 模型更新计数 ──────────────────────────────────────

    def increment_model_update(self):
        """记录一次模型更新（影响 C_H,adapt）。"""
        self._model_update_count += 1

    # ── W2 数据导出（供 log_recorder 归档）─────────────────

    def export_w2_data(self):
        """导出 W2 层数据，供 log_recorder 归档与 C_H 计算。

        数据结构与 log_recorder._compute_c_h 完全对齐：
        - axioms: list of {id, status, boundary_conditions, ...}
        - cumulative_counterexamples: int
        - downgrade_threshold: int
        - model_update_count: int
        """
        return {
            "axioms": self.get_all_axioms(),
            "cumulative_counterexamples": sum(
                len(ces) for ces in self._counterexamples.values()
            ),
            "downgrade_threshold": self.downgrade_threshold,
            "model_update_count": self._model_update_count,
            "round_id": self._round_id,
        }

    # ── 状态重置 ──────────────────────────────────────────

    def reset(self):
        """清空所有公理与历史（新实验时调用）。"""
        self._axioms = {}
        self._counterexamples = {}
        self._label_variants = {}
        self._round_active = False
        self._round_snapshot = None
        self._pending_status = {}
        self._pending_boundary = {}
        self._model_update_count = 0
        self._round_id = 0
