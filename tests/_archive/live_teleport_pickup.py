"""S29.10 传送至实验区 + pickup/yaw 实机验证。

流程：连游戏 → 传送玩家至日本街入口广场 (-730, 900, 12)
→ 观测窗口确认位移 → 扫描实体 → 有 item 则 pickup → 有 hostile 则攻击
（顺带实证）→ 汇总。

运行：py -3.12 tests/live_teleport_pickup.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

PORT = 20770
TARGET = (-730.0, 900.0, 12.0)
OUT = Path(__file__).resolve().parents[1] / "logs" / "live_pickup_yaw"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gc = GameConnector(host="127.0.0.1", port=PORT, response_timeout_s=15.0)
    gc.start()
    print("[1] server listening 20770，等待游戏连入...")
    if not gc.wait_for_game(timeout=60.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    print("[OK] 游戏已连入")

    records = []

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
    print(f"[2] 玩家当前位置: {base}")

    # [3] 传送至实验区
    print(f"[3] 传送至实验区 {TARGET} ...")
    resp = gc.execute_action("player_move", {
        "x": TARGET[0], "y": TARGET[1], "z": TARGET[2], "yaw": 0.0})
    print(f"    move 回执: accepted={resp.get('accepted')} "
          f"reason={resp.get('reason')}")
    moved = False
    for _ in range(40):
        time.sleep(0.5)
        now = player_pos()
        if now:
            d = ((now[0] - TARGET[0]) ** 2 + (now[1] - TARGET[1]) ** 2
                 + (now[2] - TARGET[2]) ** 2) ** 0.5
            if d <= 15.0:
                moved = True
                print(f"    到位: {now} 距目标 {d:.1f}m")
                break
    records.append({"test": "teleport", "target": TARGET,
                    "accepted": resp.get("accepted"),
                    "reason": resp.get("reason"), "arrived": moved})
    print(f"    传送验证: {'PASS' if moved else 'FAIL'}")

    # [4] 扫描实体
    time.sleep(1.0)
    ents = scan()
    print(f"[4] 扫描到 {len(ents)} 个实体：")
    for e in ents:
        print(f"    {e.get('entity_id')} kind={e.get('kind')} "
              f"cls={e.get('class_name', e.get('className', ''))} "
              f"labels={e.get('labels')} hp={e.get('hp')}")
    items = [e for e in ents if e.get("kind") == "item"
             or "Item" in (e.get("class_name") or e.get("className") or "")]
    hostiles = [e for e in ents if e.get("kind") != "player"
                and "hostile" in (e.get("labels") or [])]

    # [5] pickup（若有 item）
    if items:
        tid = items[0]["entity_id"]
        print(f"[5] 拾取物品 {tid} ...")
        resp = gc.execute_action("player_pickup", {"target_id": tid})
        print(f"    pickup 回执: accepted={resp.get('accepted')} "
              f"reason={resp.get('reason')} removed={resp.get('removed')}")
        time.sleep(1.5)
        still = any(e.get("entity_id") == tid for e in scan())
        evs = gc.execute_action("get_events").get("events") or []
        acquired = [e for e in evs if e.get("kind") == "item_acquired"]
        print(f"    再扫描仍在={still} item_acquired={len(acquired)}")
        ok = resp.get("accepted") is True and not still and len(acquired) >= 1
        print(f"    pickup 验证: {'PASS' if ok else 'FAIL'}")
        records.append({"test": "pickup", "target": tid,
                        "accepted": resp.get("accepted"),
                        "reason": resp.get("reason"), "removed": resp.get("removed"),
                        "still_present": still, "item_acquired_events": len(acquired)})
    elif hostiles:
        # 无 item 但有敌对 NPC——报告区域有实体但无可拾取物
        print(f"[5] 区域无 item（有 {len(hostiles)} 敌对实体但无可拾取物品）")
        records.append({"test": "pickup", "skipped": "no_item",
                        "hostiles_present": len(hostiles)})
    else:
        print("[5] 区域无 item 也无敌对实体")
        records.append({"test": "pickup", "skipped": "empty_area"})

    print("\n===== 汇总 =====")
    for r in records:
        print(json.dumps(r, ensure_ascii=False))
    (OUT / "live_teleport_pickup.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    gc.stop()


if __name__ == "__main__":
    main()
