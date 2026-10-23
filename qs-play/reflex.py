"""reflex.py —— 受击反射 v2：一套连招打死，或连续撤退脱战

用户口径（2026-10-23）：
  · 不是回合制：要么用攻击/闪避/格挡/反击一套把敌人打死，要么直接撤退；
  · 撤退要一直移动，直到摆脱追击并离开警戒范围；
  · 技能可自由组合：连续左键、连续右键、左右混合都可以。

触发：HUD 玩家 HP 下降，或可见护盾层数减少/盾值下降（hud_live 的 UMG 读数）。
     10m 内没有 CH_M_* 不触发（防摔伤/环境伤害）。
行为：
  HP > 50%  → ENGAGE：面向敌人，左键×3 → 右键 → 左键×3 → key_4，
             每轮后 Q 闪避并重新读 HUD；敌人血条消失/HP 归零则结束。
  HP ≤ 50%  → RETREAT：背向最近敌人连续冲刺，卡住就侧移/跳；
             直到距离 > 30m 连续两拍，或 18s 上限。
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
NEAR_CM = 1000.0
ENGAGE_HP = 0.50
MAX_ROUNDS = 5
RETREAT_MAX_S = 18.0
SAFE_DIST_CM = 3000.0


def state():
    s = sb_bridge.read(2)
    return s if s and not s.get("_stale") else None


# ── 低血信号：屏幕左上角红晕 ────────────────────────────────────────────
# 2026-10-24 三条读玩家 HP 的路都断了：
#   · UMG：40 根可见 ProgressBar 全是敌方条，玩家血条根本不在这个控件树里；
#   · 像素数格（hud.py）：营地背景下锚定跑到屏幕顶部，读出 band=(81,83) 的假条；
#   · 引擎：玩家 77 个组件里没有任何 Stat/Health/Condition 组件。
# 但受击时画面左上会泛红，实测满血营地 7 帧恒为 17.8，HP 1393/6500 时 79.1，
# 右上角几乎不动（-1.6），所以只用左上块，阈值 40。
RED_X0, RED_Y0, RED_X1, RED_Y1 = 0.0, 0.0, 0.14, 0.18
HURT_RED = 34.0   # 实测 38% 血=36.6 / 21% 血=79.1 / 满血=17.8，取 34≈40% 血


def red_flag(img=None):
    """左上角红度：R 减 G/B 均值。取不到帧返回 None。"""
    try:
        import numpy as np
        if img is None:
            import capture
            img = capture.grab()
        a = np.asarray(img.convert("RGB")).astype(int)
        h, w, _ = a.shape
        b = a[int(h * RED_Y0):int(h * RED_Y1), int(w * RED_X0):int(w * RED_X1)]
        if b.size == 0:
            return None
        return float((b[:, :, 0] - (b[:, :, 1] + b[:, :, 2]) / 2).mean())
    except Exception:
        return None


def is_hurt(threshold=HURT_RED):
    r = red_flag()
    return (r is not None and r >= threshold), r


# ── 阵亡判据（候选，样本只有 1 帧）───────────────────────────────────────
# 实测「阵亡/YOU DIED」帧：中央带 dark_frac=0.388、mean=88.0；
# 正常战斗 0.021、营地 0.051、纯黑过场 0.772。
# ⇒ 用 0.25~0.60 这段当阵亡窗口，能把过场黑屏排除掉。
# ⚠ 这是我自己划的候选阈值，正样本仅 1 帧，默认不据此自动按键；
#   要真按 R 复活必须显式加 --revive，并连续两帧命中。
DEAD_LO, DEAD_HI = 0.25, 0.60


def dead_frac():
    try:
        import numpy as np
        import capture
        a = np.asarray(capture.grab().convert("L")).astype(int)
        h, w = a.shape
        mid = a[int(h * 0.30):int(h * 0.70), int(w * 0.10):int(w * 0.90)]
        return float((mid < 25).mean())
    except Exception:
        return None


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


def nearest():
    s = state()
    if not s:
        return None, None, None
    e = s.get("enemy") or {}
    if not e or ("x" not in e and "dist_cm" not in e):
        return None, s, None
    p = s["player"]
    d = math.dist((p["x"], p["y"], p["z"]), (e["x"], e["y"], e["z"])) \
        if "x" in e else float(e.get("dist_cm") or 1e9)
    bearing = math.degrees(math.atan2(e["y"] - p["y"], e["x"] - p["x"])) if "x" in e else None
    return e, s, (d, bearing)


def target_alive(snap, name):
    """目标敌人是否还在附近 Pawn 列表里（按实例名，不是按类名）。

    2026-10-24：HUD 那 7 根敌条是「场上所有怪」的条，取第一根会随镜头换目标，
    所以「血条没降」根本不能当「没打死」。引擎侧又读不到 HP
    （probe enemy 被只读闸拒，props 里也没有血量字段）。
    ⇒ 唯一可靠判据：这只怪从 near_pawns 里消失 = 死亡/卸载。
    """
    if not snap or not name:
        return None
    for n in snap.get("near_pawns") or []:
        if n.get("name") == name:
            return True
    return False


def turn_to(target_yaw, steps=8):
    for _ in range(steps):
        s = state()
        if not s or "view" not in s:
            return False
        d = ((target_yaw - s["view"]["yaw"] + 540) % 360) - 180
        if abs(d) < 3:
            return True
        inputs.look_rel(max(-400, min(400, d / DEG_PER_UNIT)), 0)
        time.sleep(0.08)
    return True


def combo(log):
    """左键×3 → 右键 → 左键×3 → key_4。控制好间隔，别一帧全发。"""
    for _ in range(3):
        inputs.click_left(); time.sleep(0.16)
    inputs.click_right(0.12); time.sleep(0.22)
    for _ in range(3):
        inputs.click_left(); time.sleep(0.16)
    inputs.tap_key("key_4"); time.sleep(0.30)
    log["combo"] = "L3 R L3 key4"


def engage(reason, log, dry=False):
    """面向敌人打最多 MAX_ROUNDS 轮；敌人血不降/打空或超时就撤。"""
    log["mode"] = "engage"
    log["rounds"] = []
    gone = 0
    ehp0 = None
    no_dmg = 0
    target = None
    for r in range(MAX_ROUNDS):
        e, s, ne = nearest()
        if not s or not e:
            return "idle", "no_enemy"
        if target is None:
            target = e.get("name")
        elif not target_alive(s, target):
            return "idle", "killed"
        d, bearing = ne
        if bearing is not None:
            turn_to(bearing)
        before = hud_live.probe_once()
        if dry:
            time.sleep(0.4)
        else:
            if d > 260:
                # 还够不着：向前压两步再打，避免原地挥空
                inputs.hold_key("fwd", 0.35)
            inputs.lock_on()
            combo(log)
            # 10-24 实测：格挡(E)与闪避(Shift)分开测。combo 后先撑盾 0.25s，
            # 再 Shift 点按拉开，两个键都在真机上过一遍。
            inputs.hold_key("guard", 0.25)
            inputs.tap_key("dodge")
            time.sleep(0.18)
        after = hud_live.probe_once()
        s2 = state()
        if target and not target_alive(s2, target):
            log["killed"] = target.split(".")[-1][:40]
            print("  目标已从 near_pawns 消失 → 判击杀", flush=True)
            return "idle", "killed"
        log["rounds"].append({"r": r, "dist_cm": round(d, 1),
                              "before": before, "after": after})
        print("  round%d dist=%.0f  %s" % (r, d, hud_live.fmt(after)), flush=True)
        if after is None:
            continue
        if after.get("enemy_hp") is None or not after.get("enemy_visible"):
            # 血条可能只是被闪避/转向挡住一拍，连续两拍消失才判脱战；
            # 2026-10-23 实测有过一拍消失后又出现的假“击杀”。
            gone += 1
            if gone >= 2:
                return "idle", "bar_lost"
            time.sleep(0.5)
            continue
        gone = 0
        hurt, red = is_hurt()
        if hurt:
            return "retreat", "hurt(red=%.1f>=%.0f)" % (red, HURT_RED)
        cur = after.get("enemy_hp")
        if ehp0 is None:
            ehp0 = cur
        else:
            # 2026-10-24：玩家 HP 条不在 UMG ProgressBar 里（40 根可见条全是敌人），
            # 所以受击触发/低血撤退都读不到。改成看“敌人有没有掉血”。
            if cur is not None and cur > ehp0 - 0.004:
                no_dmg += 1
                if no_dmg >= 3:
                    return "retreat", "enemy_hp_static(%.3f)" % cur
            else:
                no_dmg = 0
                ehp0 = cur
        if after.get("player_hp") is not None and after["player_hp"] <= ENGAGE_HP:
            return "retreat", "hp<=%.2f" % ENGAGE_HP
        if after.get("player_hp") is not None and after["player_hp"] <= 0.0001:
            return "idle", "dead"
    return "retreat", "rounds_exhausted"


def retreat(log, dry=False):
    """背向最近敌人连续冲刺；卡住就侧移/跳。脱离 30m 连续两拍算脱战。"""
    log["mode"] = "retreat"
    log["retreat"] = []
    t0 = time.time()
    safe = 0
    while time.time() - t0 < RETREAT_MAX_S:
        e, s, ne = nearest()
        h = hud_live.probe_once()
        if h is None:
            time.sleep(0.15)
            continue
        if h.get("player_hp") is not None and h["player_hp"] <= 0.0001:
            return "dead"
        if not e:
            return "idle"
        d, bearing = ne
        if d > SAFE_DIST_CM:
            safe += 1
            if safe >= 2:
                print("  已脱离（dist=%.0f）" % d, flush=True)
                return "idle"
        else:
            safe = 0
        target = (bearing + 180) % 360 if bearing is not None else None
        if target is not None:
            turn_to(target)
        p0 = state()["player"]
        if dry:
            time.sleep(0.4)
        else:
            inputs.hold_combo("sprint_fwd", 0.55)
        time.sleep(0.2)
        p1 = state()["player"]
        moved = math.dist((p0["x"], p0["y"]), (p1["x"], p1["y"]))
        log["retreat"].append({"t": round(time.time() - t0, 2),
                               "dist_cm": round(d, 0), "moved_cm": round(moved, 0),
                               "hp": h.get("player_hp")})
        print("  retreat t=%.1f dist=%.0f moved=%.0f hp=%s"
              % (time.time() - t0, d, moved, h.get("player_hp")), flush=True)
        if moved < 60:
            # 被地形卡住：斜 45° 冲刺一次，再试跳+前
            if target is not None:
                turn_to((target + 45) % 360)
            if dry:
                time.sleep(0.3)
            else:
                inputs.hold_combo("sprint_fwd", 0.45)
                inputs.tap_key("jump"); time.sleep(0.15); inputs.tap_key("jump")
                inputs.hold_key("fwd", 0.5)
            time.sleep(0.2)
    return "idle"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--act", action="store_true")
    ap.add_argument("--revive", action="store_true",
                    help="命中阵亡判据时自动按 R 复活（默认不自动按键）")
    a = ap.parse_args()
    dry = not a.act
    logp = os.path.join(HERE, "runs", "reflex_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    prev = hud_live.probe_once()
    print("基线", hud_live.fmt(prev), "模式", "DRY" if dry else "ACT", flush=True)
    mode = "idle"
    cooldown_until = 0.0
    last_dead_chk = 0.0
    dead_hits = 0
    t0 = time.time()
    with open(logp, "a", encoding="utf-8") as f:
        while time.time() - t0 < a.seconds:
            cur = hud_live.probe_once()
            if cur is None:
                time.sleep(0.1)
                continue
            # 玩家 HP 读不到，阵亡只能看画面：中央带暗像素比例落在阵亡窗口里
            # 连续两拍才认，避免把过场黑屏（0.77）当成死亡。
            if time.time() - last_dead_chk >= 1.0:
                last_dead_chk = time.time()
                df = dead_frac()
                if df is not None and DEAD_LO <= df <= DEAD_HI:
                    dead_hits += 1
                    print("  疑似阵亡 dark=%.2f (%d/2)" % (df, dead_hits), flush=True)
                    if dead_hits >= 2:
                        dead_hits = 0
                        if a.revive:
                            print("  按 R 复活", flush=True)
                            inputs._tap_vk(0x52)
                            time.sleep(6.0)
                        else:
                            print("玩家阵亡，停止（加 --revive 可自动按 R）", flush=True)
                            break
                else:
                    dead_hits = 0
            if cur.get("player_hp") is not None and cur["player_hp"] <= 0.0001:
                print("玩家阵亡，停止", flush=True)
                break
            if mode == "idle":
                if time.time() < cooldown_until:
                    prev = cur
                    time.sleep(0.3)
                    continue
                reason = hp_dropped(prev, cur)
                e, s, ne = nearest()
                d = ne[0] if ne else None
                # 2026-10-24：玩家 HP 读不到，受击触发失效。改为“敌人进 10m 且
                # 有血条”就主动接战——符合用户口径「要么一套打死，要么撤」。
                if not reason or reason == "baseline":
                    if e and cur.get("enemy_visible") and d is not None and d <= NEAR_CM:
                        reason = "enemy_near"
                if reason and reason != "baseline":
                    print("TRIGGER %s dist=%s" % (reason, round(d) if d else "-"), flush=True)
                    if d is not None and d <= NEAR_CM:
                        rec = {"t": round(time.time(), 3), "reason": reason,
                               "dist_cm": round(d, 1), "dry_run": dry}
                        hurt, red = is_hurt()
                        low_hp = (cur.get("player_hp") is not None
                                  and cur["player_hp"] <= ENGAGE_HP) or hurt
                        rec["red"] = None if red is None else round(red, 1)
                        if low_hp:
                            print("  红度 %s → 先撤" % (round(red, 1) if red else "-"), flush=True)
                            nxt = retreat(rec, dry=dry)
                        else:
                            nxt, why = engage(reason, rec, dry=dry)
                            print("  engage -> %s (%s)" % (nxt, why), flush=True)
                            if nxt == "retreat":
                                nxt = retreat(rec, dry=dry)
                        rec["next_mode"] = nxt
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        f.flush()
                        if nxt == "dead":
                            print("撤退中阵亡，停止", flush=True)
                            break
                        mode = "idle" if nxt == "idle" else nxt
                        # 一轮打完先冷却 10 秒，别刚撤出又冲回去送。
                        cooldown_until = time.time() + 10.0
                        prev = hud_live.probe_once()
                        continue
                    else:
                        print("  10m 内无敌人，不反击", flush=True)
            prev = cur
            time.sleep(0.05)
    print("结束，日志", logp)


if __name__ == "__main__":
    main()
