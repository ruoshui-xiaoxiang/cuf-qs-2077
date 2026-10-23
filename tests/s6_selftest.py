"""S6 日志归档离线自测（锁定决策 #7 JSONL + #11 C_H 代理度量）。

覆盖：
- 单轮写入 + 读取复现
- 多轮追加 + list_rounds 顺序
- C_H 三项代理度量计算正确性（struct/adapt/extend）
- 预算超限检测
- 四层全量数据完整性
- from_config 加载
- 空文件 / 损坏行容错
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w3.log_recorder import LogRecorder, DEFAULT_BUDGET_B

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except AssertionError as e:
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}: {e}")
    except Exception as e:
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


def make_w0(n_entities=3, n_events=2):
    return {
        "round_id": "r0",
        "collection_ts": 1234567890.0,
        "entities": [
            {"entity_id": f"EID:{i}", "kind": "npc", "labels": ["enemy"],
             "hp": 100.0 - i * 10, "position": {"x": float(i), "y": 0.0, "z": 0.0},
             "sampling_status": "ok"}
            for i in range(n_entities)
        ],
        "events": [
            {"event_id": f"evt_{i}", "kind": "combat_attack",
             "source_entity": "EID:0", "target_entity": f"EID:{i}",
             "game_ts": 1000 + i, "round_id": "r0", "collection_ts": 1234567890.0}
            for i in range(n_events)
        ],
        "stats": {
            "raw_entities": n_entities,
            "raw_events": n_events,
            "invalid_entities": 0,
            "invalid_events": 0,
            "failed_entities": 0,
            "anomalous_entities": 0,
            "ok_entities": n_entities,
        },
    }


def make_w2(n_active=2, n_boundary=3, counterexamples=1, threshold=5):
    return {
        "axioms": [
            {
                "id": f"axiom_{i}",
                "status": "active" if i < n_active else "deprecated",
                "boundary_conditions": [f"bc_{j}" for j in range(n_boundary if i < n_active else 0)],
            }
            for i in range(n_active + 2)
        ],
        "cumulative_counterexamples": counterexamples,
        "downgrade_threshold": threshold,
        "model_update_count": 0,
    }


def make_w3():
    return {
        "action_type": "attack",
        "fn": "player_attack",
        "params": {"target_id": "EID:1"},
        "action_id": "act_1",
        "status": "completed",
        "accepted": True,
        "evidence": [{"event_id": "evt_0", "kind": "hp_changed"}],
        "polls": 2,
        "duration_s": 0.42,
    }


# ── 测试用例 ─────────────────────────────────────────────

def test_single_round_write_read():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        w0 = make_w0()
        w2 = make_w2()
        w3 = make_w3()
        entry = lr.record_round("r1", w0_data=w0, w1_data={"verified": True},
                                w2_data=w2, w3_data=w3)
        assert entry["round_id"] == "r1"
        assert "timestamp" in entry
        assert entry["w0"] == w0
        assert entry["w1"] == {"verified": True}
        assert entry["w2"] == w2
        assert entry["w3"] == w3

        loaded = lr.load_round("r1")
        assert loaded is not None, "load_round returned None"
        assert loaded["round_id"] == "r1"
        assert loaded["w0"]["entities"][0]["entity_id"] == "EID:0"
        assert loaded["w3"]["status"] == "completed"


def test_multi_round_append_order():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        for i in range(5):
            lr.record_round(f"r{i}", w0_data=make_w0(), w2_data=make_w2())
        rounds = lr.list_rounds()
        assert rounds == ["r0", "r1", "r2", "r3", "r4"], f"order wrong: {rounds}"
        # 确认是追加（文件 5 行）
        with open(Path(d) / "exp.jsonl") as f:
            lines = f.readlines()
        assert len(lines) == 5


def test_c_h_struct():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        w2 = make_w2(n_active=3, n_boundary=4)
        entry = lr.record_round("r1", w2_data=w2)
        d = entry["c_h"]["detail"]
        assert d["active_axioms"] == 3, f"expected 3, got {d['active_axioms']}"
        assert d["boundary_conditions"] == 12, f"expected 12, got {d['boundary_conditions']}"
        assert entry["c_h"]["struct"] == 15, f"expected 15, got {entry['c_h']['struct']}"


def test_c_h_adapt():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        w0 = make_w0(n_entities=5, n_events=3)
        w2 = make_w2()
        w2["model_update_count"] = 2
        entry = lr.record_round("r1", w0_data=w0, w2_data=w2)
        d = entry["c_h"]["detail"]
        assert d["sampling_count"] == 8, f"expected 8, got {d['sampling_count']}"
        assert d["model_updates"] == 2
        assert entry["c_h"]["adapt"] == 10, f"expected 10, got {entry['c_h']['adapt']}"


def test_c_h_extend():
    """逻辑学审查 A 前的旧用例（保持向后覆盖基础输入）：
    C_extend = min(counterexamples, threshold) 单调递增。"""
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        w2 = make_w2(counterexamples=2, threshold=5)
        entry = lr.record_round("r1", w2_data=w2)
        assert entry["c_h"]["extend"] == 2, f"expected min(2,5)=2, got {entry['c_h']['extend']}"

        # 反例数 >= 阈值时 extend 饱和于阈值
        w2b = make_w2(counterexamples=7, threshold=5)
        entry2 = lr.record_round("r2", w2_data=w2b)
        assert entry2["c_h"]["extend"] == 5, f"expected min(7,5)=5, got {entry2['c_h']['extend']}"


def test_budget_exceeded():
    """备忘录 4.2 语义：struct 或 extend 任一超预算即 within_budget=False。
    adapt 超仅打 overload 标记（不参与 Γ脆 翻转）。"""
    with tempfile.TemporaryDirectory() as d:
        # extend_budget 设为 1，累计反例 2 → extend=min(2,5)=2 > 1
        lr = LogRecorder(Path(d) / "exp.jsonl",
                         extend_budget=1, struct_budget=9999, adapt_budget=9999)
        w2 = make_w2(counterexamples=2, threshold=5)
        entry = lr.record_round("r1", w2_data=w2)
        assert entry["c_h"]["extend"] == 2
        assert entry["c_h"]["overloads"]["extend_overload"] is True
        # P_C 触发
        assert entry["c_h"]["within_budget"] is False
        assert entry["c_h"]["budgets"]["extend_budget"] == 1


def test_budget_within():
    """全部分量在预算内 → within_budget=True。"""
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0,
                         extend_budget=10, struct_budget=100, adapt_budget=1000)
        w0 = make_w0(n_entities=2, n_events=1)
        w2 = make_w2(n_active=1, n_boundary=1, counterexamples=0, threshold=5)
        entry = lr.record_round("r1", w0_data=w0, w2_data=w2)
        assert entry["c_h"]["within_budget"] is True


def test_empty_layers():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        entry = lr.record_round("r1")
        assert entry["c_h"]["struct"] == 0
        assert entry["c_h"]["adapt"] == 0
        # 逻辑学审查 A：ce=0 → extend=min(0,5)=0（旧语义是 5）
        assert entry["c_h"]["extend"] == 0
        assert entry["c_h"]["total"] == 0
        assert entry["w0"] == {}
        assert entry["w3"] == {}


def test_load_nonexistent_round():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        assert lr.load_round("nope") is None
        assert lr.list_rounds() == []


def test_corrupt_line_skipped():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "exp.jsonl"
        p.write_text('{"round_id":"r1"}\nNOT_JSON\n{"round_id":"r2"}\n', encoding="utf-8")
        lr = LogRecorder(p, budget_b=100.0)
        rounds = lr.list_rounds()
        assert rounds == ["r1", "r2"], f"expected [r1, r2], got {rounds}"


def test_from_config():
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "experiment_config.json"
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder.from_config(config_path=str(cfg_path),
                                     log_path=Path(d) / "exp.jsonl")
        assert lr.budget_b == 100.0, f"budget_b from config: {lr.budget_b}"
        entry = lr.record_round("r1", w0_data=make_w0(), w2_data=make_w2())
        assert entry["c_h"]["budget_b"] == 100.0


def test_full_round_replay():
    """验收核心：任意轮次可一键复现状态。"""
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        w0 = make_w0(n_entities=4, n_events=3)
        w1 = {"rules_checked": 3, "violations": 0}
        w2 = make_w2(n_active=2, n_boundary=2, counterexamples=1, threshold=5)
        w3 = make_w3()
        lr.record_round("r1", w0_data=w0, w1_data=w1, w2_data=w2, w3_data=w3)
        # 混入第二轮
        lr.record_round("r2", w0_data=make_w0(n_entities=1, n_events=0),
                        w2_data=make_w2(n_active=1, n_boundary=0))
        # 复现 r1
        r1 = lr.load_round("r1")
        assert r1 is not None
        assert len(r1["w0"]["entities"]) == 4
        assert len(r1["w0"]["events"]) == 3
        assert r1["w1"]["rules_checked"] == 3
        assert r1["w2"]["axioms"][0]["status"] == "active"
        assert r1["w3"]["action_id"] == "act_1"
        assert r1["c_h"]["struct"] == 6  # 2 active + 2*2 boundary
        assert r1["c_h"]["adapt"] == 7   # 4+3 sampling + 0 model
        # 逻辑学审查 A：ce=1 → extend=min(1,5)=1（旧语义阈值-反例=4）
        assert r1["c_h"]["extend"] == 1
        assert r1["c_h"]["total"] == 14  # 6+7+1
        assert r1["c_h"]["within_budget"] is True


def test_deprecated_axioms_excluded_from_struct():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        w2 = {
            "axioms": [
                {"id": "a1", "status": "active", "boundary_conditions": ["b1", "b2"]},
                {"id": "a2", "status": "deprecated", "boundary_conditions": ["b3"]},
                {"id": "a3", "status": "active", "boundary_conditions": []},
            ],
            "cumulative_counterexamples": 3,
            "downgrade_threshold": 5,
        }
        entry = lr.record_round("r1", w2_data=w2)
        d = entry["c_h"]["detail"]
        assert d["active_axioms"] == 2
        assert d["boundary_conditions"] == 2  # only a1's b1+b2, a3 has 0
        assert entry["c_h"]["struct"] == 4
        # 逻辑学审查 A：C_extend=min(3,5)=3（旧语义阈值-反例=2）
        assert entry["c_h"]["extend"] == 3


def test_warned_axioms_counted_as_active():
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl", budget_b=100.0)
        w2 = {
            "axioms": [
                {"id": "a1", "status": "active", "boundary_conditions": ["b1"]},
                {"id": "a2", "status": "warned", "boundary_conditions": ["b2", "b3"]},
                {"id": "a3", "status": "deprecated", "boundary_conditions": ["b4"]},
            ],
            "cumulative_counterexamples": 0,
            "downgrade_threshold": 5,
        }
        entry = lr.record_round("r1", w2_data=w2)
        d = entry["c_h"]["detail"]
        assert d["active_axioms"] == 2  # active + warned
        assert d["boundary_conditions"] == 3  # a1(1) + a2(2)
        assert entry["c_h"]["struct"] == 5
        # ce=0 → extend=0（新语义）
        assert entry["c_h"]["extend"] == 0
        # struct=5, adapt=0, extend=0 → 均未超预算
        assert entry["c_h"]["within_budget"] is True


# ── 逻辑学审查修复（A/B：C_extend 方向 + 分项预算）────

def test_c_extend_monotonic_increase_with_counterexamples():
    """隐患 A：C_extend 必须随反例数单调递增（= min(N, threshold) 方案）。"""
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(
            Path(d) / "exp.jsonl",
            struct_budget=50, extend_budget=10, adapt_budget=1000,
        )
        vals = []
        for n in [0, 1, 2, 3, 4, 5, 7, 100]:
            w2 = make_w2(counterexamples=n, threshold=5)
            e = lr.record_round(f"r{n}", w2_data=w2)
            vals.append(e["c_h"]["extend"])
        # 期望：[0,1,2,3,4,5,5,5] —— 到阈值后饱和
        assert vals == [0, 1, 2, 3, 4, 5, 5, 5], f"monotonic fail: {vals}"
        # 0 反例时 C_extend == 0（不是 threshold，旧实现是 threshold）
        assert vals[0] == 0, f"ce=0 should be 0, got {vals[0]}"


def test_component_budgets_struct_or_extend_flip():
    """备忘录 4.2（2026-08-25 锁定，S29.13 实施）：C_struct > B_struct OR
    C_extend > B_extend → within_budget=False（struct 纳入 Γ脆 判据③，
    刹车必须真生效）；adapt 超仅打 overload 标记，不翻转。
    （本用例 2026-08-26 更新：原断言停留在修正前旧语义，实跑已失败。）"""
    with tempfile.TemporaryDirectory() as dd:
        # 分项预算极低，便于触发
        lr = LogRecorder(
            Path(dd) / "exp.jsonl",
            struct_budget=2, extend_budget=2, adapt_budget=5,
        )
        # ① struct 超（3 active + 3 bc = 6 > 2），extend/adapt 没超
        w2_s = make_w2(n_active=3, n_boundary=1, counterexamples=1, threshold=5)
        e_s = lr.record_round("rs", w2_data=w2_s)
        assert e_s["c_h"]["struct"] == 6
        assert e_s["c_h"]["extend"] == 1  # 1 <= 2
        assert e_s["c_h"]["adapt"] == 0  # 没给 w0
        assert e_s["c_h"]["overloads"]["struct_overload"] is True
        assert e_s["c_h"]["overloads"]["extend_overload"] is False
        assert e_s["c_h"]["overloads"]["adapt_overload"] is False
        # 备忘录 4.2：struct 超即翻转 within_budget（Γ脆 刹车生效）
        assert e_s["c_h"]["within_budget"] is False, \
            f"struct exceed MUST flip within_budget, got {e_s['c_h']}"

        # ② adapt 超：w0 9 样本 > 5 预算；须把 struct/extend 压在预算内
        # （struct=2 ≤ 2、extend=0），才是纯净的「仅 adapt 超」场景。
        # 旧用例用默认 w2（struct=8>2）依赖 struct 不翻转的旧语义。
        w0_a = make_w0(n_entities=5, n_events=4)  # 5+4=9
        w2_a = make_w2(n_active=1, n_boundary=1, counterexamples=0, threshold=5)
        e_a = lr.record_round("ra", w0_data=w0_a, w2_data=w2_a)
        assert e_a["c_h"]["adapt"] == 9
        assert e_a["c_h"]["overloads"]["adapt_overload"] is True
        assert e_a["c_h"]["overloads"]["extend_overload"] is False
        assert e_a["c_h"]["within_budget"] is True, \
            f"adapt-only exceed must NOT flip within_budget, got {e_a['c_h']}"

        # ③ extend 超：反例 3，预算 2 —— min(3,5)=3 > 2
        w2_e = make_w2(counterexamples=3, threshold=5)
        e_e = lr.record_round("re", w2_data=w2_e)
        assert e_e["c_h"]["extend"] == 3
        assert e_e["c_h"]["overloads"]["extend_overload"] is True
        # P_C 判据：extend 超直接翻转 within_budget → 可触发解体
        assert e_e["c_h"]["within_budget"] is False, \
            f"extend exceed MUST flip within_budget, got {e_e['c_h']}"

        # ④ 三者都在预算内
        w2_ok = make_w2(n_active=1, n_boundary=1, counterexamples=0, threshold=5)
        w0_ok = make_w0(n_entities=1, n_events=1)
        e_ok = lr.record_round("rok", w0_data=w0_ok, w2_data=w2_ok)
        assert e_ok["c_h"]["overloads"]["struct_overload"] is False
        assert e_ok["c_h"]["overloads"]["adapt_overload"] is False
        assert e_ok["c_h"]["overloads"]["extend_overload"] is False
        assert e_ok["c_h"]["within_budget"] is True


def test_component_budgets_inherit_defaults():
    """不传分项预算时，使用默认值。"""
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder(Path(d) / "exp.jsonl")
        e = lr.record_round("r1")
        ov = e["c_h"]["overloads"]
        assert "struct_overload" in ov and "adapt_overload" in ov and "extend_overload" in ov
        assert e["c_h"]["budgets"]["struct_budget"] == 50
        assert e["c_h"]["budgets"]["extend_budget"] == 10
        assert e["c_h"]["budgets"]["adapt_budget"] == 1000


def test_from_config_reads_component_budgets():
    """from_config 读取 c_h 分项预算配置。"""
    cfg_path = Path(__file__).resolve().parents[1] / "config" / "experiment_config.json"
    with tempfile.TemporaryDirectory() as d:
        lr = LogRecorder.from_config(config_path=str(cfg_path),
                                     log_path=Path(d) / "exp.jsonl")
        assert hasattr(lr, "struct_budget"), "struct_budget attr missing"
        assert hasattr(lr, "extend_budget"), "extend_budget attr missing"
        assert hasattr(lr, "adapt_budget"), "adapt_budget attr missing"


# ── 主入口 ───────────────────────────────────────────────

if __name__ == "__main__":
    check("single_round_write_read", test_single_round_write_read)
    check("multi_round_append_order", test_multi_round_append_order)
    check("c_h_struct", test_c_h_struct)
    check("c_h_adapt", test_c_h_adapt)
    check("c_h_extend", test_c_h_extend)
    check("budget_exceeded", test_budget_exceeded)
    check("budget_within", test_budget_within)
    check("empty_layers", test_empty_layers)
    check("load_nonexistent_round", test_load_nonexistent_round)
    check("corrupt_line_skipped", test_corrupt_line_skipped)
    check("from_config", test_from_config)
    check("full_round_replay", test_full_round_replay)
    check("deprecated_axioms_excluded_from_struct", test_deprecated_axioms_excluded_from_struct)
    check("warned_axioms_counted_as_active", test_warned_axioms_counted_as_active)
    # ── 逻辑学审查修复 A/B ──
    check("c_extend_monotonic_increase", test_c_extend_monotonic_increase_with_counterexamples)
    check("component_budgets_struct_or_extend_flip", test_component_budgets_struct_or_extend_flip)
    check("component_budgets_defaults", test_component_budgets_inherit_defaults)
    check("from_config_component_budgets", test_from_config_reads_component_budgets)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
