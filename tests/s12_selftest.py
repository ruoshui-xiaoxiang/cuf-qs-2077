"""S12 ICC 审计脱机自测。

验收标准：可检出互斥与重复公理。
覆盖：互斥标签检测 / 重复覆盖 / 完整性 / 锚点映射 / 状态一致性 /
      Γ脆解体预警 / 审计日志。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from w2.icc_auditor import ICCAuditor

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
    auditor = ICCAuditor()

    # ── 1. 正常公理集——PASS ──
    print("\n=== 1. 正常公理集 ===")
    axioms_ok = [
        {"id": "A_M1", "hypothesis": "攻击→HP下降", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": [{"kind": "npc", "labels": ["hostile"]}]},
        {"id": "A_W2_5", "hypothesis": "HP归零→死亡", "anchor": "P_W2_5",
         "status": "active",
         "boundary_conditions": [{"kind": "npc", "labels": ["civilian"]}]},
        {"id": "A_W1", "hypothesis": "采样限制", "anchor": "P_W1",
         "status": "active",
         "boundary_conditions": [{"kind": "npc"}]},
    ]
    r = auditor.audit(axioms_ok)
    check("normal PASS", r["passed"] is True, r["summary"])
    check("normal 0 issues", r["total_issues"] == 0, str(r["issues"]))

    # ── 2. 互斥标签检测 ──
    print("\n=== 2. 互斥标签检测 ===")
    axioms_mutex = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": [
             {"kind": "npc", "labels": ["hostile"]},
             {"kind": "npc", "labels": ["civilian"]},
         ]},
    ]
    r2 = auditor.audit(axioms_mutex)
    check("mutex detected", r2["passed"] is False)
    mutex_issues = [i for i in r2["issues"] if i["type"] == "mutex_violation"]
    check("mutex issue count >= 1", len(mutex_issues) >= 1)
    check("mutex severity error", mutex_issues[0]["severity"] == "error")
    check("mutex has evidence", "mutex_pair" in mutex_issues[0]["evidence"])

    # ── 3. 重复覆盖检测 ──
    print("\n=== 3. 重复覆盖检测 ===")
    axioms_dup = [
        {"id": "A_M1", "hypothesis": "test1", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": [{"kind": "npc", "labels": ["hostile"]}]},
        {"id": "A_M2", "hypothesis": "test2", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": [{"kind": "npc", "labels": ["hostile"]}]},
    ]
    r3 = auditor.audit(axioms_dup)
    check("duplicate detected", r3["passed"] is False)
    dup_issues = [i for i in r3["issues"] if i["type"] == "duplicate_coverage"]
    check("duplicate issue count >= 1", len(dup_issues) >= 1)
    check("duplicate severity warning", dup_issues[0]["severity"] == "warning")

    # ── 4. 完整性检查：缺少 hypothesis ──
    print("\n=== 4. 完整性：缺少 hypothesis ===")
    axioms_no_hyp = [
        {"id": "A_M1", "hypothesis": "", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": [{"kind": "npc"}]},
    ]
    r4 = auditor.audit(axioms_no_hyp)
    hyp_issues = [i for i in r4["issues"] if i["type"] == "missing_hypothesis"]
    check("missing hypothesis detected", len(hyp_issues) >= 1)
    check("hypothesis severity error", hyp_issues[0]["severity"] == "error")

    # ── 5. 完整性检查：缺少边界条件 ──
    print("\n=== 5. 完整性：缺少边界条件 ===")
    axioms_no_bc = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": []},
    ]
    r5 = auditor.audit(axioms_no_bc)
    bc_issues = [i for i in r5["issues"] if i["type"] == "missing_boundary"]
    check("missing boundary detected", len(bc_issues) >= 1)
    check("boundary severity warning", bc_issues[0]["severity"] == "warning")

    # ── 6. 锚点映射检查 ──
    print("\n=== 6. 锚点映射检查 ===")
    axioms_bad_anchor = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "P_UNKNOWN",
         "status": "active",
         "boundary_conditions": [{"kind": "npc"}]},
    ]
    r6 = auditor.audit(axioms_bad_anchor)
    anchor_issues = [i for i in r6["issues"] if i["type"] == "invalid_anchor"]
    check("invalid anchor detected", len(anchor_issues) >= 1)
    check("anchor severity error", anchor_issues[0]["severity"] == "error")

    # 缺少锚点
    axioms_no_anchor = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "",
         "status": "active",
         "boundary_conditions": [{"kind": "npc"}]},
    ]
    r6b = auditor.audit(axioms_no_anchor)
    na_issues = [i for i in r6b["issues"] if i["type"] == "missing_anchor"]
    check("missing anchor detected", len(na_issues) >= 1)

    # ── 7. 状态一致性 ──
    print("\n=== 7. 状态一致性 ===")
    axioms_bad_status = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "P_M1",
         "status": "frozen",
         "boundary_conditions": [{"kind": "npc"}]},
    ]
    r7 = auditor.audit(axioms_bad_status)
    status_issues = [i for i in r7["issues"] if i["type"] == "invalid_status"]
    check("invalid status detected", len(status_issues) >= 1)
    check("status severity error", status_issues[0]["severity"] == "error")

    # ── 8. Γ脆解体预警（锁定决策 #12）──
    print("\n=== 8. Γ脆解体预警 ===")
    axioms_all_dep = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "P_M1",
         "status": "deprecated",
         "boundary_conditions": [{"kind": "npc"}]},
        {"id": "A_W2_5", "hypothesis": "test2", "anchor": "P_W2_5",
         "status": "deprecated",
         "boundary_conditions": [{"kind": "npc"}]},
    ]
    r8 = auditor.audit(axioms_all_dep)
    dep_issues = [i for i in r8["issues"] if i["type"] == "all_deprecated"]
    check("all deprecated detected", len(dep_issues) >= 1)
    check("severity critical", dep_issues[0]["severity"] == "critical")

    # ── 9. deprecated 公理跳过完整性检查 ──
    print("\n=== 9. deprecated 跳过完整性 ===")
    axioms_dep_ok = [
        {"id": "A_M1", "hypothesis": "", "anchor": "P_M1",
         "status": "deprecated",
         "boundary_conditions": []},
        {"id": "A_W2_5", "hypothesis": "ok", "anchor": "P_W2_5",
         "status": "active",
         "boundary_conditions": [{"kind": "npc"}]},
    ]
    r9 = auditor.audit(axioms_dep_ok)
    # deprecated 的 A_M1 不应产生 missing_hypothesis / missing_boundary
    hyp_9 = [i for i in r9["issues"]
             if i["type"] == "missing_hypothesis" and i["axiom_id"] == "A_M1"]
    bc_9 = [i for i in r9["issues"]
            if i["type"] == "missing_boundary" and i["axiom_id"] == "A_M1"]
    check("deprecated skip hypothesis check", len(hyp_9) == 0)
    check("deprecated skip boundary check", len(bc_9) == 0)

    # ── 10. 审计日志 ──
    print("\n=== 10. 审计日志 ===")
    log = auditor.get_audit_log()
    check("audit log has entries", len(log) >= 9)
    last = auditor.get_last_audit()
    check("last audit is Γ脆", last is not None)

    # ── 11. 输入校验 ──
    print("\n=== 11. 输入校验 ===")
    r_none = auditor.audit(None)
    check("None input fails", r_none["passed"] is False)
    r_str = auditor.audit("not a list")
    check("string input fails", r_str["passed"] is False)

    # ── 12. 多种互斥标签对 ──
    print("\n=== 12. 多种互斥标签对 ===")
    axioms_multi_mutex = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": [
             {"kind": "npc", "labels": ["police"]},
             {"kind": "npc", "labels": ["gang"]},
         ]},
    ]
    r12 = auditor.audit(axioms_multi_mutex)
    mutex12 = [i for i in r12["issues"] if i["type"] == "mutex_violation"]
    check("police/gang mutex detected", len(mutex12) >= 1)

    # ── 12b. 单条边界条件内部互斥（审计修复）──
    print("\n=== 12b. 单条边界条件内部互斥（审计修复） ===")
    axioms_single_mutex = [
        {"id": "A_M1", "hypothesis": "test", "anchor": "P_M1",
         "status": "active",
         "boundary_conditions": [
             {"kind": "npc", "labels": ["hostile", "civilian"]},
         ]},
    ]
    r12b = auditor.audit(axioms_single_mutex)
    mutex12b = [i for i in r12b["issues"] if i["type"] == "mutex_violation"]
    check("single-bc internal mutex detected", len(mutex12b) >= 1)
    if mutex12b:
        check("single-bc mutex severity error", mutex12b[0]["severity"] == "error")
        check("single-bc evidence has label_set",
              "label_set" in mutex12b[0]["evidence"], str(mutex12b[0]["evidence"]))
        check("single-bc mutex not double-counted cross-bc", len(mutex12b) == 1,
              f"expected exactly 1 issue, got {len(mutex12b)}")

    # ── 13. 空公理集 ──
    print("\n=== 13. 空公理集 ===")
    r_empty = auditor.audit([])
    check("empty axioms PASS", r_empty["passed"] is True)
    check("empty axioms 0 issues", r_empty["total_issues"] == 0)

    # ── 总结 ──
    print(f"\n{'='*50}")
    print(f"S12 ICC 审计自测: {PASS} passed, {FAIL} failed")
    print(f"{'='*50}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
