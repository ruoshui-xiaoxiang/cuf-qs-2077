"""S23 实验结论整理离线自测（RED→GREEN 契约）。

结论生成契约（S23 锁定）：
- 结论由结构化数据机械编译生成（conclusion_report.py 消费
  formal_batch_summary + BatchVisualizer 统计），非手写文本——
  因果链可追溯（铁则 #4），同数据再生结论字节级一致（无时间戳）。
- 必含部分：定位声明（#14）/ 实验假设↔锚点映射表（五行全字段：
  假设/锚点/证伪判据/判定/证据）/ 主要结果 / 明确局限 /
  复现指引 / 表述纪律声明。
- 表述纪律（锁定决策 #14）内建：全文禁词扫描（证明/证实），
  必含「操作化验证」与「描述性框架」定位表述。
- 产物：s23_conclusion.json（结构化，结论唯一依据）+
  s23_conclusion.md（人类可读，仅渲染前者）。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w3.conclusion_report import generate_conclusion_report
from s22_selftest import ensure_batch

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


# ── 测试 1：结论结构完整性 ────────────────────────────────────


def test_conclusion_structure():
    b = ensure_batch()
    with tempfile.TemporaryDirectory() as d:
        concl = generate_conclusion_report(str(b["dir"]), out_dir=d)
        for key in ("positioning", "hypothesis_anchor_table",
                    "main_results", "limitations", "reproducibility",
                    "discipline_statement"):
            assert key in concl, f"conclusion missing {key}"
        assert concl["mode"] == "s23_conclusion"
        # 落盘双产物
        assert (Path(d) / "s23_conclusion.json").exists()
        assert (Path(d) / "s23_conclusion.md").exists()
        saved = json.loads((Path(d) / "s23_conclusion.json")
                           .read_text(encoding="utf-8"))
        assert saved["main_results"] == concl["main_results"]


# ── 测试 2：假设↔锚点映射表（五行全字段 + 判定与统计一致）─────


def test_mapping_table():
    b = ensure_batch()
    with tempfile.TemporaryDirectory() as d:
        concl = generate_conclusion_report(str(b["dir"]), out_dir=d)
        table = concl["hypothesis_anchor_table"]
        assert len(table) == 5, f"must map all 5 anchors, got {len(table)}"
        ids = {row["anchor"] for row in table}
        assert ids == {"P_W2_5", "P_M1", "P_W1", "P_C", "H_L"}, ids
        for row in table:
            for field in ("hypothesis", "anchor", "falsification_criterion",
                          "verdict", "evidence"):
                assert field in row and row[field] not in (None, ""), \
                    f"{row.get('anchor')} missing {field}"
        verdicts = {row["anchor"]: row["verdict"] for row in table}
        # 与 S22 锚点判定一致（本批实证）
        assert verdicts["P_W2_5"] == "supported", verdicts
        assert verdicts["P_M1"] == "supported", verdicts
        assert verdicts["P_W1"] == "supported", verdicts
        assert verdicts["P_C"] == "not_triggered", verdicts
        assert verdicts["H_L"] == "not_tested", verdicts


# ── 测试 3：主要结果机械取自统计数据 ──────────────────────────


def test_main_results_from_stats():
    b = ensure_batch()
    with tempfile.TemporaryDirectory() as d:
        concl = generate_conclusion_report(str(b["dir"]), out_dir=d)
        mr = concl["main_results"]
        assert mr["runs_total"] == 9, mr
        assert mr["runs_completed_iteration"] == 9, mr
        assert mr["permutation_p_pooled"] <= 0.05, mr
        assert mr["permutation_p_pooled"] > 0, mr
        assert mr["virtual_lifespan_mean"] == 10, mr
        assert mr["all_converged"] is True, mr
        assert mr["axiom_coverage"] == {"A_M1": 3, "A_W1": 3, "A_W2_5": 3}, mr


# ── 测试 4：局限明确（关键项逐条在列）────────────────────────


def test_limitations_explicit():
    b = ensure_batch()
    with tempfile.TemporaryDirectory() as d:
        concl = generate_conclusion_report(str(b["dir"]), out_dir=d)
        lims = concl["limitations"]
        assert len(lims) >= 6, f"limitations too few: {len(lims)}"
        joined = " ".join(l["summary"] for l in lims)
        # 关键局限必须逐条在列
        for token in ("离线", "确定性嵌入", "mock", "种子", "P_C",
                      "H_L", "代理度量", "对照组"):
            assert token in joined, f"limitation missing token: {token}"
        for l in lims:
            assert "summary" in l and "impact" in l, l


# ── 测试 5：复现指引（命令 + 数据路径 + 回归证据）─────────────


def test_reproducibility_guide():
    b = ensure_batch()
    with tempfile.TemporaryDirectory() as d:
        concl = generate_conclusion_report(str(b["dir"]), out_dir=d)
        rep = concl["reproducibility"]
        assert "commands" in rep and len(rep["commands"]) >= 3, rep.keys()
        assert any("s22_selftest" in c for c in rep["commands"]), rep
        assert any("s21_selftest" in c for c in rep["commands"]), rep
        # 数据源路径 = 全部 9 次运行的归档（100% 可复现的物质基础）
        assert len(rep["data_sources"]) == 9, len(rep["data_sources"])
        for p in rep["data_sources"]:
            assert Path(p).exists(), f"archive missing: {p}"
        assert rep["regression"]["suites"] == 26, rep["regression"]
        assert rep["regression"]["assertions"] == 595, rep["regression"]


# ── 测试 6：表述纪律（#14）——禁词扫描 + 定位表述 ──────────────


def test_discipline_wording():
    b = ensure_batch()
    with tempfile.TemporaryDirectory() as d:
        concl = generate_conclusion_report(str(b["dir"]), out_dir=d)
        md = (Path(d) / "s23_conclusion.md").read_text(encoding="utf-8")
        for banned in ("证明", "证实"):
            assert banned not in md, f"md contains banned word: {banned}"
        # JSON 结论本体（除纪律声明字段本身）同样不得含禁词
        meta_free = {k: v for k, v in concl.items()
                     if k != "discipline_statement"}
        js_meta = json.dumps(meta_free, ensure_ascii=False)
        for banned in ("证明", "证实"):
            assert banned not in js_meta, f"json contains banned word: {banned}"
        assert "操作化验证" in md, "must state operationalization scope"
        assert "描述性框架" in md, "must state CUF v7 framework status"
        assert concl["discipline_statement"]["decision"] == "#14"
        assert concl["discipline_statement"]["banned_words"] == ["证明", "证实"]


# ── 测试 7：确定性再生——同数据字节级一致 ─────────────────────


def test_deterministic_regeneration():
    b = ensure_batch()
    with tempfile.TemporaryDirectory() as d:
        out1 = Path(d) / "r1"
        out2 = Path(d) / "r2"
        generate_conclusion_report(str(b["dir"]), out_dir=str(out1))
        generate_conclusion_report(str(b["dir"]), out_dir=str(out2))
        j1 = (out1 / "s23_conclusion.json").read_bytes()
        j2 = (out2 / "s23_conclusion.json").read_bytes()
        assert j1 == j2, "JSON conclusion must regenerate byte-identically"
        m1 = (out1 / "s23_conclusion.md").read_bytes()
        m2 = (out2 / "s23_conclusion.md").read_bytes()
        assert m1 == m2, "MD conclusion must regenerate byte-identically"


if __name__ == "__main__":
    check("conclusion_structure", test_conclusion_structure)
    check("mapping_table", test_mapping_table)
    check("main_results_from_stats", test_main_results_from_stats)
    check("limitations_explicit", test_limitations_explicit)
    check("reproducibility_guide", test_reproducibility_guide)
    check("discipline_wording", test_discipline_wording)
    check("deterministic_regeneration", test_deterministic_regeneration)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
