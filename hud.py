"""hud.py —— 把敌人头顶的浮动血条/护盾条量成数字（观测，不做决策）

两个必须记住的事实（都是 2026-10-23 实测踩出来的）：
1. 这两条是**世界空间浮动条**，跟着敌人头部在屏幕上走，
   ⇒ 不许用固定 y 带量（第一版就是这么扫空的）。这里改成全带搜索自动定位。
2. 训练场背景本身就是浅灰蓝 (178,185,197)，白条约 (226,228,231)，
   护盾条是青绿而非纯蓝，对比度很低
   ⇒ 阈值按实测色定，且必须在 ≥1920 宽的帧上量；1280 缩略图上条高只有 3px，量不准。

判据含义（用户 2026-10-23 指正）：打一下**只掉护盾，不掉血**。
所以 hit 的敏感信号是 SH 排亮列数下降，HP 排要等盾破了才动。
"""
from PIL import Image


def _cls(c):
    r, g, b = c
    if min(c) > 206 and (max(c) - min(c)) < 24:
        return "W"                                  # 白＝血条
    if g > 148 and b > 148 and r < g - 12 and abs(g - b) < 30:
        return "S"                                  # 青绿＝护盾条
    return "."


def _row_runs(px, y, xa, xb, kind, step=2, min_run=2):
    """一行里 kind 色块的个数与总宽（用来认"周期性格纹"这个形状）。"""
    runs = cur = lit = 0
    for x in range(xa, xb, step):
        if _cls(px[x, y]) == kind:
            cur += 1
            lit += 1
        else:
            if cur >= min_run:
                runs += 1
            cur = 0
    if cur >= min_run:
        runs += 1
    return runs, lit * step


def find_bars(img):
    """定位敌人头顶的浮动条。

    🔴 锚是**血条的格纹形状**（一行里 ≥8 个短亮段），不是"两排必须成对"。
    上一版要求白排+青排都各有 ≥20 亮列才认，结果护盾一旦被打掉就凑不满，
    readout 在**最有信号的时刻**返回 None —— 仪器会静默丢掉关键样本，比读错更坏。
    护盾排从血条锚下方 4~18px 去找，掉光了也照实报小数。
    """
    w, h = img.size
    px = img.convert("RGB").load()
    xa, xb = int(w * 0.22), int(w * 0.80)
    step = 2
    best = None
    for y in range(0, int(h * 0.35)):
        runs, lit = _row_runs(px, y, xa, xb, "W", step)
        if runs >= 8 and lit >= 60:
            best = (y, runs, lit)
            break
    if best is None:
        return None
    wy = best[0]
    wend = wy
    for y in range(wy, wy + 16):
        r, l = _row_runs(px, y, xa, xb, "W", step)
        if r >= 8 and l >= 60:
            wend = y
        else:
            break
    # 护盾排：锚下方找第一个有青绿格的行
    sy = None
    for y in range(wend + 3, wend + 20):
        r, l = _row_runs(px, y, xa, xb, "S", step)
        if r >= 2:
            sy = y
            break
    send = sy
    if sy is not None:
        for y in range(sy, sy + 12):
            r, l = _row_runs(px, y, xa, xb, "S", step)
            if r >= 2:
                send = y
            else:
                break
    return {"hp_band": (wy, wend), "sh_band": (sy, send) if sy else None, "w": w}


def pips(img, band, kind, min_w=4):
    """在给定 y 带内按列判亮，返回 (格数, 亮列宽)；带无效返回 (None, None)。

    🔴 无效必须返回 None，不能返回 0——10-23 第一版返回 0，
    于是"基线没读到=0、事后读到=16"被算成 +16 变化，对照组漂移带直接失真。

    格是离散整数，比"亮像素总数"稳得多。第一版用总像素在 1920 缩略图上量，
    噪声 ±22px 比一格还大，任何阈值都判不出来。
    """
    if band is None or band[0] is None:
        return (None, None)
    w, h = img.size
    px = img.convert("RGB").load()
    y0, y1 = band
    xa, xb = int(w * 0.22), int(w * 0.80)
    cols = [any(_cls(px[x, y]) == kind for y in range(y0, y1 + 1))
            for x in range(xa, xb)]
    runs = cur = lit = 0
    for c in cols:
        if c:
            cur += 1
            lit += 1
        else:
            if cur >= min_w:
                runs += 1
            cur = 0
    if cur >= min_w:
        runs += 1
    return (runs, lit)


MIN_WIDTH = 2500         # 只能在原始分辨率量，见下面这段注释


def readout(img):
    """原始分辨率读数。找不到血条锚 → None；护盾锚找不到 → 记 0（盾已空是真实状态，
    不是读取失败，不能当 None 丢掉）。

    🔴 宽度不足 MIN_WIDTH 直接拒读。格纹在缩略图上会并格：同一只满血怪，
    3840 上读 21 格、1920 上读成 1 格——不是精度差一点，是量出来就是假的。
    这个错我今天踩过两次（1280 量像素总数、1920 数格），所以做成硬闸。
    """
    if img.width < MIN_WIDTH:
        raise ValueError("帧宽只有 %d，缩略图上 HUD 格纹会并格；请用 capture.grab() 的原始分辨率"
                         % img.width)
    b = find_bars(img)
    if b is None:
        return None
    hr, hpx = pips(img, b["hp_band"], "W")
    sr, spx = pips(img, b["sh_band"], "S")
    return {"hp_pips": hr, "hp_px": hpx,
            "sh_pips": 0 if sr is None else sr, "sh_px": 0 if spx is None else spx,
            "band_hp": b["hp_band"], "band_sh": b["sh_band"], "w": b["w"]}


def measure(path):
    return readout(Image.open(path)) or {"file": path.split("/")[-1], "found": False}


# ── 角色自己的四条资源条 ─────────────────────────────────────
# BU(红) / BE(蓝) / HP(白) / SH(青绿) 在左下角，也是**分段格**。
# 为什么要量它：不放技能时它们不动，放了技能会扣资源——
# 没有这一路，"按了没反应"和"起手了但没打中"我根本分不清。
def _cls4(c):
    r, g, b = c
    if r > 150 and r > g + 45 and r > b + 45:
        return "R"                                # BU 红
    if b > 150 and b > r + 30 and g > r - 10:
        return "B"                                # BE 蓝
    if min(c) > 206 and (max(c) - min(c)) < 24:
        return "W"                                # HP 白
    if g > 145 and g > r + 18 and abs(g - b) < 34:
        return "G"                                # SH 青绿
    return "."


# 自身四条资源条是**真 HUD，位置固定**，所以按标定好的行段量，不做全图颜色搜索。
# 行段来自 2026-10-23 原始 3840x2160 实测：BU 0.853–0.864h、BE 0.874–0.885h、
# SH 0.922–0.932h，HP 夹在 BE 与 SH 之间。
# ⚠ HP 不能靠颜色满屏找——"6500/6500"这类白字与它同色，第一版满列扫把 W
#   从 y1844 糊到 y2014，整条读数直接废。x 也限到 0.40w 内避开数字。
PLAYER_BANDS = {"BU": (0.848, 0.870, "R"), "BE": (0.870, 0.892, "B"),
                "HP": (0.892, 0.918, "W"), "SH": (0.918, 0.940, "G")}


def player_bars(img, min_w=4):
    """固定行段数格。返回 {BU/BE/HP/SH: 格数}。"""
    w, h = img.size
    px = img.convert("RGB").load()
    xa, xb = int(w * 0.03), int(w * 0.40)
    out = {}
    for name, (f0, f1, kind) in PLAYER_BANDS.items():
        y0, y1 = int(h * f0), int(h * f1)
        cols = [any(_cls4(px[x, y]) == kind for y in range(y0, y1 + 1))
                for x in range(xa, xb, 2)]
        runs = cur = 0
        for c in cols:
            if c:
                cur += 1
            else:
                if cur * 2 >= min_w:
                    runs += 1
                cur = 0
        if cur * 2 >= min_w:
            runs += 1
        out[name] = runs
    return out
