"""boss_rush.py —— 首领战连续压制（格挡版 + 自愈，打死为止）

2026-10-24 用户口径（三条，都落在这里）：
  1. 闪避会打断自身攻击 → 伤害与能量都掉，所以防守用**格挡 E**，不用 Shift；
  2. 长时间不动会被持续打到死 → 每个攻击间隙短按格挡，不能整段不防；
  3. **敌人死亡、战斗结束之前不能停止行动** → 阵亡自动按 R 复活、重新上楼、
     过场结束后接着打，只有首领真的倒下才收工。

击杀判据（10-24 改）：
  ✗ 旧写法「首领实例名从 near_pawns 消失」是错的——亚巴顿首战胜利后尸体/pawn
    仍留在 near_pawns（实测：结算画面已出，实例名还在，循环白打 100 多秒）。
  ✓ 新写法：nearest_enemy 为空 且 12 秒内交过战（距离<15m）→ 二次确认 →
    等同「挑战成功」结算画面。用结算截图做人眼复核。

读数边界（10-24 实测）：
  · 玩家 HP / 首领 HP 都读不到（UMG 无玩家条、引擎无血量组件、blackboard=null）；
  · 屏幕红度与条填充比在本场景已证伪，这里一个字都不依赖它们；
  · 唯一的真值来源：桥的玩家位置/最近敌人 + 结算面板（受到的伤害、完美招架）截图。
"""
import argparse
import math
import os
import time

import capture
import inputs
import sb_bridge

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = (36390.0, -50900.0)      # 入口/复活落点（楼梯口，实测）
FAR_CM = 3000.0                  # 敌人超过 30m 视为没在交战


def snap():
    s = sb_bridge.read(2)
    return s if s and not s.get("_stale") else None


def face(p, s, e, gain=0.55, dead=12.0):
    b = math.degrees(math.atan2(e["y"] - p["y"], e["x"] - p["x"]))
    diff = ((b - s["view"]["yaw"] + 540) % 360) - 180
    if abs(diff) > dead:
        inputs.look_yaw(diff * gain)
        return round(diff, 1)
    return 0.0


def at_entry(p):
    return math.dist((p["x"], p["y"]), ENTRY) < 300


def shot(name):
    im = capture.grab()
    im.crop((850, 60, 2740, 240)).save(os.path.join(HERE, "runs", name))
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=1800,
                    help="保险上限；默认 30 分钟，正常应被击杀判据提前结束")
    ap.add_argument("--attack-n", type=int, default=3, help="每轮连打几下")
    ap.add_argument("--guard-s", type=float, default=0.3, help="每次格挡按住时长")
    ap.add_argument("--burst-every", type=float, default=22.0)
    ap.add_argument("--potion-every", type=float, default=26.0)
    ap.add_argument("--shots-every", type=float, default=45.0,
                    help="每隔多久裁一张首领血条数字图，供人工读真值")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    inputs.focus_game()
    time.sleep(0.4)
    t0 = time.time()
    hits = bursts = potions = revives = 0
    target = None
    dead_since = None
    was_fighting = False
    last_seen = None          # (t, dist_cm) 最近一次看到有效敌人
    next_burst = a.burst_every
    next_potion = a.potion_every
    next_shot = 0.0
    next_guard = 0.0
    last_log = -1

    while time.time() - t0 < a.seconds:
        t = time.time() - t0
        s = snap()
        if not s:
            time.sleep(0.2)
            continue
        p = s["player"]
        e = s.get("enemy")
        far = (not e) or e.get("dist_cm", 9e9) > FAR_CM
        if e:
            last_seen = (t, e.get("dist_cm", 9e9))

        # ── 击杀判据 v2：nearest_enemy 消失（尸体仍在 near_pawns 属正常） ──
        if was_fighting and not e and last_seen and t - last_seen[0] < 12.0:
            time.sleep(0.6)
            s2 = snap() or {}
            if not s2.get("enemy"):
                print("t=%.0f ★ nearest_enemy 消失（最后距离 %.1fm）→ 首领倒下/战斗结束"
                      % (t, last_seen[1] / 100.0), flush=True)
                shot("rush_kill.png")
                break

        # 旧判据留作兜底：实例名真的没了（换波/换图时也会命中）
        if target:
            names = [n.get("name") for n in (s.get("near_pawns") or [])]
            if target not in names and not far:
                s2 = snap() or {}
                names2 = [n.get("name") for n in (s2.get("near_pawns") or [])]
                if target not in names2:
                    print("t=%.0f ★ 首领实例名已消失 → 战斗结束" % t, flush=True)
                    shot("rush_kill.png")
                    break

        # ── 复活/重进：只有「打起来过」之后又回到入口才算阵亡 ──
        # 不能用画面暗度判死：雨夜屋顶正常场景 dark_frac 就是 0.26，
        # 正好落进我给的 0.25~0.60 窗口，脚本因此在活人身上按了 R，
        # 白吃掉一瓶药（药水 3→2，10-24 实测）。改成位置+交战史：
        # 进过 20m 内（was_fighting）→ 又站回入口且敌人 30m 外 = 已阵亡。
        if at_entry(p) and far:
            if dead_since is None:
                dead_since = t
                print("t=%.0f 在入口且无交战（was_fighting=%s）" % (t, was_fighting), flush=True)
                time.sleep(1.2)
                continue
            if t - dead_since > 1.5:
                if was_fighting:
                    revives += 1
                    print("t=%.0f 第 %d 次复活：R → 上楼 → 等过场" % (t, revives), flush=True)
                    if not a.dry_run:
                        inputs._tap_vk(0x52)         # R 复活
                        time.sleep(9.0)
                else:
                    print("t=%.0f 还没开打 → 直接上楼开战" % t, flush=True)
                if not a.dry_run:
                    inputs.hold_key("fwd", 3.4)      # 上楼梯
                    time.sleep(0.6)
                dead_since = None
                target = None
                was_fighting = False
                next_burst = t + 26.0
                next_potion = t + 20.0
                next_shot = t + 26.0
                for _ in range(60):              # 等过场结束、敌人进 20m
                    s2 = snap() or {}
                    e2 = s2.get("enemy")
                    if e2 and e2.get("dist_cm", 9e9) < 2000:
                        was_fighting = True
                        break
                    time.sleep(1.0)
                continue
        else:
            dead_since = None

        if not e:
            time.sleep(0.2)
            continue
        if target is None:
            target = e.get("name")
        if e.get("dist_cm", 9e9) < 2000:
            was_fighting = True

        turn = face(p, s, e)
        d = e["dist_cm"] / 100.0

        if t >= next_shot:
            next_shot += a.shots_every
            if not a.dry_run:
                shot("rush_t%03d.png" % int(t))
                print("t=%.0f 血条图 rush_t%03d.png" % (t, int(t)), flush=True)

        if t >= next_burst:
            next_burst += a.burst_every
            bursts += 1
            print("t=%.0f 试放爆发 Alt+4 (#%d) d=%.1fm" % (t, bursts, d), flush=True)
            if not a.dry_run:
                inputs.burst(4)
                time.sleep(1.5)

        if t >= next_potion:
            next_potion += a.potion_every
            potions += 1
            print("t=%.0f 喝药 Q (#%d)" % (t, potions), flush=True)
            if not a.dry_run:
                inputs.tap_key("potion")
            time.sleep(0.5)

        if d > 4.0:
            # 接近时绝不能按格挡：实测按住 E 期间走路会被吃掉，
            # 距离钉死在 6.0m、命中数 225→231 卡了 60 秒（10-24 实战日志）。
            if not a.dry_run:
                inputs.hold_key("fwd", 0.36)
            time.sleep(0.1)
        else:
            for _ in range(a.attack_n):
                if not a.dry_run:
                    inputs.click_left()
                time.sleep(0.15)
                hits += 1
            if hits % 12 == 0 and not a.dry_run:
                inputs.click_right(0.12)
                time.sleep(0.28)
            # 格挡只在贴身攻击段之后补，且带冷却，别吞下一轮连击（用户 10-24 口径）
            if t >= next_guard:
                next_guard = t + 1.0
                if not a.dry_run:
                    inputs.hold_key("guard", a.guard_s)
        time.sleep(0.2)

        if int(t) // 30 != last_log:
            last_log = int(t) // 30
            print("t=%.0f hits=%d d=%.1fm target=%s"
                  % (t, hits, d, (target or "").split(".")[-1][:34]), flush=True)

    print("结束：hits=%d bursts=%d potions=%d revives=%d 用时 %.0fs"
          % (hits, bursts, potions, revives, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
