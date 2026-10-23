"""fight_simple.py —— 简版：闪光就闪避，其余时间攻击/格挡

用户 10-24 口径（原话）：
  「不必那么严谨，直接按闪光就闪避，普通攻击格挡就行，
    另外伊芙攻击敌人，敌人是有僵直的」
⇒ 不去分蓝/紫/黄/红，只看**有没有强闪光**；有就闪，没有就压着打。
⇒ 攻击能打出敌人僵直，所以默认进攻，格挡只用来兜住普通攻击的间隙。

两条硬约束（上轮教训）：
  1. **不碰光标**：不再调用 look_yaw / SetCursorPos，绝不抢鼠标。
  2. **游戏不在前台就退出**：键盘事件只送前台窗口，抢焦点时按下去就是给别人打字。

性能：只抓中央区域并缩到 480x270，判闪光 <100ms；每打一下之前都判一次。
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

# 中央判定区（比例）：避开 HUD 与屏幕边缘
BOX = (0.22, 0.18, 0.78, 0.86)
V_MIN, S_MIN = 205, 80          # 亮且带色 = 闪光；纯灰白高光也算（V 高）
SMALL = (480, 270)

# 只截中央区：整屏 BitBlt 约 60~90ms，裁完只要 20~30ms，判读频率直接翻倍
_probe = ImageGrab.grab()
_W, _H = _probe.size
BBOX = (int(_W * BOX[0]), int(_H * BOX[1]), int(_W * BOX[2]), int(_H * BOX[3]))


def grab_center():
    return ImageGrab.grab(bbox=BBOX)


def flash_score(img):
    """返回 (亮饱和像素数, 总数)。中央区缩到 480x270 再数。"""
    a = np.asarray(img.resize(SMALL).convert("HSV"))
    m = (a[..., 2] >= V_MIN) & (a[..., 1] >= S_MIN)
    return int(m.sum()), SMALL[0] * SMALL[1]


def foreground_is_game(hwnd):
    return user32.GetForegroundWindow() == hwnd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--tag", default="s1")
    ap.add_argument("--close-m", type=float, default=3.6)
    ap.add_argument("--attack-n", type=int, default=3)
    ap.add_argument("--guard-s", type=float, default=0.18)
    ap.add_argument("--k", type=float, default=2.8, help="超基线多少倍算闪光")
    ap.add_argument("--min-px", type=int, default=900)
    ap.add_argument("--dodge-cool", type=float, default=0.45)
    ap.add_argument("--potion-every", type=float, default=40.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    outdir = os.path.join(HERE, "runs", "simple")
    os.makedirs(outdir, exist_ok=True)
    ok, hwnd = inputs.focus_game()
    if not ok:
        print("游戏窗口不在前台 → 不启动（避免抢焦点时乱按键）", flush=True)
        return
    time.sleep(0.3)
    t0 = time.time()
    base = 400.0
    last_dodge = -9e9
    next_potion = a.potion_every
    dodges = attacks = guards = approaches = 0
    last_log = -1

    while time.time() - t0 < a.seconds:
        t = time.time() - t0
        if not foreground_is_game(hwnd):
            print("%.1fs 游戏失去焦点 → 立刻停手退出" % t, flush=True)
            return

        img = grab_center()
        score, total = flash_score(img)
        base = base * 0.90 + score * 0.10
        flash = (score >= a.min_px and score >= a.k * max(base, 1.0))

        if flash and (t - last_dodge) > a.dodge_cool:
            last_dodge = t
            dodges += 1
            if not a.dry_run:
                inputs.tap_key("dodge")
            img.save(
                os.path.join(outdir, "flash_%s_%05d.png" % (a.tag, int(t * 1000))))
            print("%6.2fs 闪光(%d) → 闪避" % (t, score), flush=True)
            continue

        st = sb_bridge.read(0.6) or {}
        e = st.get("enemy") or {}
        d = e.get("dist_cm", 9e9) / 100.0

        if e and d <= a.close_m:
            for _ in range(a.attack_n):
                if not a.dry_run:
                    inputs.click_left()
                attacks += 1
                time.sleep(0.14)
                # 连击中间也判一次闪光：别等三下打完才闪
                s2, _ = flash_score(grab_center())
                if s2 >= a.min_px and s2 >= a.k * max(base, 1.0):
                    break
            if not a.dry_run:
                inputs.hold_key("guard", a.guard_s)
            guards += 1
        elif e:
            approaches += 1
            if not a.dry_run:
                inputs.hold_key("fwd", 0.28)
        else:
            time.sleep(0.2)

        if t >= next_potion:
            next_potion += a.potion_every
            if not a.dry_run:
                inputs.tap_key("potion")
                print("%6.2fs 喝药" % t, flush=True)

        if int(t) // 20 != last_log:
            last_log = int(t) // 20
            print("%6.2fs d=%.1f 打=%d 闪=%d 挡=%d 接近=%d 闪光基线=%.0f"
                  % (t, d, attacks, dodges, guards, approaches, base), flush=True)

    print("结束：%d 秒，打=%d 闪=%d 挡=%d 接近=%d"
          % (time.time() - t0, attacks, dodges, guards, approaches), flush=True)


if __name__ == "__main__":
    main()
