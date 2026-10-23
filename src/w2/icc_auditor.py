"""W2 ICC 审计——公理一致性与完整性审计。

对应 CUF v7 W₂ 稳态自指体的自治审计面。
反套壳铁则 #1：ICC 审计全部由硬编码规则判定，LLM 无裁决权。
反套壳铁则 #4：每次审计判定必须对应明确的判据记录，禁止黑盒决策。

审计内容：
1. 标签级互斥检测：同一公理的 boundary_conditions 中不应存在互斥标签组合
2. 重复检测：不同公理不应覆盖完全相同的标签空间
3. 完整性检查：活跃公理必须有 hypothesis 和至少一条 boundary_condition
4. 锚点映射检查：每条公理必须有对应的可证伪锚点
"""

import json
import os


class ICCAuditor:
    """ICC 审计器——硬编码判据，无 LLM 参与。

    判据全部写入代码（反套壳铁则 #4），每次审计输出结构化报告。
    """

    # 互斥标签对（硬编码，基于游戏世界规则）
    MUTEX_LABEL_PAIRS = [
        (frozenset(["hostile"]), frozenset(["civilian"])),
        (frozenset(["hostile"]), frozenset(["police"])),
        (frozenset(["police"]), frozenset(["gang"])),
        (frozenset(["player"]), frozenset(["hostile"])),
        (frozenset(["player"]), frozenset(["civilian"])),
    ]

    def __init__(self, extra_valid_anchors=None):
        self._audit_log = []
        # 修复 1（2026-08-25）：候选池锚点（P_GANG_HOSTILE/P_ITEM/P_STATUS 等）
        # 与基础锚点 VALID_ANCHORS 属两套体系，原白名单只含 4 个基础锚点 →
        # 200 候选激活后 86 条 invalid_anchor。现允许外部注入候选池锚点，
        # audit 时并入合法锚点集合。
        self._extra_anchors = set(extra_valid_anchors or [])

    # ── 审计入口 ──────────────────────────────────────────

    def audit(self, axioms):
        """执行完整 ICC 审计。

        Args:
            axioms: 公理列表（来自 axiom_engine.get_all_axioms()）

        Returns:
            {
                "passed": bool,
                "total_issues": int,
                "issues": [{"type", "severity", "axiom_id", "description", "evidence"}],
                "summary": str
            }
        """
        if not isinstance(axioms, list):
            return self._fail("axioms must be a list")

        issues = []

        # 1. 互斥检测
        issues.extend(self._check_mutex_labels(axioms))

        # 2. 重复检测
        issues.extend(self._check_duplicates(axioms))

        # 3. 完整性检查
        issues.extend(self._check_completeness(axioms))

        # 4. 锚点映射检查
        issues.extend(self._check_anchor_mapping(axioms))

        # 5. 状态一致性
        issues.extend(self._check_status_consistency(axioms))

        passed = len(issues) == 0
        report = {
            "passed": passed,
            "total_issues": len(issues),
            "issues": issues,
            "summary": (f"ICC audit: {len(axioms)} axioms, "
                        f"{len(issues)} issues "
                        f"({'PASS' if passed else 'FAIL'})"),
        }
        self._audit_log.append(report)
        return report

    # ── 1. 互斥标签检测 ────────────────────────────────────

    def _check_mutex_labels(self, axioms):
        """检测同一公理的边界条件中是否存在互斥标签组合。

        判据：MUTEX_LABEL_PAIRS 中定义的标签对不应同时出现在
        同一公理的 boundary_conditions 中。
        """
        issues = []
        for axiom in axioms:
            aid = axiom.get("id", "?")
            bcs = axiom.get("boundary_conditions", [])
            label_sets = []
            for bc in bcs:
                if isinstance(bc, dict):
                    labels = bc.get("labels", [])
                    if labels:
                        label_sets.append(frozenset(labels))
                elif isinstance(bc, str):
                    label_sets.append(frozenset([bc]))

            # 审计修复：单条边界条件自身同时携带互斥标签同样违规
            # （原实现只做跨条件两两比较，漏检单条内部互斥）。
            for ls in label_sets:
                for pair in self.MUTEX_LABEL_PAIRS:
                    if pair[0] <= ls and pair[1] <= ls:
                        issues.append({
                            "type": "mutex_violation",
                            "severity": "error",
                            "axiom_id": aid,
                            "description": (
                                f"单条边界条件内部互斥：{list(pair[0])} 与 "
                                f"{list(pair[1])} 同时出现于同一标签组合"),
                            "evidence": {
                                "label_set": sorted(ls),
                                "mutex_pair": [sorted(pair[0]), sorted(pair[1])],
                            },
                        })

            for i, ls1 in enumerate(label_sets):
                for ls2 in label_sets[i + 1:]:
                    for pair in self.MUTEX_LABEL_PAIRS:
                        if (pair[0] <= ls1 and pair[1] <= ls2 or
                            pair[0] <= ls2 and pair[1] <= ls1):
                            issues.append({
                                "type": "mutex_violation",
                                "severity": "error",
                                "axiom_id": aid,
                                "description": (
                                    f"互斥标签组合：{list(pair[0])} 与 "
                                    f"{list(pair[1])} 同时出现在边界条件中"),
                                "evidence": {
                                    "label_set_1": list(ls1),
                                    "label_set_2": list(ls2),
                                    "mutex_pair": [list(pair[0]), list(pair[1])],
                                },
                            })
        return issues

    # ── 2. 重复检测 ────────────────────────────────────────

    def _check_duplicates(self, axioms):
        """检测不同公理是否覆盖完全相同的标签空间。

        判据：两条不同公理的 boundary_conditions 不应完全相同
        （相同标签组合覆盖 = 冗余公理）。
        """
        issues = []
        for i, a1 in enumerate(axioms):
            for a2 in axioms[i + 1:]:
                bc1 = self._normalize_bc(a1.get("boundary_conditions", []))
                bc2 = self._normalize_bc(a2.get("boundary_conditions", []))
                if bc1 and bc2 and bc1 == bc2:
                    issues.append({
                        "type": "duplicate_coverage",
                        "severity": "warning",
                        "axiom_id": f"{a1.get('id', '?')} & {a2.get('id', '?')}",
                        "description": "两条公理的边界条件完全相同",
                        "evidence": {
                            "axiom_1": a1.get("id"),
                            "axiom_2": a2.get("id"),
                            "boundary_conditions": list(bc1),
                        },
                    })
        return issues

    @staticmethod
    def _normalize_bc(bcs):
        """将 boundary_conditions 归一化为可比较的 frozenset。"""
        items = []
        for bc in bcs:
            if isinstance(bc, dict):
                key = (bc.get("kind", ""), tuple(sorted(bc.get("labels", []))))
                items.append(key)
            elif isinstance(bc, str):
                items.append((bc, ()))
        return frozenset(items)

    # ── 3. 完整性检查 ──────────────────────────────────────

    def _check_completeness(self, axioms):
        """检查活跃公理的完整性。

        判据：
        - 活跃公理必须有 hypothesis（非空）
        - 活跃公理必须有至少一条 boundary_condition
        - deprecated 公理不检查完整性
        """
        issues = []
        for axiom in axioms:
            aid = axiom.get("id", "?")
            status = axiom.get("status", "active")
            if status == "deprecated":
                continue

            hypothesis = axiom.get("hypothesis", "")
            if not hypothesis or not isinstance(hypothesis, str):
                issues.append({
                    "type": "missing_hypothesis",
                    "severity": "error",
                    "axiom_id": aid,
                    "description": "活跃公理缺少 hypothesis",
                    "evidence": {"hypothesis": hypothesis},
                })

            bcs = axiom.get("boundary_conditions", [])
            if not bcs:
                issues.append({
                    "type": "missing_boundary",
                    "severity": "warning",
                    "axiom_id": aid,
                    "description": "活跃公理无边界条件",
                    "evidence": {},
                })
        return issues

    # ── 4. 锚点映射检查 ────────────────────────────────────

    VALID_ANCHORS = {"P_M1", "P_W2_5", "P_W1", "P_C"}

    def _check_anchor_mapping(self, axioms):
        """检查每条公理是否有有效的可证伪锚点。

        判据：anchor 必须在合法锚点集合（基础 VALID_ANCHORS + 候选池锚点）中，
        且非空。
        """
        issues = []
        valid = self.VALID_ANCHORS | self._extra_anchors
        for axiom in axioms:
            aid = axiom.get("id", "?")
            anchor = axiom.get("anchor", "")
            if not anchor:
                issues.append({
                    "type": "missing_anchor",
                    "severity": "error",
                    "axiom_id": aid,
                    "description": "公理缺少可证伪锚点",
                    "evidence": {},
                })
            elif anchor not in valid:
                issues.append({
                    "type": "invalid_anchor",
                    "severity": "error",
                    "axiom_id": aid,
                    "description": f"未知锚点：{anchor}",
                    "evidence": {"anchor": anchor,
                                 "valid": sorted(valid)},
                })
        return issues

    # ── 5. 状态一致性 ──────────────────────────────────────

    def _check_status_consistency(self, axioms):
        """检查公理状态的一致性。

        判据：
        - status 必须是 active/warned/deprecated 之一
        - 全部 deprecated → Γ脆 解体预警（锁定决策 #12）
        """
        issues = []
        valid_statuses = {"active", "warned", "deprecated"}
        all_deprecated = True

        for axiom in axioms:
            aid = axiom.get("id", "?")
            status = axiom.get("status", "active")
            if status not in valid_statuses:
                issues.append({
                    "type": "invalid_status",
                    "severity": "error",
                    "axiom_id": aid,
                    "description": f"无效状态：{status}",
                    "evidence": {"status": status,
                                 "valid": list(valid_statuses)},
                })
            if status != "deprecated":
                all_deprecated = False

        if axioms and all_deprecated:
            issues.append({
                "type": "all_deprecated",
                "severity": "critical",
                "axiom_id": "*",
                "description": "全部公理已降级——Γ脆解体判据触发（锁定决策 #12）",
                "evidence": {"axiom_count": len(axioms)},
            })

        return issues

    # ── 查询 ──────────────────────────────────────────────

    def get_audit_log(self):
        """返回审计历史。"""
        return list(self._audit_log)

    def get_last_audit(self):
        """返回最近一次审计报告。"""
        return self._audit_log[-1] if self._audit_log else None

    @staticmethod
    def _fail(msg):
        return {
            "passed": False,
            "total_issues": 1,
            "issues": [{"type": "input_error", "severity": "error",
                        "axiom_id": "?", "description": msg, "evidence": {}}],
            "summary": f"ICC audit failed: {msg}",
        }
