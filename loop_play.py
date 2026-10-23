"""loop_play.py —— 阶段 1：真截图 + 键鼠输入的在机回路

跑法（先 dry-run，只出意图不动键鼠）：
    py -3.12 loop_play.py --goal "走到正前方的敌人身边" --shots 8 --dry-run
真跑：把 --dry-run 去掉。开跑前有 5 秒倒计时，请把剑星窗口点到前台。

这一版的三条硬设计：
1. **进展不由模型说**：每拍记录 claim，下一拍用**引擎位移 + 帧差**两路实测回填，
   A/B 已经证明零反馈时它会编造"已生效"，所以 claim 只当预期，不当结论。
   引擎真值来自 `sb_bridge.py`（UE4SS 只读文件桥），桥停了就自动退回纯像素；
   `--no-engine` 可做 A/B。
2. **输入走操作系统级 SendInput**：不注入 DLL、不碰内存、不碰 UE4SS。
   白名单外的键（ESC/ALT/CTRL/F 键）在代码层就进不来。
3. **成本有闸**：默认花到 ¥0.5 就自己停，不停也会把已花的数报出来。

⚠ 未验的一件事，只有真游戏能答：UE4 用 Raw Input 读鼠标视角时**可能忽略注入的
相对位移**（键盘一般没事）。所以真跑先验 keyboard 侧，look 不通就改用
方向键/QE 转身，别在鼠标上耗时间。
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture          # noqa: E402
import inputs           # noqa: E402
import sb_bridge        # noqa: E402
from brain import Brain  # noqa: E402

PRICE = {"in": 1.0, "in_cached": 0.15, "out": 4.0}      # CNY / 百万 token，2026-10-23 重查


def engine_now():
    """读一次文件桥。桥停了/过期一律当不可用，不拿旧坐标冒充现在。"""
    s = sb_bridge.read()
    if not s or s.get("_stale"):
        return None
    return s


def engine_delta(a, b):
    """两次引擎采样之间的玩家位移与身体转向。

    ⚠ body_yaw 是**身体朝向**，站着转镜头时它不动（2026-10-23 实测：
    look(±400) 后 yaw 一动不动，按 W 才对齐到镜头方向）。所以它只用来判
    "有没有转身/走了多远"，不能当镜头朝向用。
    """
    if not a or not b:
        return None
    pa, pb = a.get("player"), b.get("player")
    if not pa or not pb:
        return None
    moved = math.dist((pa["x"], pa["y"], pa["z"]),
                      (pb["x"], pb["y"], pb["z"]))
    dyaw = ((pb["yaw"] - pa["yaw"] + 540) % 360) - 180
    out = {"moved_cm": round(moved, 1), "dyaw": round(dyaw, 2)}
    # v3 才有 view（镜头朝向）；v2 下为 None，不影响其它字段
    va, vb = a.get("view"), b.get("view")
    if va and vb and "yaw" in va and "yaw" in vb:
        out["dview_yaw"] = round(((vb["yaw"] - va["yaw"] + 540) % 360) - 180, 2)
    return out


def engine_summary(eng):
    """落盘用的精简快照（不存整份 state，避免 runs 里全是重复坐标）。"""
    if not eng:
        return None
    p = eng.get("player") or {}
    comp = eng.get("companion") or {}
    enemy = eng.get("enemy") or {}
    return {"player": {k: p.get(k) for k in ("x", "y", "z", "yaw", "pitch")},
            "view": eng.get("view"),
            "vel": eng.get("vel"),
            "companion_cm": comp.get("dist_cm"),
            "enemy_cm": enemy.get("dist_cm"),
            "near": [{"class": (n.get("class") or "").split(".")[-1],
                      "dist_cm": n.get("dist_cm")} for n in (eng.get("near_pawns") or [])[:3]]}


def cost_of(u):
    return (u["prompt_tokens"] - u["cached_tokens"]) * PRICE["in"] / 1e6 \
        + u["cached_tokens"] * PRICE["in_cached"] / 1e6 \
        + u["completion_tokens"] * PRICE["out"] / 1e6


def execute(intent):
    """把 intent 落到输入；返回 (ok, note)。"""
    typ = (intent or {}).get("type")
    arg = (intent or {}).get("arg")
    if typ == "hold_key":
        try:
            dur = float((intent or {}).get("dur", 0.6))
        except (TypeError, ValueError):
            dur = 0.6
        dur = max(0.1, min(1.5, dur))          # 冲刺 18m/s，不封顶会一步跑出 27 米
        if arg in inputs.COMBOS:
            return inputs.hold_combo(arg, dur), "combo %s %.2fs" % (arg, dur)
        return inputs.hold_key(arg, dur), "hold %s %.2fs" % (arg, dur)
    if typ == "tap_key":
        return inputs.tap_key(arg), "tap %s" % arg
    if typ == "look":
        dx, dy = (arg + [0])[:2] if isinstance(arg, (list, tuple)) else (0, 0)
        return inputs.look_rel(dx, dy), "look %s,%s" % (dx, dy)
    if typ == "click":
        return inputs.click_left(), "click"
    if typ == "wait":
        return True, "wait"
    return False, "unknown intent type %r" % typ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", default="向正前方移动，直到画面里出现可交互对象")
    ap.add_argument("--shots", type=int, default=8)
    ap.add_argument("--interval", type=float, default=3.0, help="两拍间隔秒（含模型耗时）")
    ap.add_argument("--dry-run", action="store_true", help="只出意图，不动键鼠")
    ap.add_argument("--budget", type=float, default=0.5, help="成本上限 CNY，超了就停")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--no-engine", action="store_true",
                    help="关掉引擎真值（A/B 用：只给像素通道）")
    args = ap.parse_args()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs",
                       time.strftime("%Y%m%d_%H%M%S") + "_play")
    os.makedirs(out, exist_ok=True)
    brain = Brain()
    print("目标：%s\n模式：%s  预算 ¥%.2f  归档：%s"
          % (args.goal, "DRY-RUN" if args.dry_run else "真输入", args.budget, out))
    print("5 秒内把《剑星》窗口点到前台……", flush=True)
    time.sleep(5)

    prev, history, rows = None, [], []
    prev_eng = None
    for i in range(args.shots):
        t0 = time.time()
        frame = capture.grab()
        cells, mean = capture.frame_diff(prev, frame) if prev else (1.0, 1.0)
        prev = frame                                      # ← 不赋这行，cells 会恒为 1.0
        eng = None if args.no_engine else engine_now()
        edelta = engine_delta(prev_eng, eng)
        prev_eng = eng
        url = capture.to_data_url(capture.resize_to(frame, args.width))
        intent, err = brain.ask_image(
            url, goal=args.goal, history=history,
            frame_stats={"cells_changed": "%.3f" % cells, "mean_delta": mean,
                         "prev_cells": ("%.3f" % history[-1]["measured_cells"])
                         if history else "None"},
            allowed_keys=inputs.MODEL_ACTIONS,
            engine=eng, engine_delta=edelta)
        it = (intent or {}).get("intent")
        ok, note = (False, "no-intent") if it is None else execute(it)
        if args.dry_run:
            ok, note = True, "dry-run(未执行) " + note

        rec = {"shot": i, "ts": time.time(), "frame_cells": round(cells, 4),
               "frame_mean": mean, "intent": it, "claim": (intent or {}).get("claim"),
               "reason": (intent or {}).get("reason"),
               "engine": engine_summary(eng),
               "engine_delta": edelta,
               "executed": bool(ok) and not args.dry_run, "exec_note": note,
               "err": err, "usage_total": dict(brain.usage),
               "cost_cny": round(cost_of(brain.usage), 5)}
        # 回填上一拍的"说了什么 vs 实际动了没有"（像素 + 引擎两路）
        if history:
            history[-1]["verified"] = {
                "claimed_change": bool(history[-1]["claim"]),
                "measured_cells_this_shot": round(cells, 4),
                "measured_moved_cm": (edelta or {}).get("moved_cm"),
                "measured_dyaw": (edelta or {}).get("dyaw")}
        history.append({"round_id": i, "intent": it, "claim": rec["claim"],
                        "measured_cells": round(cells, 4), "measured_mean": mean,
                        "measured_moved_cm": (edelta or {}).get("moved_cm"),
                        "measured_dyaw": (edelta or {}).get("dyaw")})
        rows.append(rec)
        with open(os.path.join(out, "rounds.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        eng_txt = ("  moved=%scm" % edelta["moved_cm"]) if edelta else ""
        print("s%-2d cells=%.3f%s %-10s %-8s claim=%s  ¥%.5f"
              % (i, cells, eng_txt, json.dumps(it, ensure_ascii=False)[:26] if it else "-",
                 ("OK" if ok else "FAIL"), (rec["claim"] or "-")[:38],
                 rec["cost_cny"]), flush=True)

        if rec["cost_cny"] > args.budget:
            print("到预算上限，自己停。已花 ¥%.5f" % rec["cost_cny"])
            break
        time.sleep(max(0.0, args.interval - (time.time() - t0)))

    print("\n共 %d 拍，累计 ¥%.5f，归档 %s"
          % (len(rows), cost_of(brain.usage), out))
    print("提醒：模型自述不参与判定——'做没做到'只看引擎位移与像素帧差。"
          "引擎桥过期时本回路只剩像素通道（会明确标出 engine=null）。")


if __name__ == "__main__":
    main()
