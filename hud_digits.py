"""hud_digits.py —— 用 cv2 读 HUD 数值（模板匹配，不装 OCR）

为什么不用数格：格会被技能特效/白光遮挡，实测同一状态下能读出"掉 62 格"这种假数。
数字是画在最上层的，且字体位置固定，适合模板匹配。

做法：
  1. 固定框裁出四行数值区（3840x2160 实测的分数坐标）
  2. 二值化 → 按列投影切字形 → 统一缩放到 20x28
  3. 与 assets/digits/ 下的模板做 L2 最近邻
  4. 模板由"已知真值"的帧自标定：我肉眼读过的帧给出字符串，按位置给字形打标签

自检：`py -3.12 hud_digits.py --selftest` 会在当前帧上读四个数并与模板来源对照。

🔴 2026-10-23 实测结论：**在沙漠这种亮背景场景，这套阈值法不可靠**。
   训练场背景是灰蓝 (178,185,197)，白字能分出来；沙漠沙地本身又亮又低饱和，
   自动定位会找到 6 条"假文字带"（把地形当字）。
   ⇒ 有 UE4SS 桥之后，**数值应该从引擎读**（state.json），OCR 只用于
   引擎读不到的东西（比如敌人头顶浮动条）。别再往这里加时间。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, "assets", "digits")

# 3840x2160 实测的行带与数值框（分数坐标）。数值在条的右侧、左对齐。
BANDS = {
    "BU": (0.848, 0.870),
    "BE": (0.870, 0.892),
    "HP": (0.892, 0.918),
    "SH": (0.918, 0.940),
}
TEXT_X = (0.395, 0.520)      # 数值区横向范围
GLYPH_H, GLYPH_W = 28, 20
MIN_H, MAX_H = 10, 40        # 字形高度过滤
BIN_THRESHOLD = 140          # 白字（带黑描边）在暗背景上取亮部


def crop_text(img, band):
    a = np.array(img.convert("RGB"))
    h, w, _ = a.shape
    f0, f1 = BANDS[band]
    y0, y1 = int(h * f0), int(h * f1)
    x0, x1 = int(w * TEXT_X[0]), int(w * TEXT_X[1])
    return a[y0:y1, x0:x1]


def binarize(sub):
    mn = sub.min(axis=2)
    mx = sub.max(axis=2)
    return ((mn > BIN_THRESHOLD) & ((mx - mn) < 70)).astype(np.uint8) * 255


def segment(binary):
    """按列投影切字形，返回 (glyph_img 列表, 每格 bbox)。"""
    cols = (binary > 0).sum(axis=0)
    on = cols >= 2
    spans, start = [], None
    for x, v in enumerate(on):
        if v and start is None:
            start = x
        elif not v and start is not None:
            spans.append((start, x))
            start = None
    if start is not None:
        spans.append((start, len(on)))
    out, boxes = [], []
    rows = np.where((binary > 0).sum(axis=1) >= 2)[0]
    if len(rows) == 0:
        return out, boxes
    ry0, ry1 = rows[0], rows[-1] + 1
    for x0, x1 in spans:
        w = x1 - x0
        h = ry1 - ry0
        if w < 2 or h < MIN_H or h > MAX_H or w > 40:
            continue
        g = binary[ry0:ry1, x0:x1]
        g = cv2.resize(g, (GLYPH_W, GLYPH_H), interpolation=cv2.INTER_AREA)
        out.append(g.astype(np.float32) / 255.0)
        boxes.append((x0, x1, w, h))
    return out, boxes


def load_templates():
    tpl = {}
    if not os.path.isdir(TPL_DIR):
        return tpl
    for fn in os.listdir(TPL_DIR):
        if not fn.endswith(".png"):
            continue
        ch = fn[:-4]
        tpl.setdefault(ch, []).append(
            cv2.imread(os.path.join(TPL_DIR, fn), cv2.IMREAD_GRAYSCALE)
            .astype(np.float32) / 255.0)
    return tpl


def build(frame, band, truth):
    """用一帧 + 已知字符串自标定模板。truth 里不含空格的字符须与字形数一致。"""
    glyphs, boxes = segment(binarize(crop_text(frame, band)))
    exp = [c for c in truth if c != " "]
    if len(glyphs) != len(exp):
        return None, "字形数 %d != 期望字符数 %d（truth=%r）" % (
            len(glyphs), len(exp), truth)
    os.makedirs(TPL_DIR, exist_ok=True)
    added = 0
    for g, ch in zip(glyphs, exp):
        if not (ch.isdigit() or ch in ",/."):
            continue
        existing = load_templates().get(ch, [])
        if any(np.abs(e - g).mean() < 0.02 for e in existing):
            continue
        cv2.imwrite(os.path.join(TPL_DIR, "%s.png" % ch),
                    (g * 255).astype(np.uint8))
        added += 1
    return len(glyphs), added


def read_band(frame, band, tpl):
    glyphs, boxes = segment(binarize(crop_text(frame, band)))
    if not tpl or not glyphs:
        return None, "无模板或未切到字形"
    out, scores = [], []
    for g in glyphs:
        best, bestd = None, 1e9
        for ch, refs in tpl.items():
            for r in refs:
                d = float(np.abs(r - g).mean())
                if d < bestd:
                    best, bestd = ch, d
        out.append(best)
        scores.append(round(bestd, 3))
    return "".join(out), scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="用当前帧自标定模板")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    import capture

    frame = capture.grab()
    truth = {"BU": "1,600/1,600", "BE": "2,450/2,800",
             "HP": "6,500/6,500", "SH": "500/500"}
    if args.build:
        for b, t in truth.items():
            n, info = build(frame, b, t)
            print("%-3s %s 字形=%s 新增模板=%s" % (b, t, n, info))
        return
    tpl = load_templates()
    print("模板字符:", "".join(sorted(tpl)))
    for b, t in truth.items():
        s, sc = read_band(frame, b, tpl)
        print("%-3s 读=%s 真值=%s %s" % (b, s, t, "OK" if s == t else "≠"))
        if isinstance(sc, list):
            print("     距离:", sc)


if __name__ == "__main__":
    main()
