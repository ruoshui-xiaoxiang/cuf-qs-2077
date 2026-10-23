"""hud_live.py —— 从 HUD 控件读玩家/敌人的 HP 与护盾（百分比）

2026-10-23 实测：剑星的 HUD 是 UMG，血条/盾条都是 UProgressBar，
`Percent` 直接给 0..1。比截图像素稳得多，也不需要引擎属性。

当前它靠反复发只读 `probe widgets` 取数（不重启、不改 Lua）：
  · 每次让 Lua 扫一遍 UProgressBar 并写 probe_widgets.json
  · Python 按控件全名挑出玩家/敌人那几根，取 Percent 与可见性
  · 默认 5Hz 采样，写 runs/hud_live_*.jsonl

⚠ 这是过渡方案：每拍扫 120 个控件 + 写 24KB 文件。验证通过后应把
   这几根控件缓存进 Lua 的 state.json（v3.3），反射层直接读。
"""
import argparse
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = r"D:/SteamLibrary/steamapps/common/StellarBlade/SB/Binaries/Win64/ue4ss/Mods/SBBridge"
CMD = os.path.join(DIR, "cmd.json")
OUT = os.path.join(DIR, "probe_widgets.json")

PLAYER_HP = "WB_MainHUD_PlayerInfo"
ENEMY = "WB_MainHUD_EnemyStateBar_C"
TACHY = "ProgressBar_TachyGauge"
FX = "ProgressBar_FxGauge"


def _percent(bar):
    v = bar.get("percent")
    return float(v) if isinstance(v, (int, float)) else None


def probe_once(timeout=3.0):
    """发一次只读 widgets 探针，返回解析后的关键条；超时返回 None。"""
    t0 = os.path.getmtime(OUT) if os.path.exists(OUT) else 0
    with open(CMD, "w", encoding="utf-8") as f:
        f.write(json.dumps({"action": "probe", "arg": "widgets"}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.02)
        try:
            if os.path.getmtime(OUT) > t0:
                d = json.load(open(OUT, encoding="utf-8"))
                return parse(d)
        except (OSError, ValueError):
            continue
    return None


def parse(d):
    """从 120 根 ProgressBar 里挑出玩家/敌人那几根。按全名精确匹配，
    避免把 WB_MainHUD_Return.ProgressBar_HP（返回/远程条）当成玩家血。"""
    res = {"player_hp": None, "player_shield": [], "tachy": None,
           "fx": [], "enemy_hp": None, "enemy_shield": [],
           "enemy_visible": False, "player_visible": False, "ts": time.time()}
    for b in d.get("bars") or []:
        name = b.get("name") or ""
        vis = bool(b.get("visible"))
        pct = _percent(b)
        if pct is None or not vis:
            # 只认可见控件：模板/预置控件（如 WB_MainHUD_PlayerInfo_C:WidgetTree
            # 里那根 0.520 的 HP）和未启用的 Shield_3~5 都是不可见的。
            continue
        live = "WB_MainHUD_Master_C" in name
        if live and PLAYER_HP in name and name.endswith("ProgressBar_HP"):
            res["player_hp"], res["player_visible"] = pct, vis
        elif live and PLAYER_HP in name and "WB_MainHud_Shield_" in name \
                and name.endswith("ProgressBar_Shield_1"):
            res["player_shield"].append(pct)
        elif live and PLAYER_HP in name and name.endswith(TACHY):
            res["tachy"] = pct
        elif live and PLAYER_HP in name and "WB_MainHUD_FxGauge_" in name \
                and name.endswith(FX):
            res["fx"].append(pct)
        elif ENEMY in name and name.endswith("ProgressBar_HP"):
            res["enemy_hp"], res["enemy_visible"] = pct, vis
        elif ENEMY in name and "WB_MainHud_ShieldSmall_" in name \
                and name.endswith("ProgressBar_Shield_1"):
            res["enemy_shield"].append(pct)
    res["player_shield"].sort(reverse=True)
    res["enemy_shield"].sort(reverse=True)
    res["fx"].sort(reverse=True)
    return res


def fmt(r):
    if r is None:
        return "no-data"
    return ("P.hp=%s sh=%s fx=%s tachy=%s | E.hp=%s sh=%s vis=%s"
            % ("%.3f" % r["player_hp"] if r["player_hp"] is not None else "-",
               "/".join("%.2f" % x for x in r["player_shield"]) or "-",
               "/".join("%.2f" % x for x in r["fx"]) or "-",
               "%.2f" % r["tachy"] if r["tachy"] is not None else "-",
               "%.3f" % r["enemy_hp"] if r["enemy_hp"] is not None else "-",
               "/".join("%.2f" % x for x in r["enemy_shield"]) or "-",
               r["enemy_visible"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--hz", type=float, default=5)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    outdir = os.path.join(HERE, "runs")
    os.makedirs(outdir, exist_ok=True)
    logp = os.path.join(outdir, "hud_live_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    t0 = time.time()
    n = 0
    prev = None
    with open(logp, "a", encoding="utf-8") as f:
        while time.time() - t0 < a.seconds:
            start = time.time()
            r = probe_once()
            if r is not None:
                r["seq"] = n
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                if not a.quiet:
                    change = ""
                    if prev is not None:
                        if prev["player_hp"] != r["player_hp"]:
                            change += "  ←HP %s→%s" % (prev["player_hp"], r["player_hp"])
                        if prev["enemy_hp"] != r["enemy_hp"]:
                            change += "  ←EHP %s→%s" % (prev["enemy_hp"], r["enemy_hp"])
                    print("%4d %.2fs %s%s" % (n, time.time() - t0, fmt(r), change), flush=True)
                prev = r
                n += 1
            time.sleep(max(0.0, 1.0 / a.hz - (time.time() - start)))
    print("采样 %d 次，日志 %s" % (n, logp))


if __name__ == "__main__":
    main()
