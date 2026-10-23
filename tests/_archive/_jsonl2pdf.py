"""JSONL 原始实验日志 → PDF（reportlab + 中文字体）。

把 rounds.jsonl 每行 JSON 格式化输出到 PDF（保留原始字段 + 中文）。
用法：py -3.12 tests/_jsonl2pdf.py <输出pdf> <输入jsonl1> [输入jsonl2 ...]
"""
import json
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Preformatted, HRFlowable, PageBreak)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("SimHei", r"C:\Windows\Fonts\simhei.ttf"))
try:
    pdfmetrics.registerFont(TTFont("SimSun", r"C:\Windows\Fonts\simsun.ttc",
                                   subfontIndex=0))
except Exception:
    pass

S_TITLE = ParagraphStyle("T", fontName="SimHei", fontSize=13, leading=18,
                         spaceBefore=6, spaceAfter=8)
S_FILE = ParagraphStyle("F", fontName="SimHei", fontSize=10, leading=14,
                        spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor("#374151"))
S_JSON = ParagraphStyle("J", fontName="SimSun", fontSize=6.3, leading=8)


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main():
    out_path = Path(sys.argv[1])
    in_paths = [Path(p) for p in sys.argv[2:]]
    doc = SimpleDocTemplate(str(out_path), pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title=out_path.stem)
    flow = []
    flow.append(Paragraph("CUF-QS 原始实验日志（rounds.jsonl）", S_TITLE))
    flow.append(Paragraph(f"共 {len(in_paths)} 份日志，逐轮 JSON 全量归档", S_FILE))
    flow.append(HRFlowable(width="100%", thickness=0.5,
                           color=colors.HexColor("#d1d5db")))

    for ip in in_paths:
        flow.append(Paragraph(f"日志：{ip}", S_FILE))
        lines = ip.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                obj = line
            txt = json.dumps(obj, ensure_ascii=False, indent=1)
            # 每轮前加轮次标记
            rid = obj.get("round_id") if isinstance(obj, dict) else None
            if rid is not None:
                flow.append(Paragraph(
                    f"———— 第 {rid} 轮 ————",
                    ParagraphStyle("R", fontName="SimHei", fontSize=8,
                                   leading=11, spaceBefore=6, spaceAfter=2,
                                   textColor=colors.HexColor("#0f172a"))))
            flow.append(Preformatted(_esc(txt), S_JSON))
        flow.append(PageBreak())

    doc.build(flow)
    print(f"[PDF] {out_path}  ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
