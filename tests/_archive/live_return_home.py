"""回归点校准：读当前位置，偏离即传送回回归点并验证（带回执核对）。"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

HOME = (49.77174377441406, 65.8017578125, 35.99559783935547)


def scan(gc):
    return gc.execute_action("scan_entities").get("entities") or []


def player_pos(ents):
    for e in ents:
        if e.get("kind") == "player":
            p = e.get("position") or {}
            return (p.get("x"), p.get("y"), p.get("z"))
    return None


def main():
    gc = GameConnector(host="127.0.0.1", port=20770, response_timeout_s=20.0)
    gc.start()
    if not gc.wait_for_game(timeout=90.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    cur = player_pos(scan(gc))
    print(f"当前位置: {cur}")
    if cur and all(v is not None for v in cur):
        d = math.dist(cur, HOME)
        if d <= 2.5:
            print(f"已在回归点 {d:.2f}m 内，无需操作")
            gc.stop()
            return
        print(f"偏离 {d:.2f}m，执行回归传送")
    resp = gc.execute_action("player_move",
                             {"x": HOME[0], "y": HOME[1], "z": HOME[2]})
    print("回执:", json.dumps({k: resp.get(k) for k in
                               ("accepted", "reason", "start_pos", "end_pos")},
                              ensure_ascii=False))
    time.sleep(4.0)
    back = player_pos(scan(gc))
    print(f"回读位置: {back}")
    if back and all(v is not None for v in back):
        d2 = math.dist(back, HOME)
        print(f"距回归点 {d2:.2f}m {'OK' if d2 <= 2.5 else 'WARN 再次检查'}")
    gc.stop()


if __name__ == "__main__":
    main()
