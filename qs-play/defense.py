"""defense.py —— 带预警色判读的攻防循环（v0.1）

用户口径（10-24）：
  攻击/技能 = 输出；格挡/闪避 = 减伤。要**分清楚什么时候做什么**，
  不能一直连打顺手乱按 E。

公开资料（多来源一致）的防御分工：
  蓝光  → 前+闪避（闪现，绕到身后）
  紫光  → 后+闪避（击退，暴露弱点）
  黄光  → 只能完美闪避（格挡无效）
  红光/白光 → 完美格挡（精准格挡，削架势）
  其它时候 = 攻击窗口（敌人没在起手）

规则等级（诚实标注）：
  · 「颜色→防御」有公开资料支撑，属于**规则**；
  · 颜色阈值与冷却时长是**候选参数**，还没标定，先跑先记。
  · 判读延迟：整圈 ~150ms（抓屏+HSV），比人眼慢，先用宽松冷却。

输出：runs/defense/<tag>.csv + 电光存帧，供事后逐帧人眼复核。
"""
import argparse
import csv
import os
import time

import numpy as np

import capture
import inputs
import sb_bridge
from telegraph import counts_of, RANGES

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--tag", default="rd1")
    ap.add_argument("--close-m", type=float, default=3.6, help="进这个距离才攻击")
    ap.add_argument("--min-px", type=int, default=700)
    ap.add_argument("--k", type=float, default=2.6)
    ap.add_argument("--cool", type=float, default=0.55, help="两次判读防御的最小间隔")
    ap.add_argument("--attack-n", type=int, default=2)
    ap.add_argument("--potion-every", type=float, default=45.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    outdir = os.path.join(HERE, "runs", "defense")
    os.makedirs(outdir, exist_ok=True)
    csv_path = os.path.join(outdir, "%s.csv" % a.tag)
    f = open(csv_path, "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["t", "red", "yellow", "blue", "purple", "dist_m", "action"])

    inputs.focus_game()
    time.sleep(0.4)
    base = {k: 1.0 for k in RANGES}
    last_spike = {k: -9e9 for k in RANGES}
    last_def = -9e9
    t0 = time.time()
    next_potion = a.potion_every
    acts = {}
    frames = 0
    enter = None          # 复活落点（第一次看到敌人时记下来）
    was_fighting = False

    while time.time() - t0 < a.seconds:
        t = time.time() - t0
        img = capture.grab()
        c = counts_of(img)
        st = sb_bridge.read(0.6) or {}
        e = st.get("enemy") or {}
        p = st.get("player") or {}
        d = e.get("dist_cm", 9e9) / 100.0
        action = ""

        # ── 1) 预警色：最高优先级，判到就防 ──
        hit = None
        for k in RANGES:
            if c[k] > base[k]:
                base[k] = base[k] * 0.94 + c[k] * 0.06
            else:
                base[k] = base[k] * 0.90 + c[k] * 0.10
            if (c[k] >= a.min_px and c[k] >= a.k * max(base[k], 1.0)
                    and t - last_spike[k] > 0.35):
                last_spike[k] = t
                hit = k if hit is None else hit

        if hit and (t - last_def) > a.cool:
            last_def = t
            action = "def_" + hit
            if not a.dry_run:
                if hit == "blue":
                    inputs.hold_combo("sprint_fwd", 0.12)    # 前闪 = 闪现
                elif hit == "purple":
                    inputs.hold_combo("sprint_back", 0.12)   # 后闪 = 击退
                elif hit == "yellow":
                    inputs.tap_key("dodge")                  # 只能完美闪避
                else:
                    inputs.hold_key("guard", 0.28)           # 红/白 = 完美格挡
            img.resize((1280, 720)).save(
                os.path.join(outdir, "hit_%s_%05d_%s.png" % (a.tag, int(t * 1000), hit)))
            acts[action] = acts.get(action, 0) + 1
            log("%6.2fs %-11s d=%.1f" % (t, action, d))

        # ── 2) 没预警：按距离决定接近 / 攻击 ──
        elif e and d <= a.close_m:
            was_fighting = True
            action = "attack"
            if not a.dry_run:
                for _ in range(a.attack_n):
                    inputs.click_left()
                    time.sleep(0.16)
                inputs.hold_key("guard", 0.22)   # 连打完补一拍盾，别整段裸奔
            acts["attack"] = acts.get("attack", 0) + 1
        elif e and d > a.close_m:
            action = "approach"
            if not a.dry_run:
                inputs.hold_key("fwd", 0.30)
            acts["approach"] = acts.get("approach", 0) + 1
        else:
            action = "no_enemy"
            if enter is None and p:
                enter = (p.get("x"), p.get("y"))
            time.sleep(0.25)

        if t >= next_potion:
            next_potion += a.potion_every
            if not a.dry_run:
                inputs.tap_key("potion")
                log("%6.2fs 喝药" % t)

        w.writerow([round(t, 3), c["red"], c["yellow"], c["blue"], c["purple"],
                    round(d, 2) if e else "", action])
        frames += 1

    f.close()
    log("结束：%d 帧 / %.1f fps；动作分布 %s；CSV=%s"
        % (frames, frames / max(time.time() - t0, 1e-6), acts, csv_path))


if __name__ == "__main__":
    main()
