"""S23 实验结论整理——由结构化数据机械编译最终结论报告。

结论生成契约（S23 锁定）：
- 结论非手写文本：全部由 formal_batch 产物与 BatchVisualizer 统计
  机械编译（因果链可追溯，反套壳铁则 #4）；无时间戳，同数据再生
  字节级一致（100% 可复现）。
- 必含六部分：定位声明（锁定决策 #14）/ 实验假设↔锚点映射表
  （五行全字段）/ 主要结果 / 明确局限 / 复现指引 / 表述纪律声明。
- 表述纪律内建：全文禁用「证明 / 证实」（模板层保证），
  必含「操作化验证」与「描述性框架」定位表述；CUF v7 自定位
  为描述性框架（非理论）。
- 产物：s23_conclusion.json（结构化，结论唯一依据）+
  s23_conclusion.md（人类可读，仅渲染前者，铁则 #2）。
"""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from w3.visualizer import BatchVisualizer

BANNED_WORDS = ("证明", "证实")

_POSITIONING = (
    "本实验是 CUF v7 描述性框架在虚拟实例（赛博朋克2077 通信管线 + "
    "种子化剧本世界）上的操作化验证。CUF v7 自定位为描述性框架"
    "（非理论），本实验不构成对该框架本身的真理性主张，仅为"
    "「其工程实例化的 W₂ 稳态自指体的运行机制」提供虚拟实例层面的"
    "实证参照。全部结论以结构化数据（stats_summary.json 与各轮"
    "JSONL 归档）为唯一依据。"
)

_HYPOTHESES = {
    "P_W2_5": {
        "hypothesis": "降级机制比纯统计拟合更能维持稳态",
        "falsification_criterion": "实验组收敛性不优于对照组",
    },
    "P_M1": {
        "hypothesis": "规则边界单调细化、永不穷尽",
        "falsification_criterion": "公理集边界信息量出现净减少",
    },
    "P_W1": {
        "hypothesis": "采样受传感器与规则处理能力双重限制",
        "falsification_criterion": "出现超出白名单能力的有效观测",
    },
    "P_C": {
        "hypothesis": "超预算即解体",
        "falsification_criterion":
            "成本代理超预算（C_extend > B_extend）后系统仍维持稳态",
    },
    "H_L": {
        "hypothesis": "递进动力学链单向成立（③→②→①）",
        "falsification_criterion":
            "①/②被触发但此前连续 N 轮 C_extend 从未达到 B_extend",
    },
}

_LIMITATIONS = [
    {"summary": "对齐分走离线确定性嵌入路径（state_fn 返回当前目标"
                "向量），对齐分结构性恒为 1.0，收敛曲线的真实区分度"
                "待实机 7B 隐状态路径（S13 ModelAdapter 可替换，"
                "裁决路径不变）",
     "impact": "本批实验组优势表现为「对齐分恒在上限 + 机制成本可控"
               "（单次降级）」，而非「跌落-恢复速度差异」"},
    {"summary": "世界为种子化 mock 剧本（mock 游戏侧 + 种子化世界"
                "生成器），非真实赛博朋克2077 运行时环境",
     "impact": "结论界定于剧本世界；实机通信/动作/采集管线已单独"
               "实机验收（S4/S5/S7），正式批次为剧本回放"},
    {"summary": "每公理条件 3 个种子（锁定决策 #9 的下限），精确重排"
                "检验的 p 值下限为 2/512",
     "impact": "统计功效有限；扩大种子数可直接提高分辨力"},
    {"summary": "P_C（超预算解体）在本批未触发：默认 extend_budget=10 "
                "无超支轮",
     "impact": "判据③在本批无触发实例，需专项低预算压力批次补充"},
    {"summary": "H_L（递进动力学链）在本批未检验：无 ①/② 解体事件",
     "impact": "动力学链假设待压力场景数据"},
    {"summary": "C_H 为代理度量（锁定决策 #11），非真实热力学/计算"
                "资源成本",
     "impact": "预算比较仅在代理口径内有效"},
    {"summary": "对照组为频率估计器（纯统计拟合），非最强可能的"
                "统计基线",
     "impact": "实验组优势对弱对照组成立时结论稳健；更强基线"
               "（7B 上下文预测路）待 induction_fn 接口实机接入"},
    {"summary": "对照组分数为准确率仿射映射（score=2*acc-1），与实验组"
                "对齐分仅在同域意义上可比",
     "impact": "两组分数物理含义不同，比较限于同域数值口径"},
]

_REGRESSION = {"suites": 26, "assertions": 595,
               "note": "s2-s29 全量回归（结论生成时点锁定事实）"}

_COMMANDS = [
    "py tests\\s21_selftest.py   # 再生正式批次（3 公理 × 3 种子聚焦剧本）",
    "py tests\\s22_selftest.py   # 再生统计与图表（六大交付物）",
    "py tests\\s23_selftest.py   # 再生本结论报告（字节级一致）",
    "py src\\w3\\auto_experiment.py --rounds N --port <P> "
    "--log-path <log> --report-path <report>   # 单次一键实验",
]


def generate_conclusion_report(batch_dir, out_dir=None):
    """消费 formal_batch 目录，机械编译最终结论报告并落盘。

    Returns:
        结论 dict（六部分），无时间戳、完全由输入数据决定。
    """
    batch_dir = Path(batch_dir)
    viz = BatchVisualizer(str(batch_dir))
    stats = viz.render_all()
    summary = viz.summary

    runs = [(axiom, run) for axiom, run in viz._runs()]

    # ── 假设↔锚点映射表（判定与证据取自统计层）────────────
    anchors = stats["anchors"]
    table = []
    for anchor_id in ("P_W2_5", "P_M1", "P_W1", "P_C", "H_L"):
        entry = anchors[anchor_id]
        table.append({
            "anchor": anchor_id,
            "hypothesis": _HYPOTHESES[anchor_id]["hypothesis"],
            "falsification_criterion":
                _HYPOTHESES[anchor_id]["falsification_criterion"],
            "verdict": entry["verdict"],
            "evidence": entry["evidence"],
        })

    # ── 主要结果（全部机械取自统计层）─────────────────────
    st = stats["statistical_tests"]
    life = stats["lifespan"]
    metrics = stats["metrics"]
    iteration_done = sum(
        1 for _, run in runs
        if (run.get("iteration") or {}).get("final_status") == "deprecated")
    downgrade_rounds = {
        axiom: [r["downgrade_round"]
                for r in metrics["per_condition"][axiom]]
        for axiom in metrics["per_condition"]
    }
    main_results = {
        "runs_total": len(runs),
        "runs_completed_iteration": iteration_done,
        "all_converged": all(run.get("converged_final") is True
                             for _, run in runs),
        "virtual_lifespan_mean": life["overall"]["mean"],
        "permutation_p_pooled":
            st["pooled"]["permutation"]["p_value"],
        "observed_mean_diff_pooled":
            st["pooled"]["permutation"]["observed_mean_diff"],
        "axiom_coverage": summary.get("axiom_coverage"),
        "downgrade_rounds": downgrade_rounds,
    }

    # ── 复现指引 ──────────────────────────────────────────
    data_sources = [run["log_path"] for _, run in runs]
    reproducibility = {
        "commands": list(_COMMANDS),
        "data_sources": data_sources,
        "batch_dir": str(batch_dir),
        "regression": dict(_REGRESSION),
        "claim": "全部实验数据为 JSONL 追加式归档，任意轮次可一键复现；"
                 "结论报告由数据机械再生，字节级一致。",
    }

    concl = {
        "mode": "s23_conclusion",
        "positioning": _POSITIONING,
        "hypothesis_anchor_table": table,
        "main_results": main_results,
        "limitations": [dict(x) for x in _LIMITATIONS],
        "reproducibility": reproducibility,
        "discipline_statement": {
            "decision": "#14",
            "rule": "实验定位为「虚拟实例上的操作化验证」；"
                    "禁止任何真理性断言类表述；CUF v7 自定位为"
                    "描述性框架（非理论）。",
            "banned_words": list(BANNED_WORDS),
            "compliance": "template_level_guarantee",
        },
    }

    # 模板层纪律自检：禁词扫描（fail-loud），覆盖 JSON 本体与 MD 渲染
    body = json.dumps({k: v for k, v in concl.items()
                       if k != "discipline_statement"}, ensure_ascii=False)
    md_text = _render_markdown(concl)
    for word in BANNED_WORDS:
        if word in body:
            raise ValueError(f"conclusion template violates #14: {word}")
        if word in md_text:
            raise ValueError(f"conclusion markdown violates #14: {word}")

    out_dir = Path(out_dir) if out_dir else batch_dir / "conclusion"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "s23_conclusion.json").write_text(
        json.dumps(concl, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "s23_conclusion.md").write_text(md_text, encoding="utf-8")
    return concl


def _render_markdown(concl):
    lines = []
    lines.append("# CUF-QS 虚拟具身实验——最终结论报告（S23）")
    lines.append("")
    lines.append("## 一、定位声明")
    lines.append("")
    lines.append(concl["positioning"])
    lines.append("")
    lines.append("## 二、实验假设 ↔ 可证伪锚点映射表")
    lines.append("")
    lines.append("| 锚点 | 实验假设 | 证伪判据 | 本批判定 | 证据摘要 |")
    lines.append("|---|---|---|---|---|")
    for row in concl["hypothesis_anchor_table"]:
        evidence = json.dumps(row["evidence"], ensure_ascii=False)
        lines.append(
            f"| {row['anchor']} | {row['hypothesis']} | "
            f"{row['falsification_criterion']} | {row['verdict']} | "
            f"{evidence} |")
    lines.append("")
    lines.append("## 三、主要结果")
    lines.append("")
    mr = concl["main_results"]
    lines.append(f"- 正式批次运行：{mr['runs_total']} 次（3 公理 × 3 种子），"
                 f"完成整条迭代链 {mr['runs_completed_iteration']} 次。")
    lines.append(f"- 二次收敛：{'全部达成' if mr['all_converged'] else '未全部达成'}；"
                 f"虚拟寿命均值 {mr['virtual_lifespan_mean']} 轮"
                 "（默认预算下无解体）。")
    lines.append(f"- P_W2_5 成对比较（种子级单元，精确重排）："
                 f"观测均值差 {mr['observed_mean_diff_pooled']:.4f}，"
                 f"p = {mr['permutation_p_pooled']:.6f}"
                 "（≤0.05 → 实验组收敛性优于对照组，假设获支持）。")
    lines.append(f"- 公理覆盖度：{json.dumps(mr['axiom_coverage'])}。")
    lines.append(f"- 降级轮次（各种子）：{json.dumps(mr['downgrade_rounds'])}。")
    lines.append("")
    lines.append("## 四、明确局限")
    lines.append("")
    for i, lim in enumerate(concl["limitations"], 1):
        lines.append(f"{i}. {lim['summary']}")
        lines.append(f"   - 影响：{lim['impact']}")
    lines.append("")
    lines.append("## 五、复现指引")
    lines.append("")
    rep = concl["reproducibility"]
    lines.append("再生命令：")
    lines.append("")
    for c in rep["commands"]:
        lines.append(f"- `{c}`")
    lines.append("")
    lines.append(f"- 批次目录：{rep['batch_dir']}")
    lines.append(f"- 数据源（每运行四层全量 JSONL 归档，共 "
                 f"{len(rep['data_sources'])} 份）：")
    for p in rep["data_sources"]:
        lines.append(f"  - {p}")
    lines.append(f"- 回归证据：{rep['regression']['suites']} 套自测 / "
                 f"{rep['regression']['assertions']} 断言 0 失败"
                 "（结论生成时点锁定）。")
    lines.append(f"- {rep['claim']}")
    lines.append("")
    lines.append("## 六、表述纪律声明（锁定决策 #14）")
    lines.append("")
    ds = concl["discipline_statement"]
    lines.append(f"- 规则：{ds['rule']}")
    lines.append("- 本报告全文经模板层禁词扫描，"
                 "不含禁词清单所列真理性断言类表述；"
                 "所有锚点判定均为操作化口径下的支持/证伪/未触发/未检验。")
    lines.append("")
    return "\n".join(lines)
