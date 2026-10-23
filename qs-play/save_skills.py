"""save_skills.py —— 从剑星存档读主角已获得的技能/能力（只读）

2026-10-23 实测：存档不是加密/压缩的，外层是 8 字节 `EVAS` 包装，里面是
标准 GVAS 属性序列化。技能状态在 `AcquisitionSkillList` 里，
每一项是 `AcquisitionSkillAlias`（NameProperty）。

本脚本只做两件事：
  1. 扫出全部唯一的 AcquisitionSkillAlias（实测 170 条）；
  2. 读 SPLevel / SPExp（IntProperty）。

⚠ 这是“已获得”的状态，不是“游戏里全部技能定义”。全量定义在资产
   SkillTable / SkillStringTable / SkillCommandTable / SkillResultTable 里，
   需要另外解包或运行时 dump，脚本不碰。
⚠ 存档只在游戏保存时更新；内存里刚学的技能可能还没落盘。
"""
import argparse
import collections
import glob
import json
import os
import re
import struct
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "save_skills.json")


def default_save():
    pat = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                       "SB", "Saved", "SaveGames", "*", "StellarBladeSave00.sav")
    files = glob.glob(pat)
    return max(files, key=os.path.getmtime) if files else None


def fstr(d, o):
    n = struct.unpack_from("<i", d, o)[0]
    if n < 0 or n > 400:
        return None, o
    s = d[o + 4:o + 4 + n - 1].decode("utf-8", "replace") if n > 0 else ""
    return s, o + 4 + n


def scan_name_prop(d, prop):
    """扫某个 NameProperty 的所有取值。

    存档的每个 tag 前有 1 字节 0x00、每个 value 前也有 1 字节 0x00
    （实测；不是标准 UE 序列化）。所以：name/type/size/index 之后跳 1 字节。
    """
    out = []
    for m in re.finditer(prop.encode(), d):
        o = m.start() - 4
        name, o = fstr(d, o)
        if name != prop:
            continue
        typ, o = fstr(d, o)
        if typ != "NameProperty":
            continue
        o += 9                       # size + array_index + 1 字节前缀
        v, _ = fstr(d, o)
        if v and len(v) < 80:
            out.append(v)
    return out


def scan_int_prop(d, prop):
    out = []
    for m in re.finditer(prop.encode(), d):
        o = m.start() - 4
        name, o = fstr(d, o)
        if name != prop:
            continue
        typ, o = fstr(d, o)
        if typ != "IntProperty":
            continue
        o += 9
        try:
            out.append(struct.unpack_from("<i", d, o)[0])
        except struct.error:
            pass
    return out


def classify(names):
    c = collections.defaultdict(list)
    for v in names:
        if v.startswith(("P_EVE_", "P_Eve_")):
            c["meta_points_items"].append(v)
        elif v.startswith("TachyMode_"):
            c["tachy_mode"].append(v)
        elif v.startswith("Burst"):
            c["burst"].append(v)
        elif "Upgrade" in v:
            c["upgrades"].append(v)
        else:
            c["combat_base"].append(v)
    return {k: sorted(v) for k, v in c.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", default=None)
    ap.add_argument("--print", action="store_true", dest="show")
    a = ap.parse_args()
    path = a.save or default_save()
    if not path or not os.path.exists(path):
        raise SystemExit("找不到存档；用 --save 指定")
    d = open(path, "rb").read()
    if d[:4] != b"EVAS":
        raise SystemExit("不是 EVAS 存档（magic=%r）" % d[:4])

    aliases = []
    for v in scan_name_prop(d, "AcquisitionSkillAlias"):
        if v not in aliases:
            aliases.append(v)
    groups = classify(aliases)
    sp_level = scan_int_prop(d, "SPLevel")
    sp_exp = scan_int_prop(d, "SPExp")

    doc = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": path,
        "save_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(os.path.getmtime(path))),
        "summary": {"acquired": len(aliases),
                    **{k: len(v) for k, v in groups.items()},
                    "sp_level": sp_level[0] if sp_level else None,
                    "sp_exp": sp_exp[0] if sp_exp else None},
        "aliases": sorted(aliases),
        "groups": groups,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)

    print("存档：%s（%s）" % (path, doc["save_mtime"]))
    print("已获得 %d 条：战斗 %d / 升级 %d / Tachy %d / Burst %d / 点数与道具 %d"
          % (len(aliases), len(groups.get("combat_base", [])),
             len(groups.get("upgrades", [])), len(groups.get("tachy_mode", [])),
             len(groups.get("burst", [])), len(groups.get("meta_points_items", []))))
    print("SPLevel=%s SPExp=%s" % (sp_level[0] if sp_level else "?",
                                   sp_exp[0] if sp_exp else "?"))
    print("写出 %s" % OUT)
    if a.show:
        for k, v in groups.items():
            print("\n[%s] %d\n  %s" % (k, len(v), ", ".join(v)))


if __name__ == "__main__":
    main()
