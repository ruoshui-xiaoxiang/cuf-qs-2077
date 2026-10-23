"""fight_skill.py —— 技能压制流

用户 10-24 决定：不管战斗 mod，做成**技能压制**——技能收益最大，
能量满了就放，近战只用来攒能量和保命（连段不打完不打断）。

能量读数：hud_energy.py（左下 HUD 分段条，满格≈0.78，空≈0.02）
  · BE 满（>0.62）→ 轮询 1~4，并观察 BE 是否下降来判断哪个键是真技能
  · BU 满（>0.62）→ Alt+4（爆发）
  · 都不是 → 连段攒能量

安全：不碰光标；失去焦点立刻退出。
"""
import argparse
import ctypes
import os
import time

import numpy as np
from PIL import ImageGrab

import hud_energy
import inputs
import sb_bridge

HERE = os.path.dirname(os.path.abspath(__file__))
user32 = ctypes.windll.user32

ENTRY = (36390.0, -50900.0)
FULL = 0.62          # 满格 0.787、空 0.02，取 0.62 当“够放”
LOW = 0.28           # HP 低于此比例喝药

_box = ImageGrab.grab()
_W, _H = _box.size
HUD_BBOX = (0, int(_H * 0.77), int(_W * 0.30), _H)
HUD_OY = HUD_BBOX[1]
FLASH_BBOX = (int(_W * 0.22), int(_H * 0.18), int(_W * 0.78), int(_H * 0.86))
SMALL = (480, 270)
V_MIN, S_MIN = 205, 80


def grab_hud():
    return ImageGrab.grab(bbox=HUD_BBOX)


def grab_flash():
    return ImageGrab.grab(bbox=FLASH_BBOX)


def flash_px(img):
    a = np.asarray(img.resize(SMALL).convert("HSV"))
    return int(((a[..., 2] >= V_MIN) & (a[..., 1] >= S_MIN)).sum())


def fg(hwnd):
    return user32.GetForegroundWindow() == hwnd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--tag", default="k1")
    ap.add_argument("--close-m", type=float, default=3.2)
    ap.add_argument("--combo", type=int, default=4)
    ap.add_argument("--k", type=float, default=2.0)
    ap.add_argument("--min-px", type=int, default=600)
    ap.add_argument("--dodge-cool", type=float, default=0.8)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    os.makedirs(os.path.join(HERE, "runs", "skill"), exist_ok=True)
    ok, hwnd = inputs.focus_game()
    if not ok:
        print("游戏不在前台 → 不启动", flush=True)
        return
    time.sleep(0.3)

    t0 = time.time()
    base = 400.0
    last_dodge = -9e9
    key_i = 0
    known_keys = {}
    casts = 0
    combos = 0
    dodges = 0
    potions = 0
    last_potion = -9e9
    last_log = -1
    next_sample = 0.0
    zero_streak = 0
    revives = 0

    while time.time() - t0 < a.seconds:
        t = time.time() - t0
        if not fg(hwnd):
            print("%.1fs 游戏失去焦点 → 停手退出" % t, flush=True)
            return

        # ── 闪光 → 闪避 ──
        fscore = flash_px(grab_flash())
        base = base * 0.90 + fscore * 0.10
        if fscore >= a.min_px and fscore >= a.k * max(base, 1.0) and (t - last_dodge) > a.dodge_cool:
            last_dodge = t
            dodges += 1
            if not a.dry_run:
                inputs.tap_key("dodge")
            print("%6.2fs 闪光(%d) → 闪避" % (t, fscore), flush=True)
            continue

        r = hud_energy.ratios(grab_hud(), oy=HUD_OY)
        st = sb_bridge.read(0.6) or {}
        e = st.get("enemy") or {}
        d = e.get("dist_cm", 9e9) / 100.0

        # ── 阵亡：回到入口且敌人很远 → R 复活 → 上楼 ──
        pos = st.get("player") or {}
        if pos and d > 30 and ((pos.get("x", 0) - ENTRY[0]) ** 2 + (pos.get("y", 0) - ENTRY[1]) ** 2) < 300 ** 2:
            revives += 1
            print("%6.2fs 判定阵亡（在入口、敌人 %.0fm）→ R 复活" % (t, d), flush=True)
            if not a.dry_run:
                inputs._tap_vk(0x52)
                time.sleep(8.0)
                inputs.hold_key("fwd", 3.2)
            continue

        # ── 技能优先 ──
        if r["BE"] > FULL:
            key = "key_%d" % (key_i % 4 + 1)
            key_i += 1
            casts += 1
            before = r["BE"]
            if not a.dry_run:
                inputs.tap_key(key)
                time.sleep(0.9)
                after = hud_energy.ratios(grab_hud(), oy=HUD_OY)["BE"]
                if after < before - 0.12:
                    known_keys[key] = round(before - after, 3)
                    print("%6.2fs ★ %s 消耗 BE %.3f→%.3f → 这是真技能" % (t, key, before, after), flush=True)
                else:
                    print("%6.2fs %s 放了但 BE 没掉（%.3f）" % (t, key, after), flush=True)
            print("%6.2fs 技能键=%s BE=%.2f BU=%.2f HP=%.2f"
                  % (t, key, r["BE"], r["BU"], r["HP"]), flush=True)
            continue

        if r["BU"] > FULL:
            if not a.dry_run:
                inputs.burst(4)
                time.sleep(0.8)
            print("%6.2fs 爆发 Alt+4（BU=%.2f）" % (t, r["BU"]), flush=True)
            continue

        # ── 血低喝药 ──（读数全零=解析失败，直接跳过）
        if max(r.values()) <= 0.001:
            zero_streak += 1
            if zero_streak == 3:
                # 后台暂停时 HUD 整块不画，按键只能解除暂停
                print("%6.2fs HUD 全零×3 → 按 Space 解除暂停" % t, flush=True)
                if not a.dry_run:
                    inputs.tap_key("jump")
            r = {"BU": 0.0, "BE": 0.0, "HP": 1.0, "SH": 0.0}
        else:
            zero_streak = 0
        if r["HP"] < LOW and t > 8 and potions < 2 and (t - last_potion) > 20:
            potions += 1
            last_potion = t
            if not a.dry_run:
                inputs.tap_key("potion")
            print("%6.2fs 血低 HP=%.2f → 喝药(#%d)" % (t, r["HP"], potions), flush=True)
            time.sleep(0.6)
            continue

        # ── 否则攒能量：连段 + 接近 ──
        if e and d <= a.close_m:
            combos += 1
            for _ in range(a.combo):
                if not a.dry_run:
                    inputs.click_left()
                time.sleep(0.16)
        elif e:
            if not a.dry_run:
                inputs.hold_key("fwd", 0.30)
        else:
            time.sleep(0.2)

        if t >= next_sample:
            next_sample = t + 4.0
            print("%6.2fs BE=%.2f BU=%.2f HP=%.2f SH=%.2f d=%.1f 连段=%d 闪=%d"
                  % (t, r["BE"], r["BU"], r["HP"], r["SH"], d, combos, dodges), flush=True)

    print("结束：连段=%d 闪=%d 技能=%d 药=%d 已知技能键=%s"
          % (combos, dodges, casts, potions, known_keys), flush=True)


if __name__ == "__main__":
    main()
