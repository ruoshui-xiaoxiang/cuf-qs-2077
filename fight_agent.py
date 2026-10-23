"""fight_agent.py —— 模型战术层 + 脚本反应层

分工（10-24 定）：
  · 战术：tactician.py 每秒出一词（push/poke/retreat/skill/heal/wait），0.5s 实测延迟；
  · 反应：闪光→闪避、距离→接近、血低→喝药，全在本地 100ms 内完成，不等模型；
  · 模型慢/挂了就沿用上一个意图（TTL 2.5s），不会卡住手。

安全：不碰光标；游戏不在前台立刻退出；HUD 全零（后台暂停）不盲动，连续 3 次才按 Space。
"""
import argparse
import ctypes
import os
import threading
import time

import numpy as np
from PIL import ImageGrab

import hud_energy
import inputs
import sb_bridge
from tactician import Tactician

HERE = os.path.dirname(os.path.abspath(__file__))
user32 = ctypes.windll.user32
ENTRY = (36390.0, -50900.0)

_box = ImageGrab.grab()
_W, _H = _box.size
HUD_BBOX = (0, int(_H * 0.77), int(_W * 0.30), _H)
HUD_OY = HUD_BBOX[1]
FLASH_BBOX = (int(_W * 0.22), int(_H * 0.18), int(_W * 0.78), int(_H * 0.86))
SMALL = (480, 270)
V_MIN, S_MIN = 205, 80
FLASH_MIN, FLASH_K = 900, 2.2
INTENT_TTL = 2.5


def grab_hud():
    return ImageGrab.grab(bbox=HUD_BBOX)


def flash_px():
    a = np.asarray(ImageGrab.grab(bbox=FLASH_BBOX).resize(SMALL).convert("HSV"))
    return int(((a[..., 2] >= V_MIN) & (a[..., 1] >= S_MIN)).sum())


def fg(hwnd):
    return user32.GetForegroundWindow() == hwnd


class Brain(threading.Thread):
    """后台决策线程：主循环把状态摘要塞进来，它把意图写回 latest。"""

    def __init__(self, tac):
        super().__init__(daemon=True)
        self.tac = tac
        self.job = None
        self.lock = threading.Lock()
        self.latest = (None, 0.0, "", 0.0)   # intent, ts, digest, latency
        self.busy = False

    def submit(self, digest):
        with self.lock:
            if not self.busy:
                self.busy = True
                self.job = digest

    def run(self):
        while True:
            digest = None
            with self.lock:
                if self.job:
                    digest = self.job
                    self.job = None
            if digest:
                intent, lat = self.tac.decide(digest)
                with self.lock:
                    if intent:
                        self.latest = (intent, time.time(), digest, lat)
                    self.busy = False
            else:
                time.sleep(0.05)


def act(intent, d, st, skill_i, dry=False):
    """把意图翻成 0.6~1.0 秒的动作。返回 (新 skill_i, 描述)。"""
    if not dry:
        if d > 3.5 and intent != "retreat":
            inputs.hold_key("fwd", 0.30)
            return skill_i, "接近"
        if intent == "push":
            for _ in range(4):
                inputs.click_left()
                time.sleep(0.15)
            return skill_i, "连段4"
        if intent == "poke":
            for _ in range(2):
                inputs.click_left()
                time.sleep(0.15)
            inputs.hold_key("guard", 0.18)
            return skill_i, "试探2+挡"
        if intent == "retreat":
            inputs.hold_key("back", 0.40)
            inputs.hold_key("guard", 0.15)
            return skill_i, "后撤"
        if intent == "skill":
            key = ("key_2", "key_3", "key_4")[skill_i % 3]
            inputs.tap_key(key)
            time.sleep(0.35)
            return skill_i + 1, "技能" + key[-1]
        if intent == "heal":
            inputs.tap_key("potion")
            time.sleep(0.4)
            return skill_i, "喝药"
        # wait
        inputs.hold_key("guard", 0.30)
        return skill_i, "等"
    return skill_i, "(dry)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--tag", default="a1")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    ok, hwnd = inputs.focus_game()
    if not ok:
        print("游戏不在前台 → 不启动", flush=True)
        return
    time.sleep(0.3)

    tac = Tactician()
    brain = Brain(tac)
    brain.start()

    t0 = time.time()
    base = 400.0
    last_dodge = -9e9
    last_heal = -9e9
    zero = 0
    skill_i = 0
    prev_hp = None
    hit_times = []
    next_decide = 0.0
    last_log = -1
    was_fighting = False
    last_unpause = -9e9
    logf = open(os.path.join(HERE, "runs", "agent_%s.csv" % a.tag), "w",
                encoding="utf-8")
    logf.write("t,hp,sh,be,bu,dist,intent,latency,action\n")

    while time.time() - t0 < a.seconds:
        t = time.time() - t0
        if not fg(hwnd):
            print("%.1fs 失去焦点 → 退出" % t, flush=True)
            break

        # 反应层：闪光 → 闪避（不等模型）
        fscore = flash_px()
        base = base * 0.90 + fscore * 0.10
        if fscore >= FLASH_MIN and fscore >= FLASH_K * max(base, 1.0) and (t - last_dodge) > 0.8:
            last_dodge = t
            if not a.dry_run:
                inputs.tap_key("dodge")
            print("%6.2fs 闪光(%d) → 闪避(反应层)" % (t, fscore), flush=True)
            continue

        r = hud_energy.ratios(grab_hud(), oy=HUD_OY)
        if max(r.values()) <= 0.001:
            zero += 1
            # 切图/过场也会没有 HUD，所以要求连续 8 次（约 4 秒）且 10 秒内只试一次
            if zero >= 8 and (t - last_unpause) > 10:
                last_unpause = t
                print("%6.2fs HUD 全零×%d → 按 Space 解暂停/过场" % (t, zero), flush=True)
                if not a.dry_run:
                    inputs.tap_key("jump")
            continue
        zero = 0

        st = sb_bridge.read(0.6) or {}
        e = st.get("enemy") or {}
        p = st.get("player") or {}
        d = e.get("dist_cm", 9e9) / 100.0

        if d < 20:
            was_fighting = True

        # 阵亡：必须交过战、又在入口且敌人在 30m 外，才算死（开局就站在入口不算）
        if was_fighting and p and d > 30 and ((p.get("x", 0) - ENTRY[0]) ** 2 + (p.get("y", 0) - ENTRY[1]) ** 2) < 300 ** 2:
            print("%6.2fs 判定阵亡 → R 复活" % t, flush=True)
            if not a.dry_run:
                inputs._tap_vk(0x52)
                time.sleep(8.0)
                inputs.hold_key("fwd", 3.2)
            was_fighting = False
            continue

        # 挨打统计（HP 比例下降）
        if prev_hp is not None and r["HP"] < prev_hp - 0.02:
            hit_times.append(t)
        prev_hp = r["HP"]
        hit_times = [x for x in hit_times if t - x < 3.0]

        # 每秒给模型一拍
        if t >= next_decide:
            next_decide = t + 1.0
            pct = lambda v: min(100.0, v / 0.787 * 100)
            digest = "HP %.0f%% SH %.0f%% BE %.0f%% BU %.0f%% 距离%.1fm 首领未读数 最近3秒挨打%d次" % (
                pct(r["HP"]), pct(r["SH"]), pct(r["BE"]), pct(r["BU"]), d, len(hit_times))
            brain.submit(digest)

        intent, ts, digest, lat = brain.latest
        if intent is None or (time.time() - ts) > INTENT_TTL:
            intent = "poke"          # 没决策就用最保守的试探
        if intent == "heal" and (t - last_heal) < 20:
            intent = "poke"
        if intent == "heal":
            last_heal = t

        skill_i, desc = act(intent, d, st, skill_i, a.dry_run)
        logf.write("%.1f,%.2f,%.2f,%.2f,%.2f,%.1f,%s,%.2f,%s\n"
                   % (t, r["HP"], r["SH"], r["BE"], r["BU"], d, intent, lat, desc))

        if int(t) // 15 != last_log:
            last_log = int(t) // 15
            print("%6.2fs HP=%.2f BE=%.2f d=%.1f 模型=%s(%.2fs) → %s"
                  % (t, r["HP"], r["BE"], d, intent, lat, desc), flush=True)

    logf.close()
    print("结束，CSV=runs/agent_%s.csv" % a.tag, flush=True)


if __name__ == "__main__":
    main()
