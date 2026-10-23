"""fight_v2.py —— 连段优先 + 闪光闪避 + 技能轮询

用户 10-24 三条（原话要点）：
  1. 闪避和格挡会打断 Eve 自己的攻击动作 → **不能连续不停放**，只在需要时用；
  2. 普通攻击用格挡兜 → 格挡放在连段收尾，不插在连段中间；
  3. **技能收益更大**，之前全程没放 → 按能量到点就按。

实现取舍：
  · 攻击：5 连打，中间不做任何防守，保证动作打完（这是输出基本盘）；
  · 闪避：只在闪光时（阈值放低，宁可早闪），闪完立刻回到连段；
  · 格挡：只在连段收尾补 0.14s，不每刀都按；
  · 技能：每 8 秒轮一个 1~4；每 20 秒补一次 Alt+4。
    能量不够时按键无副作用（满血开局实测 1~4/Alt+4 全无反应），所以轮询安全。

硬约束：不碰光标；游戏失去焦点立刻退出。
"""
import argparse
import ctypes
import os
import time

import numpy as np
from PIL import ImageGrab

import inputs
import sb_bridge

HERE = os.path.dirname(os.path.abspath(__file__))
user32 = ctypes.windll.user32

BOX = (0.22, 0.18, 0.78, 0.86)
V_MIN, S_MIN = 205, 80
SMALL = (480, 270)

_probe = ImageGrab.grab()
_W, _H = _probe.size
BBOX = (int(_W * BOX[0]), int(_H * BOX[1]), int(_W * BOX[2]), int(_H * BOX[3]))


def grab_center():
    return ImageGrab.grab(bbox=BBOX)


def flash_score(img):
    a = np.asarray(img.resize(SMALL).convert("HSV"))
    m = (a[..., 2] >= V_MIN) & (a[..., 1] >= S_MIN)
    return int(m.sum())


def fg(hwnd):
    return user32.GetForegroundWindow() == hwnd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--close-m", type=float, default=3.2)
    ap.add_argument("--combo", type=int, default=5)
    ap.add_argument("--skill-every", type=float, default=8.0)
    ap.add_argument("--burst-every", type=float, default=20.0)
    ap.add_argument("--k", type=float, default=1.8, help="闪光阈值倍数（放低=更早闪）")
    ap.add_argument("--min-px", type=int, default=500)
    ap.add_argument("--dodge-cool", type=float, default=0.7)
    ap.add_argument("--potion-every", type=float, default=40.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    outdir = os.path.join(HERE, "runs", "v2")
    os.makedirs(outdir, exist_ok=True)
    ok, hwnd = inputs.focus_game()
    if not ok:
        print("游戏不在前台 → 不启动", flush=True)
        return
    time.sleep(0.3)

    t0 = time.time()
    base = 400.0
    last_dodge = -9e9
    next_skill = 6.0
    next_burst = a.burst_every
    next_potion = a.potion_every
    skill_idx = 0
    attacks = dodges = guards = approaches = skills = 0
    last_log = -1

    while time.time() - t0 < a.seconds:
        t = time.time() - t0
        if not fg(hwnd):
            print("%.1fs 游戏失去焦点 → 停手退出" % t, flush=True)
            return

        score = flash_score(grab_center())
        base = base * 0.90 + score * 0.10
        if score >= a.min_px and score >= a.k * max(base, 1.0) and (t - last_dodge) > a.dodge_cool:
            last_dodge = t
            dodges += 1
            if not a.dry_run:
                inputs.tap_key("dodge")
            grab_center().save(os.path.join(outdir, "flash_%s_%05d.png" % (a.tag, int(t * 1000))))
            print("%6.2fs 闪光(%d) → 闪避" % (t, score), flush=True)
            continue

        st = sb_bridge.read(0.6) or {}
        e = st.get("enemy") or {}
        d = e.get("dist_cm", 9e9) / 100.0

        # 技能：到点就按，能量不够自然没反应
        if t >= next_skill:
            next_skill += a.skill_every
            key = "key_%d" % (skill_idx % 4 + 1)
            skill_idx += 1
            skills += 1
            if not a.dry_run:
                inputs.tap_key(key)
            print("%6.2fs 技能 %s（第 %d 次轮询）" % (t, key, skills), flush=True)

        if t >= next_burst:
            next_burst += a.burst_every
            if not a.dry_run:
                inputs.burst(4)
            print("%6.2fs 爆发 Alt+4" % t, flush=True)

        if e and d <= a.close_m:
            for _ in range(a.combo):
                if not a.dry_run:
                    inputs.click_left()
                attacks += 1
                time.sleep(0.16)
                score2 = flash_score(grab_center())
                if score2 >= a.min_px and score2 >= a.k * max(base, 1.0) and (t - last_dodge) > a.dodge_cool - 0.2:
                    break
            if not a.dry_run:
                inputs.hold_key("guard", 0.14)   # 只在连段收尾补一下
            guards += 1
        elif e:
            approaches += 1
            if not a.dry_run:
                inputs.hold_key("fwd", 0.30)
        else:
            time.sleep(0.2)

        if t >= next_potion:
            next_potion += a.potion_every
            if not a.dry_run:
                inputs.tap_key("potion")
                print("%6.2fs 喝药" % t, flush=True)

        if int(t) // 20 != last_log:
            last_log = int(t) // 20
            print("%6.2fs d=%.1f 打=%d 闪=%d 挡=%d 接近=%d 技能=%d 基线=%.0f"
                  % (t, d, attacks, dodges, guards, approaches, skills, base), flush=True)

    print("结束：打=%d 闪=%d 挡=%d 接近=%d 技能=%d" % (attacks, dodges, guards, approaches, skills), flush=True)


if __name__ == "__main__":
    main()
