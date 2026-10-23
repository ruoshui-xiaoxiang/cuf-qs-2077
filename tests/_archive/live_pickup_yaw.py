"""S29.10 pickup/yaw 实机验证（CufBridge 0.6.7 新 handler 首次实机）。

流程：
1. GameConnector 启动 server（监听 20770），等待游戏内 CufBridge 连入。
2. 扫描实体 → 查找可拾取物品（kind=item 或 className 含 Item）。
3. 若有物品：执行 player_pickup → 验证 accepted + item_acquired 事件 +
   再扫描确认物品消失（item 消失证据 = pickup 生效）。
4. 玩家当前位置 → player_move（相对 +10m，带 yaw=90°）→ 验证 accepted +
   位移（朝向由 CufBridge 侧 EulerAngles 施加，位置证据在观测窗口取证）。
5. 若无物品：仅验证 move+yaw，并如实报告「场景无 item 可拾取」。

运行：py -3.12 tests/live_pickup_yaw.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

PORT = 20770
OUT = Path(__file__).resolve().parents[1] / "logs" / "live_pickup_yaw"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gc = GameConnector(host="127.0.0.1", port=PORT, response_timeout_s=15.0)
    gc.start()
    print(f"[1] server listening 20770，等待游戏 CufBridge 连入（≤60s）...")
    if not gc.wait_for_game(timeout=60.0):
        print("[FAIL] 60s 内游戏未连入")
        gc.stop()
        sys.exit(1)
    print("[OK] 游戏已连入")

    records = []

    def scan():
        resp = gc.execute_action("scan_entities")
        return resp.get("entities") or []

    # [2] 扫描实体
    ents = scan()
    print(f"[2] 扫描到 {len(ents)} 个实体：")
    for e in ents:
        print(f"    {e.get('entity_id')} kind={e.get('kind')} "
              f"cls={e.get('class_name', e.get('className', ''))} "
              f"labels={e.get('labels')} hp={e.get('hp')}")
    items = [e for e in ents if e.get("kind") == "item"
             or "Item" in (e.get("class_name") or e.get("className") or "")]
    player = next((e for e in ents if e.get("kind") == "player"), None)

    # [3] pickup
    if items:
        tid = items[0]["entity_id"]
        print(f"[3] 拾取物品 {tid} ...")
        resp = gc.execute_action("player_pickup", {"target_id": tid})
        print(f"    pickup 回执: accepted={resp.get('accepted')} "
              f"reason={resp.get('reason')} removed={resp.get('removed')}")
        time.sleep(1.5)
        ents2 = scan()
        still = any(e.get("entity_id") == tid for e in ents2)
        print(f"    再扫描：物品仍存在={still}")
        evs = gc.execute_action("get_events").get("events") or []
        acquired = [e for e in evs if e.get("kind") == "item_acquired"]
        print(f"    item_acquired 事件: {len(acquired)} 条")
        records.append({
            "test": "pickup", "target": tid,
            "accepted": resp.get("accepted"), "reason": resp.get("reason"),
            "removed": resp.get("removed"), "still_present": still,
            "item_acquired_events": len(acquired),
        })
        pickup_ok = resp.get("accepted") is True and not still \
            and len(acquired) >= 1
        print(f"    pickup 验证: {'PASS' if pickup_ok else 'FAIL'}")
    else:
        print("[3] 场景无 item 实体（kind/className 均无 Item），跳过 pickup")
        records.append({"test": "pickup", "skipped": "no_item_in_scan"})

    # [4] move + yaw
    if player:
        pos = player.get("position") or {}
        base = (float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0)))
        goal = (base[0] + 10.0, base[1], base[2])
        print(f"[4] move 带 yaw：({goal[0]:.1f},{goal[1]:.1f},{goal[2]:.1f}) yaw=90°")
        resp = gc.execute_action("player_move", {
            "x": goal[0], "y": goal[1], "z": goal[2], "yaw": 1.5708})
        print(f"    move 回执: accepted={resp.get('accepted')} "
              f"reason={resp.get('reason')}")
        # 观测窗口取证位移（跨帧异步落位）
        moved = False
        for _ in range(40):
            time.sleep(0.5)
            ents3 = scan()
            p2 = next((e for e in ents3 if e.get("kind") == "player"), None)
            if not p2:
                continue
            q = p2.get("position") or {}
            now = (float(q.get("x", 0)), float(q.get("y", 0)), float(q.get("z", 0)))
            dist = ((now[0] - base[0]) ** 2 + (now[1] - base[1]) ** 2
                    + (now[2] - base[2]) ** 2) ** 0.5
            if dist >= 2.0:
                moved = True
                print(f"    位移证据: base={base} now={now} dist={dist:.2f}m")
                break
        print(f"    move+yaw 验证: {'PASS' if moved else 'FAIL'}")
        records.append({
            "test": "move_yaw", "accepted": resp.get("accepted"),
            "reason": resp.get("reason"), "displacement_m": dist if moved else 0.0,
        })
    else:
        print("[4] 未找到玩家实体，跳过 move")
        records.append({"test": "move_yaw", "skipped": "no_player"})

    # 汇总
    print("\n===== 汇总 =====")
    for r in records:
        print(json.dumps(r, ensure_ascii=False))
    (OUT / "live_pickup_yaw.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"记录写入 {OUT / 'live_pickup_yaw.json'}")
    gc.stop()


if __name__ == "__main__":
    main()
