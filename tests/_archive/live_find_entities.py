"""S29.10 平面坐标实体搜索（保持 z 高度，8 方向小步移动）。

策略：从玩家当前位置出发，保持地面高度 z 不变，沿 8 方向各移动 40m，
逐点传送+扫描，找到首个含实体（非 player）的点即停；穿地检测
（传送后 z 与目标 z 偏差 >20m 视为异常，回退原位）。

运行：py -3.12 tests/live_find_entities.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

PORT = 20770
STEP = 40.0
OUT = Path(__file__).resolve().parents[1] / "logs" / "live_pickup_yaw"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gc = GameConnector(host="127.0.0.1", port=PORT, response_timeout_s=15.0)
    gc.start()
    print("[1] 等待游戏连入...")
    if not gc.wait_for_game(timeout=60.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    print("[OK] 已连入")

    def scan():
        resp = gc.execute_action("scan_entities")
        return resp.get("entities") or []

    def player_pos():
        for e in scan():
            if e.get("kind") == "player":
                p = e.get("position") or {}
                return (float(p.get("x", 0)), float(p.get("y", 0)),
                        float(p.get("z", 0)))
        return None

    base = player_pos()
    if base is None:
        print("[FAIL] 未找到玩家")
        gc.stop()
        sys.exit(1)
    print(f"[2] 起始位置: {base}（z={base[2]:.1f} 为地面高度基准）")

    # 8 方向
    dirs = [(1, 0), (1, 1), (0, 1), (-1, 1),
            (-1, 0), (-1, -1), (0, -1), (1, -1)]
    found = False
    for i, (dx, dy) in enumerate(dirs):
        tx = base[0] + dx * STEP
        ty = base[1] + dy * STEP
        tz = base[2]
        print(f"[3.{i}] 传送 ({tx:.0f}, {ty:.0f}, {tz:.0f}) ...")
        resp = gc.execute_action("player_move", {
            "x": tx, "y": ty, "z": tz, "yaw": 0.0})
        time.sleep(1.2)
        now = player_pos()
        if now is None:
            print("    !! 位置读取失败（可能加载中/穿地），尝试回退")
            gc.execute_action("player_move", {
                "x": base[0], "y": base[1], "z": base[2], "yaw": 0.0})
            continue
        dz = abs(now[2] - tz)
        if dz > 20.0:
            print(f"    !! 穿地检测：z 从 {tz:.1f} -> {now[2]:.1f}（偏差 {dz:.1f}m），回退原位")
            gc.execute_action("player_move", {
                "x": base[0], "y": base[1], "z": base[2], "yaw": 0.0})
            continue
        ents = [e for e in scan() if e.get("kind") != "player"]
        if ents:
            print(f"    !! 发现 {len(ents)} 个实体：")
            for e in ents:
                print(f"       {e.get('entity_id')} kind={e.get('kind')} "
                      f"cls={e.get('class_name', e.get('className', ''))} "
                      f"labels={e.get('labels')} hp={e.get('hp')}")
            (OUT / "live_find_entities.json").write_text(
                sys.intern(f"[{{\"pos\": {list(now)}, \"entities\": {len(ents)}}}]"),
                encoding="utf-8")
            found = True
            break
        print(f"    空（z={now[2]:.1f}）")

    if not found:
        print("[4] 8 方向 40m 内均无实体（玩家保持在起始位置附近）")
        gc.execute_action("player_move", {
            "x": base[0], "y": base[1], "z": base[2], "yaw": 0.0})
    gc.stop()


if __name__ == "__main__":
    main()
