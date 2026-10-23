"""telegraph.py —— 预警色检测（候选尺，未标定前只当线索）

为什么要有这个：引擎侧读敌人动作（蒙太奇）两条路都试过——
  · tick 常驻读 → 切图时踩到失效 AnimInstance，native 崩溃两次；
  · 探针按需读 → 还没验证就不敢再往 tick 里放。
所以先走人眼那一套：**看预警光的颜色**。

公开资料一致的规则（蓝前紫后，黄闪红挡）：
  蓝光  → 前 + 闪避（闪现，绕到身后）
  紫光  → 后 + 闪避（击退）
  黄光  → 只能完美闪避，格挡无效
  红光/白光 → 完美格挡（精准按下格挡）

本脚本只回答两个问题：**有没有预警光、是什么颜色、什么时候**。
不做决策、不按键。它是行动层的眼睛，不是脑子。

⚠ 候选尺：阈值没经过实物标定前，任何“检出率/准确率”都不许当结论。
   验证方法：spike 时存帧（hit_*.png），逐帧人眼确认那一帧真有该颜色预警光；
   反向也要验：人眼确认有预警光的帧，脚本有没有报。
"""
import argparse
import csv
import os
import time

import numpy as np

import capture
import sb_bridge

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "runs", "telegraph")

# 色相用角度写，PIL 的 HSV 里 H 是 0..255，换算在代码里做。
RANGES = {
    "red":    [(0, 14), (346, 360)],
    "yellow": [(34, 70)],
    "blue":   [(185, 250)],
    "purple": [(251, 305)],
}
S_MIN, V_MIN = 135, 130          # 0..255；低饱和的灰紫环境光先挡掉


def _masks(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    ok = (s >= S_MIN) & (v >= V_MIN)
    out = {}
    for name, spans in RANGES.items():
        m = np.zeros(h.shape, dtype=bool)
        for a, b in spans:
            lo, hi = int(a * 255 / 360), int(b * 255 / 360)
            m |= (h >= lo) & (h <= hi)
        out[name] = int((m & ok).sum())
    return out


def counts_of(img):
    small = img.resize((1280, 720)) if img.width > 1280 else img
    hsv = np.asarray(small.convert("HSV"))
    return _masks(hsv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--min-px", type=int, default=700,
                    help="绝对下限（1280x720 下的像素数）；再叠加基线倍数")
    ap.add_argument("--k", type=float, default=2.6, help="超过基线的倍数才算尖峰")
    ap.add_argument("--cooldown", type=float, default=0.7, help="同类两次尖峰最小间隔")
    ap.add_argument("--save-frames", action="store_true", default=True)
    ap.add_argument("--tag", default="t")
    a = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    csv_path = os.path.join(OUTDIR, "counts_%s.csv" % a.tag)
    f = open(csv_path, "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["t", "red", "yellow", "blue", "purple", "dist_m", "x", "y", "spike"])

    base = {k: 1.0 for k in RANGES}
    last_spike = {k: -9e9 for k in RANGES}
    t0 = time.time()
    n = 0
    spikes = 0
    try:
        while time.time() - t0 < a.seconds:
            t = time.time() - t0
            img = capture.grab()
            c = counts_of(img)
            st = sb_bridge.read(0) or {}
            e = st.get("enemy") or {}
            p = st.get("player") or {}
            hit = ""
            for k in RANGES:
                # 基线用指数滑动平均：只有持续亮着的东西会被吸收掉
                if c[k] > base[k]:
                    base[k] = base[k] * 0.94 + c[k] * 0.06
                else:
                    base[k] = base[k] * 0.90 + c[k] * 0.10
                if (c[k] >= a.min_px and c[k] >= a.k * max(base[k], 1.0)
                        and t - last_spike[k] > a.cooldown):
                    last_spike[k] = t
                    hit += ("+" if hit else "") + k
                    if a.save_frames:
                        img.resize((1280, 720)).save(
                            os.path.join(OUTDIR, "hit_%s_%05d_%s.png"
                                         % (a.tag, int(t * 1000), k)))
            if hit:
                spikes += 1
            w.writerow([round(t, 3), c["red"], c["yellow"], c["blue"], c["purple"],
                        round(e.get("dist_cm", 0) / 100.0, 2) if e else "",
                        round(p.get("x", 0), 1), round(p.get("y", 0), 1), hit])
            n += 1
            print("%6.2fs red=%-6d yel=%-6d blue=%-6d pur=%-6d d=%s %s"
                  % (t, c["red"], c["yellow"], c["blue"], c["purple"],
                     e.get("dist_cm", 0) // 100 if e else "-", hit), flush=True)
    finally:
        f.close()
    print("结束：%d 帧 / %.1f fps，尖峰 %d 次，CSV=%s" %
          (n, n / max(time.time() - t0, 1e-6), spikes, csv_path), flush=True)


if __name__ == "__main__":
    main()
