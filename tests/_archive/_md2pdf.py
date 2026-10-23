"""Markdown → PDF 转换器（reportlab + 系统中文字体）。

把实验报告 md 转成 PDF 存到桌面。支持：标题 #/##/###、表格、无序/有序
列表、代码块、引用 >、分隔线 ---、普通段落、行内 **粗体** 与 `代码`。

用法：py -3.12 tests/_md2pdf.py <输入md> <输出pdf路径>
"""
import re
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Preformatted, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── 中文字体注册 ──────────────────────────────────────────
_SIMHEI = r"C:\Windows\Fonts\simhei.ttf"
_SIMSUN = r"C:\Windows\Fonts\simsun.ttc"

pdfmetrics.registerFont(TTFont("SimHei", _SIMHEI))
try:
    pdfmetrics.registerFont(TTFont("SimSun", _SIMSUN, subfontIndex=0))
    _BODY_FONT = "SimSun"
except Exception:
    _BODY_FONT = "SimHei"
pdfmetrics.registerFontFamily(
    "SimSun", normal="SimSun", bold="SimHei", italic="SimSun",
    boldItalic="SimHei")
pdfmetrics.registerFontFamily(
    "SimHei", normal="SimHei", bold="SimHei", italic="SimHei",
    boldItalic="SimHei")

_S = getSampleStyleSheet()
S_TITLE = ParagraphStyle("T", fontName="SimHei", fontSize=18, leading=24,
                         spaceAfter=12, alignment=TA_LEFT)
S_H1 = ParagraphStyle("H1", fontName="SimHei", fontSize=15, leading=20,
                      spaceBefore=14, spaceAfter=8, textColor=colors.HexColor("#0f172a"))
S_H2 = ParagraphStyle("H2", fontName="SimHei", fontSize=13, leading=18,
                      spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#374151"))
S_H3 = ParagraphStyle("H3", fontName="SimHei", fontSize=11.5, leading=16,
                      spaceBefore=8, spaceAfter=4)
S_BODY = ParagraphStyle("BODY", fontName=_BODY_FONT, fontSize=10, leading=15,
                        spaceAfter=6)
S_QUOTE = ParagraphStyle("QUOTE", fontName=_BODY_FONT, fontSize=9, leading=13,
                         leftIndent=8, textColor=colors.HexColor("#6b7280"),
                         spaceAfter=6)
S_CODE = ParagraphStyle("CODE", fontName="SimHei", fontSize=8, leading=11,
                        backColor=colors.HexColor("#f3f4f6"),
                        borderPadding=4, spaceAfter=6)
S_CELL = ParagraphStyle("CELL", fontName=_BODY_FONT, fontSize=8.5, leading=12)
S_CELLH = ParagraphStyle("CELLH", fontName="SimHei", fontSize=8.5, leading=12)


def _inline(md_text):
    """行内格式：`code` → <font>，**bold** → <b>，转义 HTML。"""
    s = md_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"`([^`]+)`", r'<font face="SimHei">\1</font>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s


def _is_table_sep(line):
    return bool(re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", line)) and "-" in line


def _parse_table(rows):
    cells = []
    for row in rows:
        row = row.strip().strip("|")
        cells.append([c.strip() for c in row.split("|")])
    return cells


def build_flowables(md_path):
    lines = Path(md_path).read_text(encoding="utf-8").splitlines()
    flow = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 代码块
        if stripped.startswith("```"):
            code = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            flow.append(Preformatted("\n".join(code), S_CODE))
            continue

        # 分隔线
        if re.match(r"^-{3,}\s*$", stripped):
            flow.append(HRFlowable(width="100%", thickness=0.5,
                                   color=colors.HexColor("#d1d5db"),
                                   spaceAfter=6))
            i += 1
            continue

        # 表格（连续 | 行）
        if stripped.startswith("|"):
            tbl_rows = []
            while i < n and lines[i].strip().startswith("|"):
                cur = lines[i].strip()
                if not _is_table_sep(cur):
                    tbl_rows.append(cur)
                i += 1
            if tbl_rows:
                data = _parse_table(tbl_rows)
                header = data[0]
                body = data[1:]
                # 转 Paragraph 支持换行
                pdata = []
                for r, row in enumerate([header] + body):
                    style = S_CELLH if r == 0 else S_CELL
                    pdata.append([Paragraph(_inline(c), style) for c in row])
                tbl = Table(pdata, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]))
                flow.append(tbl)
                flow.append(Spacer(1, 8))
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = _inline(m.group(2))
            if level == 1:
                flow.append(Paragraph(text, S_H1))
            elif level == 2:
                flow.append(Paragraph(text, S_H2))
            else:
                flow.append(Paragraph(text, S_H3))
            i += 1
            continue

        # 列表
        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            flow.append(Paragraph("• " + _inline(m.group(1)), S_BODY))
            i += 1
            continue
        m = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if m:
            flow.append(Paragraph("• " + _inline(m.group(1)), S_BODY))
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            flow.append(Paragraph(_inline(stripped.lstrip("> ")), S_QUOTE))
            i += 1
            continue

        # 普通段落
        flow.append(Paragraph(_inline(stripped), S_BODY))
        i += 1
    return flow


def main():
    md_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=md_path.stem)
    flow = build_flowables(md_path)
    doc.build(flow)
    print(f"[PDF] {out_path}  ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
