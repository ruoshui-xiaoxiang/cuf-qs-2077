"""sb_bridge.py —— 读剑星 UE4SS 文件桥（Python 侧）

Lua 每 100ms 写 state.json；这里读它，并做两件必要的防护：
1. **新鲜度**：超过 1.5 秒没更新就当"桥死了"，返回 None，不拿旧坐标当现在。
2. **写闸**：send() 只有在 allow_write.txt 存在时才落 cmd.json——
   和 Lua 侧同一条闸，两边都拦，避免我（或以后的快回路）手滑改游戏状态。
"""
import json
import os
import time

UE4SS = r"D:/SteamLibrary/steamapps/common/StellarBlade/SB/Binaries/Win64/ue4ss"
DIR = os.path.join(UE4SS, "Mods", "SBBridge")
STATE = os.path.join(DIR, "state.json")
CMD = os.path.join(DIR, "cmd.json")
UNLOCK = os.path.join(DIR, "allow_write.txt")
LOG = os.path.join(UE4SS, "UE4SS.log")


def read(max_age_s=1.5):
    """返回 dict；桥没在写、文件不存在或太旧 → None。"""
    try:
        age = time.time() - os.path.getmtime(STATE)
    except OSError:
        return None
    if age > max_age_s:
        return {"_stale": True, "_age_s": round(age, 1)}
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    d["_age_s"] = round(age, 3)
    return d


def write_enabled():
    return os.path.exists(UNLOCK)


def send(action, arg=None):
    """请求一个动作。未解锁一律拒绝（返回 False 和原因），不写文件。"""
    if not write_enabled():
        return False, "denied: allow_write.txt 不存在（只读模式）"
    payload = {"action": action}
    if arg is not None:
        payload["arg"] = arg
    with open(CMD, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))
    return True, "queued"


def tail_log(n=12):
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


if __name__ == "__main__":
    s = read()
    print("write_enabled:", write_enabled())
    if s is None:
        print("桥没在写：state.json 不存在或读不出")
        print("--- UE4SS.log 末尾 ---")
        print(tail_log())
    elif s.get("_stale"):
        print("桥停了：state.json 已 %.1fs 没更新" % s["_age_s"])
        print("--- UE4SS.log 末尾 ---")
        print(tail_log())
    else:
        print("桥在线，%.3fs 前更新:" % s["_age_s"])
        print(json.dumps({k: v for k, v in s.items() if not k.startswith("_")},
                         ensure_ascii=False, indent=1)[:1200])
