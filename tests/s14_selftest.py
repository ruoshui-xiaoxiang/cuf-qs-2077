"""S14 LLM 辅助模块自测。

验收（project_rules.md S14 条目）：
- 仅三件事：
  1. hypothesis_nl(hypothesis_struct)：假设自然语言化
  2. summarize_downgrade(downgrade_event)：降级语义总结
  3. check_semantic_contradiction(axioms)：语义矛盾参考排查（仅参考）
- 全部输出带明确"无裁决权"标记（建议/参考、不可直接用于裁决）。
- 全部输出结构化（dict，含 advisory/disclaimer/meta 字段，反套壳铁则 #2）。
- 自测模式（use_model=False，默认）：不加载 7B，纯规则生成，可直接单测。
- from_config(config)：读取 config.llm.use_model 决定实机/自测；缺省为 False（禁静默联网）。
- 裁决权下沉检查（反套壳铁则 #1）：任何返回字段不含"触发/降级/通过/不通过"等裁决字眼；
  若 axiom 对象给出最终裁决标记，辅助模块必须在 disclaimer 明确拒绝并剥离裁决表达。
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


def expect_raises(name, fn, substr=None):
    global PASS, FAIL
    try:
        fn()
        FAIL += 1
        print(f"  FAIL  {name}  (expected exception, none raised)")
    except Exception as e:
        if substr and substr not in str(e):
            FAIL += 1
            print(f"  FAIL  {name}  (exception msg missing {substr!r}: got {e})")
        else:
            PASS += 1
            print(f"  PASS  {name}")


NO_VERDICT_MUST_NOT = [
    "触发降级", "触发预警", "做出裁决", "独立裁决", "直接裁决",
    "最终裁决", "判定为", "判定通过", "判定不通过",
    "确认降级", "执行降级", "否决", "通过公理", "公理有效", "公理无效",
    "公理通过", "公理未通过", "系统判定触发",
]

# 本模块允许的固定合规表达——即使命中上面黑名单的子串，只要属于这些白名单表达即可通过。
_SAFE_PHRASES = [
    "无裁决权", "不具备裁决权", "不得直接用于裁决", "不得直接用于公理状态变更",
    "不得直接用于降级触发", "不得直接用于收敛判定", "不得直接用于审计",
    "仅建议·无裁决权",
]


def no_verdict_words(text):
    """反套壳铁则 #1：辅助模块输出中不得出现裁决类动词短语。

    注意：黑名单条目用"裁决"的组合短语而不用裸"裁决"，
    以避免误伤固定合规表达"无裁决权"。
    """
    t = text or ""
    # 先把所有安全短语从文本里替换掉，再比对黑名单，避免误报
    masked = t
    for safe in _SAFE_PHRASES:
        masked = masked.replace(safe, "[SAFE]")
    bad = [w for w in NO_VERDICT_MUST_NOT if w in masked]
    return bad



def test_hypothesis_nl_structure_and_disclaimer():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)

    h_struct = {
        "axiom_id": "A_PW2_5",
        "anchor": "P_W2_5",
        "predicate": "降级机制比对照组维持稳态更优",
        "predicted_condition": "对照组出现至少 1 次 C_extend 超预算",
        "boundary_conditions": [
            {"scope": "Nippon Plaza", "when": "combat_attack freq > 0.1/s"},
            {"scope": "same_seed", "when": "seed in [1,2,3]"},
        ],
    }

    r = llm.hypothesis_nl(h_struct)

    check("hypothesis_nl returns dict", isinstance(r, dict))
    check("hypothesis_nl has 'text' key", "text" in r and isinstance(r["text"], str) and len(r["text"]) > 0)
    check("hypothesis_nl has 'advisory' True", r.get("advisory") is True)
    check("hypothesis_nl has 'disclaimer' string", isinstance(r.get("disclaimer"), str) and len(r["disclaimer"]) > 0)
    check("hypothesis_nl has 'meta' dict", isinstance(r.get("meta"), dict))
    check("hypothesis_nl meta.axiom_id", r["meta"].get("axiom_id") == "A_PW2_5")
    check("hypothesis_nl meta.anchor", r["meta"].get("anchor") == "P_W2_5")
    check("hypothesis_nl meta.generated_ts", isinstance(r["meta"].get("generated_ts"), (int, float)))
    check("hypothesis_nl meta.mode == offline", r["meta"].get("mode") == "offline")
    check("hypothesis_nl text mentions axiom_id", "A_PW2_5" in r["text"])
    check("hypothesis_nl text mentions anchor", "P_W2_5" in r["text"])
    check("hypothesis_nl text mentions predicate core", "降级机制" in r["text"] and "稳态" in r["text"])
    check("hypothesis_nl disclaimer has 仅建议/无裁决权 substring",
          ("仅建议" in r["disclaimer"]) or ("无裁决权" in r["disclaimer"]))
    bad = no_verdict_words(r["text"]) + no_verdict_words(r["disclaimer"])
    check("hypothesis_nl NO verdict words", len(bad) == 0, f"bad words: {bad}")


def test_hypothesis_nl_rejects_missing_required():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)

    expect_raises(
        "hypothesis_nl missing axiom_id raises",
        lambda: llm.hypothesis_nl({"anchor": "P_W2_5", "predicate": "x"}),
        "axiom_id",
    )
    expect_raises(
        "hypothesis_nl missing anchor raises",
        lambda: llm.hypothesis_nl({"axiom_id": "A1", "predicate": "x"}),
        "anchor",
    )
    expect_raises(
        "hypothesis_nl missing predicate raises",
        lambda: llm.hypothesis_nl({"axiom_id": "A1", "anchor": "P1"}),
        "predicate",
    )
    expect_raises(
        "hypothesis_nl non-dict raises",
        lambda: llm.hypothesis_nl("hello"),
        "dict",
    )


def test_summarize_downgrade_structure_and_no_verdict():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)

    event = {
        "axiom_id": "A_PW2_5",
        "from_status": "warned",
        "to_status": "deprecated",
        "reason": "counterexample_count=5 (>=downgrade_threshold=5)",
        "counterexamples_used": [
            {"labels": "hostile+player", "round_id": 11},
            {"labels": "police+gang", "round_id": 13},
            {"labels": "civilian+hostile", "round_id": 14},
        ],
        "boundary_added": [
            "when label_combo in {police+gang,civilian+hostile}",
        ],
    }

    r = llm.summarize_downgrade(event)

    check("summarize_downgrade returns dict", isinstance(r, dict))
    check("summarize_downgrade has summary string", isinstance(r.get("summary"), str) and len(r["summary"]) > 0)
    check("summarize_downgrade has key_points list", isinstance(r.get("key_points"), list))
    check("summarize_downgrade advisory=True", r.get("advisory") is True)
    check("summarize_downgrade disclaimer has 无裁决权/仅建议",
          ("仅建议" in r.get("disclaimer", "")) or ("无裁决权" in r.get("disclaimer", "")))
    check("summarize_downgrade meta.axiom_id", r["meta"].get("axiom_id") == "A_PW2_5")
    check("summarize_downgrade meta.from/to status",
          r["meta"].get("from_status") == "warned" and r["meta"].get("to_status") == "deprecated")
    check("summarize_downgrade meta.generated_ts", isinstance(r["meta"].get("generated_ts"), (int, float)))
    check("summarize_downgrade summary contains A_PW2_5", "A_PW2_5" in r["summary"])
    check("summarize_downgrade summary contains status transition",
          "warned" in r["summary"] and "deprecated" in r["summary"])
    check("summarize_downgrade key_points non-empty", len(r["key_points"]) > 0)
    bad = no_verdict_words(r["summary"]) + no_verdict_words("".join(r["key_points"])) + no_verdict_words(r.get("disclaimer", ""))
    check("summarize_downgrade NO verdict words", len(bad) == 0, f"bad: {bad}")


def test_summarize_downgrade_rejects_bad_event():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)

    expect_raises("summarize_downgrade non-dict raises",
                  lambda: llm.summarize_downgrade(["oops"]), "dict")
    expect_raises("summarize_downgrade no axiom_id raises",
                  lambda: llm.summarize_downgrade({"reason": "x"}), "axiom_id")
    expect_raises("summarize_downgrade no status fields raises",
                  lambda: llm.summarize_downgrade({"axiom_id": "A1"}), "from_status")


def test_check_semantic_contradiction_reference_only():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)

    axioms = [
        {
            "axiom_id": "A1",
            "hypothesis": "所有警察不攻击平民",
            "boundary_conditions": [{"label_set": ["police", "civilian"]}],
        },
        {
            "axiom_id": "A2",
            "hypothesis": "平民受到攻击时警察会反击",
            "boundary_conditions": [{"label_set": ["civilian", "attacked"]}],
        },
        {
            "axiom_id": "A3",
            "hypothesis": "所有平民持有武器",  # 与世界常识弱冲突
            "boundary_conditions": [{"label_set": ["civilian"]}],
        },
    ]

    r = llm.check_semantic_contradiction(axioms)

    check("semantic_contradiction returns dict", isinstance(r, dict))
    check("semantic_contradiction has suggestions list", isinstance(r.get("suggestions"), list))
    check("semantic_contradiction has found_any bool", isinstance(r.get("found_any"), bool))
    check("semantic_contradiction advisory=True", r.get("advisory") is True)
    check("semantic_contradiction disclaimer present",
          isinstance(r.get("disclaimer"), str) and len(r["disclaimer"]) > 0)
    # 核心：反套壳铁则 #2 参考标记——disclaimer 必须明确指出"以 ICCAuditor 为准"
    check("semantic_contradiction disclaimer points to ICCAuditor",
          "ICCAuditor" in r.get("disclaimer", ""))
    check("semantic_contradiction meta.count_axioms == 3", r["meta"].get("count_axioms") == 3)
    check("semantic_contradiction meta.mode == offline", r["meta"].get("mode") == "offline")

    # 建议项结构
    if r["suggestions"]:
        s0 = r["suggestions"][0]
        check("suggestion item has severity", isinstance(s0.get("severity"), str) and s0["severity"] in ("high", "medium", "low"))
        check("suggestion item has axiom_ids (list)", isinstance(s0.get("axiom_ids"), list))
        check("suggestion item has reason string", isinstance(s0.get("reason"), str) and len(s0["reason"]) > 0)
        check("suggestion item has suggested_review_only",
              isinstance(s0.get("suggested_action"), str) and "人工审核" in s0["suggested_action"])

    bad = no_verdict_words(r.get("disclaimer", ""))
    for s in r["suggestions"]:
        bad += no_verdict_words(s.get("reason", ""))
        bad += no_verdict_words(s.get("suggested_action", ""))
    check("semantic_contradiction NO verdict words", len(bad) == 0, f"bad: {bad}")


def test_check_semantic_contradiction_rejects_bad_axioms():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)

    expect_raises("semantic_contradiction str input raises",
                  lambda: llm.check_semantic_contradiction("x"), "list")
    expect_raises("semantic_contradiction axiom missing axiom_id raises",
                  lambda: llm.check_semantic_contradiction([{"hypothesis": "x"}]), "axiom_id")


def test_from_config_defaults_offline_disallows_live_without_flag():
    """默认 config 无 llm 字段时，from_config 必须走 use_model=False 模式。"""
    from w2.llm_assistant import LLMAssistant
    import os as _os
    cfg_path = _os.path.join(_os.path.dirname(__file__), "..", "config", "experiment_config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    llm = LLMAssistant.from_config(cfg)
    # 当前 config 未设置 llm.use_model → 必须 fallback 到离线，避免静默联网
    check("from_config default cfg → offline mode", llm.mode == "offline")
    check("from_config default cfg → use_model=False", llm.use_model is False)


def test_from_config_with_llm_offline_flag_stays_offline():
    from w2.llm_assistant import LLMAssistant
    cfg = {"llm": {"use_model": False, "mode_note": "offline"}}
    llm = LLMAssistant.from_config(cfg)
    check("from_config explicit offline stays offline", llm.mode == "offline")
    check("from_config explicit offline use_model=False", llm.use_model is False)


def test_mode_metadata_consistent_across_calls():
    """离线模式下每次调用的 meta.mode 都必须是 offline。"""
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)
    r1 = llm.hypothesis_nl({"axiom_id": "A1", "anchor": "P1", "predicate": "p"})
    r2 = llm.summarize_downgrade({"axiom_id": "A1", "from_status": "active", "to_status": "warned", "reason": "r"})
    r3 = llm.check_semantic_contradiction([
        {"axiom_id": "A1", "hypothesis": "h1", "boundary_conditions": []},
    ])
    check("hypothesis_nl mode offline", r1["meta"]["mode"] == "offline")
    check("summarize_downgrade mode offline", r2["meta"]["mode"] == "offline")
    check("semantic mode offline", r3["meta"]["mode"] == "offline")


def test_verdict_stripping_when_event_carries_trigger_words():
    """即使传入数据中包含裁决类字眼（如公理引擎输出），辅助模块最终输出也必须剥离。"""
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=False)

    event = {
        "axiom_id": "A1",
        "from_status": "active",
        "to_status": "warned",
        # 这些是上游数据，不是辅助模块要做的裁决
        "reason": "系统判定触发降级预警",
    }
    r = llm.summarize_downgrade(event)
    bad = no_verdict_words(r["summary"]) + no_verdict_words("".join(r["key_points"]))
    check("verdict stripping: no verdict phrase leaked into summary/points",
          len(bad) == 0, f"leaked: {bad}; summary={r['summary']!r}")


# ── 审计 A1：live 分支（mock 适配器，不加载真实模型，纯离线可测）──


class FakeAdapter:
    """mock 模型适配器：loaded=True + 可控 generate_text（供 live 分支测试）。"""

    def __init__(self, text="", fail=False):
        self.loaded = True
        self._text = text
        self._fail = fail

    def generate_text(self, prompt):
        if self._fail:
            raise RuntimeError("mock generation failure")
        return self._text


def test_live_mode_with_adapter_uses_model_text():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(
        use_model=True,
        model_adapter=FakeAdapter(text="该假设在当前虚拟实例上表现良好。"))
    check("live attach mode == live", llm.mode == "live")
    r = llm.hypothesis_nl({"axiom_id": "A1", "anchor": "P1", "predicate": "p"})
    check("live text from model", "表现良好" in r["text"], r["text"])
    check("live meta.mode == live", r["meta"]["mode"] == "live")
    check("live meta.model_generated True",
          r["meta"]["model_generated"] is True)
    check("live advisory True", r["advisory"] is True)
    bad = no_verdict_words(r["text"])
    check("live NO verdict words", len(bad) == 0, f"bad: {bad}")


def test_live_mode_strips_verdict_words_from_model_text():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(
        use_model=True,
        model_adapter=FakeAdapter(text="系统判定触发降级，公理判定通过。"))
    r = llm.summarize_downgrade(
        {"axiom_id": "A1", "from_status": "active", "to_status": "warned"})
    bad = no_verdict_words(r["summary"])
    check("live verdict words stripped", len(bad) == 0, f"bad: {bad}")


def test_live_mode_fallback_offline_on_adapter_failure():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=True, model_adapter=FakeAdapter(fail=True))
    r = llm.hypothesis_nl({"axiom_id": "A1", "anchor": "P1", "predicate": "p"})
    # mode 是配置态（live 保持稳定）；本次生成失败以 model_generated=False
    # + fallback_note 记录，不翻转实例模式（避免一次失败永久离线）。
    check("live failure keeps config mode live", r["meta"]["mode"] == "live")
    check("live failure model_generated False",
          r["meta"]["model_generated"] is False)
    check("live failure fallback_note recorded",
          "fallback" in str(r["meta"].get("fallback_note", "")))
    check("live failure still advisory", r["advisory"] is True)
    check("live failure text non-empty (template)", len(r["text"]) > 0)


def test_attach_model_adapter_switches_to_live():
    from w2.llm_assistant import LLMAssistant
    llm = LLMAssistant(use_model=True)  # adapter 缺失 → offline
    check("before attach offline", llm.mode == "offline")
    llm.attach_model_adapter(FakeAdapter(text="ok"))
    check("after attach live", llm.mode == "live")


if __name__ == "__main__":
    test_hypothesis_nl_structure_and_disclaimer()
    test_hypothesis_nl_rejects_missing_required()
    test_summarize_downgrade_structure_and_no_verdict()
    test_summarize_downgrade_rejects_bad_event()
    test_check_semantic_contradiction_reference_only()
    test_check_semantic_contradiction_rejects_bad_axioms()
    test_from_config_defaults_offline_disallows_live_without_flag()
    test_from_config_with_llm_offline_flag_stays_offline()
    test_mode_metadata_consistent_across_calls()
    test_verdict_stripping_when_event_carries_trigger_words()
    test_live_mode_with_adapter_uses_model_text()
    test_live_mode_strips_verdict_words_from_model_text()
    test_live_mode_fallback_offline_on_adapter_failure()
    test_attach_model_adapter_switches_to_live()

    print("=" * 50)
    print(f"S14 LLM 辅助模块自测: {PASS} passed, {FAIL} failed")
    print("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)
