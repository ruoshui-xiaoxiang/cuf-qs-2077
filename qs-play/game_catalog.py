"""game_catalog.py —— 从游戏文件里列出敌人 / NPC 名单（只读）

数据源（都不需要解包工具）：
1. **IoStore 名字表**：`SB/Content/Paks/pakchunk0-WindowsNoEditor.utoc`
   里有一段明文 ASCII 名字表，含全部 `CH_*_Blueprint.uasset` 与
   `CH_*_Skeleton.uasset`。扫它就能拿到"游戏里存在哪些角色资产"。
   注意：这是**资产目录**，不是某个关卡的生怪表。
2. **UE4SS actor dump**：`ue4ss/<ts>-ue4ss_actor_data.csv`（Ctrl+7 产物），
   这是"当时那一局实际 spawn 了谁 + 坐标"。它只覆盖 dump 时所在的地图。

用法：
    py -3.12 game_catalog.py                # 扫默认安装，写 data/characters.json
    py -3.12 game_catalog.py --print        # 额外打印分类明细
"""
import argparse
import collections
import csv
import glob
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = r"D:/SteamLibrary/steamapps/common/StellarBlade"
UTOC = os.path.join(GAME, "SB/Content/Paks/pakchunk0-WindowsNoEditor.utoc")
UE4SS = os.path.join(GAME, "SB/Binaries/Win64/ue4ss")
OUT = os.path.join(HERE, "data", "characters.json")

RUN = re.compile(rb"[\x20-\x7e]{6,}")


def scan_utoc(path=UTOC):
    """返回 {'blueprints': {...}, 'skeletons': {...}}，值为资产基名。"""
    data = open(path, "rb").read()
    bp, sk = set(), set()
    for raw in RUN.findall(data):
        s = raw.decode("ascii", "ignore")
        if not s.startswith("CH_") or not s.endswith(".uasset"):
            continue
        base = s[:-len(".uasset")]
        if base.endswith("_Skeleton"):
            sk.add(base[:-len("_Skeleton")])
        elif base.endswith("_Blueprint") or base.endswith("_BP"):
            bp.add(base)
    return {"blueprints": bp, "skeletons": sk}


def classify(names):
    """按前缀分类；未识别的进 other，不猜。"""
    out = collections.defaultdict(list)
    for n in sorted(names):
        if n.endswith("_Seq"):
            out["cinematic_variants"].append(n)
        elif n.startswith("CH_M_"):
            out["monsters"].append(n)
        elif n.startswith("CH_NPC_"):
            out["npcs"].append(n)
        elif n.startswith("CH_P_"):
            out["player"].append(n)
        elif n.startswith("CH_Item_"):
            out["items"].append(n)
        elif n.startswith("CH_Dron"):
            out["drone"].append(n)
        else:
            out["other"].append(n)
    return dict(out)


def latest_actor_dump():
    files = sorted(glob.glob(os.path.join(UE4SS, "*-ue4ss_actor_data.csv")),
                   key=os.path.getmtime)
    return files[-1] if files else None


def scan_actor_dump(path):
    """运行时 dump：distinct 角色类名 → 出现次数。只收 CH_ 类。"""
    if not path:
        return {}
    cnt = collections.Counter()
    with open(path, encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 2:
                continue
            m = re.search(r"BlueprintGeneratedClass'([^']+)'", row[1])
            if not m:
                continue
            name = m.group(1).split("/")[-1].split(".")[0]
            if name.startswith("CH_"):
                cnt[name] += 1
    return dict(cnt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--utoc", default=UTOC)
    ap.add_argument("--print", action="store_true", dest="show")
    a = ap.parse_args()

    t0 = time.time()
    cat = scan_utoc(a.utoc)
    groups = classify(cat["blueprints"])
    skel = classify(cat["skeletons"])
    dump_path = latest_actor_dump()
    runtime = scan_actor_dump(dump_path)

    doc = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_in_s": round(time.time() - t0, 1),
        "source": {"utoc": a.utoc, "utoc_mtime": time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(a.utoc)))},
        "summary": {
            "character_blueprints": sum(len(v) for v in groups.values()),
            "skeleton_bases": sum(len(v) for v in skel.values()),
            "runtime_classes": len(runtime),
        },
        "characters": groups,
        "skeletons": skel,
        "runtime_dump": {
            "file": os.path.basename(dump_path) if dump_path else None,
            "classes": dict(sorted(runtime.items(), key=lambda kv: -kv[1])),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    print("写入 %s（%.1fs）" % (OUT, time.time() - t0))
    print("角色蓝图 %d：怪 %d / NPC %d / 玩家 %d / 其它 %d"
          % (doc["summary"]["character_blueprints"],
             len(groups.get("monsters", [])), len(groups.get("npcs", [])),
             len(groups.get("player", [])),
             len(groups.get("other", [])) + len(groups.get("items", []))
             + len(groups.get("drone", [])) + len(groups.get("cinematic_variants", []))))
    print("骨架基名 %d：怪 %d / NPC %d"
          % (doc["summary"]["skeleton_bases"],
             len(skel.get("monsters", [])), len(skel.get("npcs", []))))
    if dump_path:
        print("运行时 dump：%s，%d 个 CH_ 类"
              % (os.path.basename(dump_path), len(runtime)))
    if a.show:
        for k, v in groups.items():
            print("\n[%s] %d\n  %s" % (k, len(v), "\n  ".join(v[:40])))


if __name__ == "__main__":
    main()
