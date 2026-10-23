"""probe_reach.py —— 用护盾条当裁判，量"走多久才砍得着"

做法：重置 → 测基线 → 每轮【砍一刀 → 立刻量盾/血 → 没掉就再走 0.3 秒】。
产物是可复用的控制常数：从出生点走 N 个 0.3 秒，攻击才开始生效。

为什么量护盾不是血量：用户 2026-10-23 指正——打一下只掉护盾，不掉血。
所以盾是敏感信号，血要等盾破。settle 取 0.45s，赶在护盾回满之前读。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture          # noqa: E402
import hud              # noqa: E402
import inputs           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STEP_WALK = 0.3          # 每轮再逼近 0.3 秒
TRIALS = 12
DROP = 1                # 掉 1 格即算命中（原始分辨率下格是整数，极差实测 0）


def shot(name):
    """原始分辨率抓帧（缩略图上 HUD 只有 3px 高，量不准）。
    只裁顶部条带存盘，避免 13 张 15MB 全帧占地方。"""
    f = capture.grab()
    r = hud.readout(f)
    p = os.path.join(HERE, "runs", name)
    if r:
        y0, y1 = min(r["band_hp"][0], r["band_sh"][0] or 0), max(r["band_hp"][1], r["band_sh"][1] or 0)
        f.crop((int(f.width*0.20), max(0, y0-8), int(f.width*0.85), y1+12)).save(p)
    return f, r


def main():
    ok, _ = inputs.reset_training()
    print("重置 ok=%s，等敌人就位 4s" % ok)
    time.sleep(4.0)
    _, base = shot("reach_base.png")
    if not base:
        print("读不到浮动条，先确认在训练场且敌人在画面里"); return
    print("基线：盾 %d 格 / 血 %d 格" % (base["sh_pips"], base["hp_pips"]))

    log = [{"trial": 0, "walked_s": 0.0, "sh_px": base["sh_px"],
            "hp_px": base["hp_px"], "hit": False, "frame": "reach_base.png"}]
    walked = 0.0
    hit_at = None
    for t in range(1, TRIALS + 1):
        inputs.click_left()
        time.sleep(0.45)
        _, m = shot("reach_%02d.png" % t)
        row = {"trial": t, "walked_s": round(walked, 2), "frame": "reach_%02d.png" % t}
        if not m:
            row.update(found=False, hit=False)
            print("  第%2d刀 走了%.1fs → 没读到条" % (t, walked))
        else:
            d = base["sh_pips"] - m["sh_pips"]
            row.update(sh_px=m["sh_px"], hp_px=m["hp_px"],
                       shield_drop=d, hit=d >= DROP)
            print("  第%2d刀 走了%.1fs → 盾 %d格(掉%d) 血 %d格 %s"
                  % (t, walked, m["sh_pips"], d, m["hp_pips"],
                     "★命中" if row["hit"] else ""))
            if row["hit"] and hit_at is None:
                hit_at = round(walked, 2)
        log.append(row)
        if hit_at is not None:
            break
        inputs.hold_key("fwd", STEP_WALK)
        time.sleep(0.35)
        walked += STEP_WALK

    out = {"baseline": {"sh_pips": base["sh_pips"], "hp_pips": base["hp_pips"]},
           "step_walk_s": STEP_WALK, "drop_threshold": DROP,
           "unit": "pips（原始分辨率离散格，非像素）",
           "first_hit_walk_s": hit_at, "trials": log}
    with open(os.path.join(HERE, "runs", "reach_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n结论：从重置点走 %s 秒后攻击才打上护盾（阈值 掉≥%dpx）"
          % (hit_at, DROP))
    print("明细存 runs/reach_result.json")


if __name__ == "__main__":
    main()
