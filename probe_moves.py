"""probe_moves.py —— 盘"哪些输入真能出手"，带对照组

为什么必须有对照组（10-23 踩出来的）：训练场开着「无限能量」，BE 条每 0.7 秒
自己就在涨（28→29→30 几乎每个动作都出现）。没有对照，我会把所有键都判成"接住了"。

协议：
  对照 = 重置 → 走 0.9s 进范围 → 读数 → 什么都不按等 0.7s → 再读数   ×3
  实测 = 同上，只是中间真的做动作                                    ×N
判据 = 变化必须**超出对照组的漂移带**，否则一律记"存疑"，不记"无效"也不记"有效"。

三路人证：敌人盾格掉＝造成伤害；自身条变化超出漂移＝游戏接住了这个输入；
两路都在带内＝这个键位在这个场景下没观测到效果。

⚠ 不干净之处照旧写在结论里：重置不清技能冷却，靠后的候选可能因 CD 未转完而被低估；
采样窗 0.7s，短动画可能错过峰值。
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
IN_RANGE_S = 0.9         # probe_reach.py 实测：走这么久左键才砍得着
SETTLE = 0.7
KEYS = ("sh", "hp", "BU", "BE", "PHP", "PSH")

MOVES = [
    ("LMB 轻击",        lambda: inputs.click_left()),
    ("RMB 重击(点)",    lambda: inputs.click_right(0.06)),
    ("RMB 蓄力(按0.5)", lambda: inputs.click_right(0.5)),
    ("Space",           lambda: inputs.tap_key("jump", 0.10)),
    ("Shift 冲刺",      lambda: inputs.hold_key("sprint", 0.45)),
    ("Q",               lambda: inputs.tap_key("dodge", 0.10)),
    ("E",               lambda: inputs.tap_key("interact", 0.10)),
    ("R",               lambda: inputs.tap_key("key_r", 0.10)),
    ("F",               lambda: inputs.tap_key("key_f", 0.10)),
    ("V",               lambda: inputs.tap_key("key_v", 0.10)),
    ("B",               lambda: inputs.tap_key("key_b", 0.10)),
    ("C",               lambda: inputs.tap_key("key_c", 0.10)),
    ("X",               lambda: inputs.tap_key("key_x", 0.10)),
    ("T",               lambda: inputs.tap_key("key_t", 0.10)),
    ("1",               lambda: inputs.tap_key("key_1", 0.10)),
    ("2",               lambda: inputs.tap_key("key_2", 0.10)),
    ("3",               lambda: inputs.tap_key("key_3", 0.10)),
    ("4",               lambda: inputs.tap_key("key_4", 0.10)),
]


def snap(retries=2):
    """读一次全部计数器。读不到就重试；仍读不到返回 None（**不许当 0 用**）。"""
    for _ in range(retries + 1):
        f = capture.grab()
        e = hud.readout(f)
        if e and e.get("hp_pips") is not None:
            p = hud.player_bars(f)
            return {"sh": e["sh_pips"], "hp": e["hp_pips"],
                    "BU": p.get("BU"), "BE": p.get("BE"),
                    "PHP": p.get("HP"), "PSH": p.get("SH")}
        time.sleep(0.25)
    return None


FULL_HP, FULL_SH = 20, 14     # 重置后应达到的满状态（实测满血 21/16，留 1~2 格余量）


def settle_full(tries=6):
    """重置并等到敌人回到满状态，才允许开始这一试次。

    为什么必须加这一步（10-23 实测）：上一版没有前置状态校验，
    Space / Shift 冲刺这类不该造成伤害的动作也报了掉血，
    第 4 项还出现自身血条 -64 —— 前一刀的结算延迟会漏进后一拍的基线，
    试次之间不独立，整张表就只是好看。
    """
    for _ in range(tries):
        inputs.reset_training()
        time.sleep(4.0)
        inputs.hold_key("fwd", IN_RANGE_S)
        time.sleep(0.6)
        s = snap()
        if s and s["hp"] >= FULL_HP and s["sh"] >= FULL_SH:
            return s
        time.sleep(1.0)
    return None


def trial(fn=None):
    """跑一次完整条件：重置到满状态 → 进范围 → 读数 → (动作) → 再读数。"""
    a = settle_full()
    if a is None:
        return None
    if fn:
        fn()
    time.sleep(SETTLE)
    b = snap()
    if b is None:
        return None
    d = {k: (b[k] - a[k]) if (a[k] is not None and b[k] is not None) else None
         for k in KEYS}
    d["_raw"] = {"a": a, "b": b}
    return d


def main():
    only = set(sys.argv[1:])
    moves = [m for m in MOVES if not only or any(o in m[0] for o in only)]
    print("=== 对照组：什么都不按，量自然漂移 ===")
    ctrl = []
    while len(ctrl) < 3:
        d = trial()
        if d is None:
            print("  对照采样失败（条没读到），重跑一次")
            continue
        ctrl.append(d)
        print("  对照%d  " % (len(ctrl)),
              "  ".join("%s%+d" % (k, d[k]) for k in KEYS if d[k] is not None))
    band = {}
    for k in KEYS:
        vals = [d[k] for d in ctrl if d[k] is not None]
        band[k] = (min(vals), max(vals)) if vals else (0, 0)
    print("  漂移带:", {k: band[k] for k in KEYS})

    print("\n=== 实测组 ===")
    print("%-16s %-22s %s" % ("动作", "超出漂移的变化", "判读"))
    rows = []
    for name, fn in moves:
        d = trial(fn)
        if d is None:
            print("%-16s %-22s %s" % (name, "—", "采样失败(条没读到)"))
            rows.append({"move": name, "verdict": "sample_failed"})
            continue
        out = {k: d[k] for k in KEYS if d[k] is not None
               and not (band[k][0] <= d[k] <= band[k][1])}
        dmg = "sh" in out and out["sh"] < band["sh"][0]
        hp = "hp" in out and out["hp"] < band["hp"][0]
        if dmg or hp:
            verdict = "★造成伤害 " + " ".join("%s%+d" % (k, out[k]) for k in out)
        elif out:
            verdict = "接住了·未命中 " + " ".join("%s%+d" % (k, out[k]) for k in out)
        else:
            verdict = "存疑(全在漂移带内)"
        print("%-16s %-22s %s" % (
            name, " ".join("%s%+d" % (k, v) for k, v in out.items()) or "无", verdict))
        rows.append({"move": name, "delta": {k: d[k] for k in KEYS},
                     "outside_band": out, "verdict": verdict})

    with open(os.path.join(HERE, "runs", "moves_result.json"), "w",
              encoding="utf-8") as f:
        json.dump({"in_range_s": IN_RANGE_S, "settle": SETTLE,
                   "control_band": band, "control_raw": [d["_raw"] for d in ctrl],
                   "rows": rows}, f, ensure_ascii=False, indent=2)
    print("\n漂移带与明细存 runs/moves_result.json")
    print("注意：重置不清 CD，靠后的候选可能被低估；「存疑」≠「无效」。")


if __name__ == "__main__":
    main()
