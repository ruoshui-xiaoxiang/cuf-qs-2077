"""hud_energy.py —— 读左下 HUD 的 BU/BE/HP/SH 充能比例（像素法，候选尺）

为什么要它：技能压制流必须知道能量满没满。引擎侧读不到（没有血量/能量组件），
HUD 数字又没有 OCR 模板，但**分段条的几何位置是固定的**，数亮列就行。

坐标来自 3840x2160 最大化窗口的实拍帧（2026-10-24）：
  BU  y1880..1896  x150..535
  BE  y1910..1926  x150..535
  HP  y1943..1959  x150..970
  SH  y1975..1991  x150..540

⚠ 候选尺：判据是“亮列占比”，满格不会等于 1.0（段间有黑缝）。
   首次使用先拿已知状态标定（满血/空能量），比值只做相对比较，不当绝对真值。
"""
import numpy as np

BANDS = {
    "BU": (1880, 1896, 150, 535),
    "BE": (1910, 1926, 150, 535),
    "HP": (1943, 1959, 150, 970),
    "SH": (1975, 1991, 150, 540),
}
LIT = 110.0          # 灰度阈值：亮格 >110，暗格 ~40-60


def ratios(img, ox=0, oy=0):
    """img 可以是从 (ox, oy) 起裁出来的子图；默认整帧。"""
    out = {}
    for name, (y0, y1, x0, x1) in BANDS.items():
        a = np.asarray(img.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy)).convert("L"),
                       dtype=np.float32)
        col = a.mean(axis=0)
        out[name] = round(float((col > LIT).mean()), 3)
    return out


if __name__ == "__main__":
    import capture
    im = capture.grab()
    print(im.size, ratios(im))
