"""reflex.py —— 受击反射：HP/护盾下降 → 闪避 → 转向最近敌人 → 左键连击

触发源是 HUD 的 UMG ProgressBar（hud_live.probe_once），不是像素：
  · 玩家 HP 百分比下降，或
  · 可见护盾条数量减少 / 任一护盾百分比下降
就算触发；10m 内有 CH_M_* 敌人才出手（避免摔伤/环境伤害误触发）。

动作序列（2026-10-23 口径）：
  1. 立刻 Q 闪避（脱离硬直）
  2. 用 bridge 的 view yaw + 敌人坐标算方位，0.0933°/单位转向
  3. 左键 3 连（≈0.18s 间隔）
  4. 记录 before/after（玩家 HP/盾 + 敌人 HP/盾），冷却 2s，每场最多 3 次

用法：
    py -3.12 reflex.py --seconds 60 --dry-run   # 只报触发，不动键鼠
    py -3.12 reflex.py --seconds 120 --act      # 真反击
"""
import argparse
import json
import math
import os
import time

import sb_bridge
import inputs
import hud_live

HERE = os.path.dirname(os.path.abspath(__file__))
DEG_PER_UNIT = 0.0933
HP_EPS = 0.0005
SHIELD_EPS = 0.01
NEAR_CM = 1000.0          # 只有 10m 内有敌人才反击
COOLDOWN_S = 2.0
MAX_ACTIONS = 3


def state():
    s = sb_bridge.read(2)
    return s if s and not s.get("_stale") else None


def hp_dropped(prev, cur):
    if not prev or not cur or cur.get("player_hp") is None:
        return None
    if prev.get("player_hp") is None:
        return "baseline"
    if cur["player_hp"] < prev["player_hp"] - HP_EPS:
        return "hp %.3f→%.3f" % (prev["player_hp"], cur["player_hp"])
    ps, cs = prev.get("player_shield") or [], cur.get("player_shield") or []
    if len(cs) < len(ps):
        return "shield layers %d→%d" % (len(ps), len(cs))
    if cs and ps and any(c < p - SHIELD_EPS for p, c in zip(ps, cs)):
        return "shield %s→%s" % (ps, cs)
    return None


def nearest_enemy():
    s = state()
    if not s:
        return None
    e = s.get("enemy") or {}
    if not e.get("x") and e.get("x") != 0:
        return None
    return e, s


def turn_to(target_yaw, budget_steps=6):
    for _ in range(budget_steps):
        s = state()
        if not s or "view" not in s:
            return False
        cur = s["view"]["yaw"]
        d = ((target_yaw - cur + 540) % 360) - 180
        if abs(d) < 3:
            return True
        inputs.look_rel(max(-400, min(400, d / DEG_PER_UNIT)), 0)
        time.sleep(0.08)
    return True


def act(reason, dist_cm):
    """执行闪避→转向→三连。返回 (ok, detail)。"""
    before = hud_live.probe_once()
    e, s = nearest_enemy() or ({}, None)
    inputs.tap_key("dodge")                    # Q
    time.sleep(0.26)
    if e:
        p = s["player"]
        bearing = math.degrees(math.atan2(e["y"] - p["y"], e["x"] - p["x"]))
        turn_to(bearing)
    ok = True
    for i in range(3):
        ok = inputs.click_left() and ok
        time.sleep(0.18)
    time.sleep(0.55)
    after = hud_live.probe_once()
    detail = {"t": round(time.time(), 3), "reason": reason,
              "dist_cm": round(dist_cm, 1) if dist_cm else None,
              "before": before, "after": after, "ok": ok}
    return ok, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--dry-run", action="store_true", help="只报触发，不动键鼠")
    ap.add_argument("--act", action="store_true", help="真执行闪避+连击")
    a = ap.parse_args()
    if not a.act:
        a.dry_run = True
    logp = os.path.join(HERE, "runs", "reflex_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    prev = hud_live.probe_once()
    print("基线", hud_live.fmt(prev), flush=True)
    actions = 0
    last_act = 0.0
    t0 = time.time()
    with open(logp, "a", encoding="utf-8") as f:
        while time.time() - t0 < a.seconds:
            cur = hud_live.probe_once()
            if cur is None:
                time.sleep(0.1)
                continue
            if cur.get("player_hp") is not None and cur["player_hp"] <= 0.0001:
                print("玩家已阵亡，停止反射", flush=True)
                break
            reason = hp_dropped(prev, cur)
            now = time.time()
            if reason and reason != "baseline" and now - last_act >= COOLDOWN_S \
                    and actions < MAX_ACTIONS:
                e, s = nearest_enemy() or ({}, None)
                dist = None
                if e and s:
                    p = s["player"]
                    dist = math.dist((p["x"], p["y"], p["z"]),
                                     (e["x"], e["y"], e["z"]))
                print("TRIGGER %s  dist=%s" % (reason, round(dist, 0) if dist else "-"), flush=True)
                if dist is not None and dist <= NEAR_CM:
                    if a.dry_run:
                        rec = {"t": round(now, 3), "reason": reason,
                               "dist_cm": dist, "dry_run": True, "cur": cur}
                        print("  dry-run：不动键鼠", flush=True)
                    else:
                        ok, rec = act(reason, dist)
                        print("  acted ok=%s -> %s" % (ok, hud_live.fmt(rec["after"])), flush=True)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    actions += 1
                    last_act = now
                else:
                    print("  10m 内没有敌人，不反击（防摔伤误触）", flush=True)
            prev = cur
            time.sleep(0.05)
    print("结束：触发执行 %d 次，日志 %s" % (actions, logp))


if __name__ == "__main__":
    main()
