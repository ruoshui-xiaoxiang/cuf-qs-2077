"""step.py —— 单步执行器：agent（我）当决策核时用的手和眼

用法：
    py -3.12 step.py look 200 0            # 转视角
    py -3.12 step.py hold fwd 0.8          # 按住走
    py -3.12 step.py tap interact          # 点一下 E
    py -3.12 step.py click                 # 左键
    py -3.12 step.py wait                  # 只抓帧不动

每步都做同一件事：动作前抓帧 → 执行输入 → 等 settle → 动作后抓帧 → 算帧差，
把 (动作, cells, mean, 存盘帧) 追加进 runs/agent_log.jsonl。
帧差是唯一的过程证据——**我觉得自己做了什么，不算数**。

--peek 会把动作后的帧存成 png，方便我直接看。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture          # noqa: E402
import inputs           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "runs", "agent_log.jsonl")

# 判据来自 2026-10-23 实测地板（剑星静置 5 次采样：cells 0.111–0.153，
# mean 1.71–2.53；画面有沙尘/发丝/HUD 动效，永远不会是 0）。
# 取地板最大值 + 余量，低于它一律判"这一拍没改变画面"。
FLOOR_CELLS, FLOOR_MEAN = 0.25, 6.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["look", "hold", "tap", "click", "wait", "reset"])
    ap.add_argument("args", nargs="*", default=[])
    ap.add_argument("--settle", type=float, default=0.9, help="动作后等多久再抓帧")
    ap.add_argument("--peek", action="store_true", help="存动作后的 png 供目视")
    ap.add_argument("--note", default="", help="我这一步想验证什么")
    a = ap.parse_args()

    before = capture.grab()
    t0 = time.time()

    if a.action == "look":
        dx = int(a.args[0]) if a.args else 200
        dy = int(a.args[1]) if len(a.args) > 1 else 0
        ok, desc = inputs.look_rel(dx, dy), "look(%d,%d)" % (dx, dy)
    elif a.action == "hold":
        key = a.args[0] if a.args else "fwd"
        dur = float(a.args[1]) if len(a.args) > 1 else 0.8
        ok, desc = inputs.hold_key(key, dur), "hold_key(%s,%.1fs)" % (key, dur)
    elif a.action == "tap":
        key = a.args[0] if a.args else "interact"
        ok, desc = inputs.tap_key(key), "tap_key(%s)" % key
    elif a.action == "click":
        ok, desc = inputs.click_left(), "click_left()"
    elif a.action == "reset":
        ok, desc = inputs.reset_training(), "TAB+1 reset"
    else:
        ok, desc = True, "wait"

    time.sleep(max(0.05, a.settle - (time.time() - t0)))
    after = capture.grab()
    cells, mean = capture.frame_diff(before, after)

    rec = {"ts": time.time(), "action": desc, "ok": bool(ok),
           "cells": round(cells, 4), "mean": mean, "note": a.note,
           "floor": [FLOOR_CELLS, FLOOR_MEAN],
           "moved": cells > FLOOR_CELLS or mean > FLOOR_MEAN}
    if a.peek:
        p = os.path.join(HERE, "runs", "last_%d.png" % int(a.ts if False else time.time()))
        capture.resize_to(after, 1280).save(p)
        rec["peek"] = os.path.basename(p)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("%-22s ok=%-5s cells=%.4f mean=%-6s %s" % (
        desc, ok, cells, mean,
        "→ 过地板，判为真改变画面" if rec["moved"] else "→ 未过地板，判为没动"))
    if a.peek:
        print("   帧存于 runs/%s" % rec.get("peek"))


if __name__ == "__main__":
    main()
