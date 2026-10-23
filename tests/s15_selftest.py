"""S15 全模块单测——关闭 LLM，模拟数据跑通全部核心逻辑（离线闭环集成）。

验收（project_rules.md S15）：反套壳自查清单第 1、2、5 条通过。
  1. 关闭大模型进程，系统可完成「模拟反例→触发降级→更新奇点向量」完整逻辑。
  2. 所有核心判定有结构化数值依据，无"由大模型判断"的黑盒环节。
  5. QS收敛计算为独立向量运算，不修改模型权重，不由模型生成分数。

闭环链路（全部硬编码模块，零 LLM / 零模型加载）：
  公理注入（world_rules.json 定义）
  → 模拟观测 → W1 rule_validator 真值判定（反例校验）
  → W2 axiom_engine.generate_hypothesis（结构化假设）
  → downgrade_manager.process_counterexample（3预警/5降级 + 多标签保护）
  → 降级触发 → 边界自动补全 + QS 历史重置（奇点向量更新前置）
  → 注入新目标向量 g → 对齐分序列 → check_monotonicity 二次收敛
  → log_recorder 归档 + C_H 三项代理度量核验

用法：py tests\\s15_selftest.py   （纯离线，无端口依赖，无游戏/模型依赖）
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 闭环只允许依赖硬编码模块——禁止导入 llm_assistant / model_adapter
from w1.rule_validator import RuleValidator
from w2.axiom_engine import AxiomEngine
from w2.qs_kernel import QSKernel
from w2.downgrade_manager import DowngradeManager
from w3.log_recorder import LogRecorder

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


def make_m1_counterexample(labels, eid="EID:100"):
    """构造 A_M1 有效反例：攻击后 HP 未下降（违反 R_M1_2）。

    label_history 携带 >=2 种标签组合，满足 rule_validator 侧多样性闸门。
    """
    return {
        "entity": {"entity_id": eid, "kind": "npc", "labels": list(labels), "hp": 80},
        "events": [{
            "kind": "combat_attack",
            "source_entity": "EID:1",
            "target_entity": eid,
            "game_ts": 1.0,
        }],
        "context": {"hp_before": 80, "hp_after": 80, "distance_to_player": 10.0},
        "label_history": [["hostile"], ["gang"]],
    }


def feed_counterexample(engine, dm, validator, axiom_id, labels, qs=None):
    """走完整链路：观测 → W1 校验 → 假设生成 → 降级处理。返回 dm 结果。"""
    obs = make_m1_counterexample(labels)
    h = engine.generate_hypothesis(axiom_id, obs, rule_validator=validator)
    if not h.get("is_counterexample"):
        return {"is_counterexample": False, "hypothesis": h}
    r = dm.process_counterexample(axiom_id, h, observation=obs, qs_kernel=qs)
    r["is_counterexample"] = True
    return r


def drive_two_rounds_to_downgrade(engine, dm, validator, axiom_id, qs=None):
    """跨 2 轮喂 6 次反例，把公理从 active 推到 deprecated。

    轮1：4 次调用（第 1 次被多标签保护拒绝，第 4 次计数=3 触发预警）
    轮2：2 次调用（计数=5 触发降级）
    返回动作序列。
    """
    def _action_of(r):
        if not r.get("is_counterexample"):
            return "rejected"
        if r.get("recorded") is False:
            return "rejected"  # 多标签保护拒绝（锁定决策 #4）：不计入计数
        return r.get("action")

    actions = []
    engine.begin_round()
    for labels in (["hostile"], ["gang"], ["hostile"], ["gang"]):
        r = feed_counterexample(engine, dm, validator, axiom_id, labels, qs)
        actions.append(_action_of(r))
    engine.end_round()

    engine.begin_round()
    for labels in (["hostile"], ["gang"]):
        r = feed_counterexample(engine, dm, validator, axiom_id, labels, qs)
        actions.append(_action_of(r))
    engine.end_round()
    return actions


# ══════════════════════════════════════════════════════════
# 部分 1：自查清单第 1 条——模拟反例 → 触发降级 → 更新奇点向量
# ══════════════════════════════════════════════════════════

def test_closed_loop_downgrade_chain():
    print("\n[1] 闭环主链：模拟反例 → 预警 → 降级 → 边界补全 → QS 重置")
    validator = RuleValidator()
    engine = AxiomEngine()
    qs = QSKernel()  # 默认 window=5 eps=0.01 min_rounds=5 tau=0.6（与配置同源）
    dm = DowngradeManager(engine, qs_kernel=qs)

    # 公理注入：从 W1 规则库定义注入（真实 world_rules.json）
    defs = validator.get_axioms()
    check("W1 规则库提供 3 条实验公理", len(defs) == 3, f"got {len(defs)}")
    for d in defs:
        engine.add_axiom(d["axiom_id"], d["hypothesis"], d["anchor"])
    check("公理注入后计数=3", engine.get_axiom_count() == 3)

    # 初始目标向量 g（模拟嵌入注入，纯数值）
    g0 = qs.compute_target_g([1.0, 0.0, 0.0])
    check("初始 g 已设置且归一化",
          g0 is not None and abs(sum(x * x for x in g0) - 1.0) < 1e-9)

    # 降级前喂一个对齐分，验证降级时历史被重置
    qs.compute_alignment_score([1.0, 0.0, 0.0])
    check("降级前 QS 历史非空", len(qs.get_history()) == 1)

    actions = drive_two_rounds_to_downgrade(engine, dm, validator, "A_M1", qs)
    check("动作序列 = [rejected,none,none,warn, none,downgrade]",
          actions == ["rejected", "none", "none", "warn", "none", "downgrade"],
          f"got {actions}")

    axiom = engine.get_axiom("A_M1")
    check("A_M1 最终状态 = deprecated", axiom["status"] == "deprecated")
    check("A_M1 反例计数 = 5", axiom["counterexample_count"] == 5)
    check("边界条件已自动补全（单调细化）",
          len(axiom["boundary_conditions"]) > 0,
          f"bc={axiom['boundary_conditions']}")
    auto_bc = [b for b in axiom["boundary_conditions"]
               if isinstance(b, dict) and b.get("source") == "auto_complete"]
    check("自动补全边界带来源标记（因果可追溯）", len(auto_bc) > 0)

    check("降级事件已记录（结构化）",
          len(dm.get_downgrade_events("A_M1")) == 1)
    ev = dm.get_downgrade_events("A_M1")[0]
    check("降级事件含数值依据 count=5/threshold=5",
          ev["count"] == 5 and ev["threshold"] == 5)

    check("降级触发 QS 历史重置（奇点向量更新前置）",
          len(qs.get_history()) == 0)
    check("其余公理仍活跃（2 条）",
          len(engine.get_active_axioms()) == 2)


# ══════════════════════════════════════════════════════════
# 部分 2：自查清单第 5 条——QS 收敛为独立向量运算
# ══════════════════════════════════════════════════════════

def test_qs_secondary_convergence_pure_math():
    print("\n[2] QS 二次收敛：降级后注入新 g，纯向量运算判定收敛")
    validator = RuleValidator()
    engine = AxiomEngine()
    qs = QSKernel()
    dm = DowngradeManager(engine, qs_kernel=qs)
    for d in validator.get_axioms():
        engine.add_axiom(d["axiom_id"], d["hypothesis"], d["anchor"])
    qs.compute_target_g([1.0, 0.0, 0.0])
    drive_two_rounds_to_downgrade(engine, dm, validator, "A_M1", qs)

    # 降级后注入新目标向量 g'（公理集变化后的新奇点）
    g_new = qs.compute_target_g([0.0, 1.0, 0.0])
    check("新 g 注入成功", g_new is not None)

    # 手算余弦对照（独立向量运算，不由模型生成分数）
    s = qs.compute_alignment_score([0.0, 2.0, 0.0])  # 归一化后 = g' → cos=1.0
    check("对齐分 = 手算余弦 1.0", abs(s - 1.0) < 1e-9, f"got {s}")
    s_ortho = QSKernel()
    s_ortho.set_target_g([1.0, 0.0, 0.0])
    score_ortho = s_ortho.compute_alignment_score([0.0, 1.0, 0.0])
    check("正交向量对齐分 = 0.0（手算一致）", abs(score_ortho) < 1e-9)

    # 连续 5 轮完美对齐 → 收敛
    for _ in range(4):
        qs.compute_alignment_score([0.0, 1.0, 0.0])
    conv = qs.check_monotonicity()
    check("二次收敛判定 converged=True", conv["converged"] is True,
          f"reason={conv.get('reason')}")
    check("收敛依据为数值：window_mean=1.0 >= tau=0.6",
          abs(conv["window_mean"] - 1.0) < 1e-9 and conv["above_threshold"])
    check("收敛判定含单调性数值核验", conv["monotonic"] is True)

    # 漂移可检出（对齐分骤降）
    qs.compute_alignment_score([0.0, 0.0, 1.0])  # 与 g' 正交 → 0.0
    drift = qs.detect_drift()
    check("漂移检测可捕获骤降", drift["drifted"] is True, f"{drift}")


# ══════════════════════════════════════════════════════════
# 部分 3：自查清单第 2 条——核心判定全部结构化数值依据
# ══════════════════════════════════════════════════════════

def test_all_decisions_have_numeric_grounds():
    print("\n[3] 结构化数值依据：降级/收敛/成本判定无黑盒")
    validator = RuleValidator()
    engine = AxiomEngine()
    qs = QSKernel()
    dm = DowngradeManager(engine, qs_kernel=qs)
    for d in validator.get_axioms():
        engine.add_axiom(d["axiom_id"], d["hypothesis"], d["anchor"])
    qs.compute_target_g([1.0, 0.0, 0.0])

    engine.begin_round()
    # 单次反例判定结果必须携带数值字段
    r2 = feed_counterexample(engine, dm, validator, "A_M1", ["hostile"], qs)
    r2b = feed_counterexample(engine, dm, validator, "A_M1", ["gang"], qs)
    engine.end_round()
    check("反例判定返回结构化 count/label_variants 数值",
          isinstance(r2b.get("count"), int) and isinstance(r2b.get("label_variants"), int))
    check("多标签保护拒绝时给出原因字符串",
          r2.get("recorded") is False and "reason" in r2)

    # W1 真值判定结构化：违反规则列表带 rule_id + reason
    obs = make_m1_counterexample(["hostile"])
    vr = validator.is_valid_counterexample("A_M1", obs)
    check("W1 反例校验返回 violated_rules 结构化列表",
          vr["is_valid"] is True and len(vr["violated_rules"]) > 0
          and all("rule_id" in v and "reason" in v for v in vr["violated_rules"]))

    # C_H 代理度量全数值
    with tempfile.TemporaryDirectory() as td:
        rec = LogRecorder(os.path.join(td, "exp.jsonl"))
        w2 = engine.export_w2_data()
        entry = rec.record_round(
            round_id=1,
            w0_data={"stats": {"raw_entities": 10, "raw_events": 5}},
            w1_data={"rules_checked": 4},
            w2_data=w2,
            w3_data={"actions": []},
        )
        c_h = entry["c_h"]
        check("C_H 三项全为数值",
              isinstance(c_h["struct"], int) and isinstance(c_h["adapt"], int)
              and isinstance(c_h["extend"], int))
        check("C_H,extend = min(累计反例, 阈值) 数值一致",
              c_h["extend"] == min(w2["cumulative_counterexamples"],
                                   w2["downgrade_threshold"]))
        check("C_H,adapt = 采样数(15) + 模型更新数(0)", c_h["adapt"] == 15)
        check("预算判定 within_budget 为布尔 + 分项过载标记齐全",
              isinstance(c_h["within_budget"], bool)
              and set(c_h["overloads"].keys()) ==
              {"struct_overload", "adapt_overload", "extend_overload"})
        # 归档可复现（因果链可追溯）
        loaded = rec.load_round(1)
        check("归档轮次可一键复现", loaded is not None
              and loaded["w2"]["cumulative_counterexamples"]
              == w2["cumulative_counterexamples"])


# ══════════════════════════════════════════════════════════
# 部分 4：Γ脆 判据①（语义死亡）离线可检出
# ══════════════════════════════════════════════════════════

def test_semantic_death_detection():
    print("\n[4] Γ脆 判据①：全部公理 deprecated → 语义死亡（离线检出）")
    validator = RuleValidator()
    engine = AxiomEngine()
    qs = QSKernel()
    dm = DowngradeManager(engine, qs_kernel=qs)
    # 只注入 1 条公理，降级后即"全部 deprecated"
    d = validator.get_axioms()[0]
    engine.add_axiom(d["axiom_id"], d["hypothesis"], d["anchor"])
    qs.compute_target_g([1.0, 0.0, 0.0])

    actions = drive_two_rounds_to_downgrade(engine, dm, validator, d["axiom_id"], qs)
    check("单公理降级动作链正确", actions[-1] == "downgrade", f"{actions}")

    all_axioms = engine.get_all_axioms()
    semantic_death = (
        len(all_axioms) > 0
        and all(a["status"] == "deprecated" for a in all_axioms)
    )
    check("全部公理 deprecated → 语义死亡判据①成立", semantic_death is True)
    check("语义死亡后活跃公理数=0（g 失去载体）",
          len(engine.get_active_axioms()) == 0)


# ══════════════════════════════════════════════════════════
# 部分 5：反套壳结构核验——闭环零 LLM / 零模型依赖
# ══════════════════════════════════════════════════════════

def test_no_llm_no_model_in_loop():
    print("\n[5] 反套壳结构核验：闭环未导入 LLM 辅助 / 模型适配模块")
    check("闭环未导入 w2.llm_assistant",
          "w2.llm_assistant" not in sys.modules)
    check("闭环未导入 w2.model_adapter",
          "w2.model_adapter" not in sys.modules)
    check("闭环未导入 torch（无模型进程）", "torch" not in sys.modules)
    check("闭环未导入 transformers", "transformers" not in sys.modules)


if __name__ == "__main__":
    test_closed_loop_downgrade_chain()
    test_qs_secondary_convergence_pure_math()
    test_all_decisions_have_numeric_grounds()
    test_semantic_death_detection()
    test_no_llm_no_model_in_loop()

    print("=" * 50)
    print(f"S15 全模块离线闭环自测: {PASS} passed, {FAIL} failed")
    print("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)
