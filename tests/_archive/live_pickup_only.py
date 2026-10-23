"""S29.10 pickup 实机验证（仅拾取，不移动玩家）。

连游戏 → 扫描当前区域 → 若有 item：pickup 验证（消失+事件）；
若无 item：报告区域实体清单（供判断下一步，如攻击制造掉落物）。

运行：py -3.12 tests/live_pickup_only.py
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
    print("[1] 等待游戏连入...")
    if not gc.wait_for_game(timeout=60.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    print("[OK] 已连入")

    def scan():
        resp = gc.execute_action("scan_entities")
        return resp.get("entities") or []

    ents = scan()
    print(f"[2] 扫描到 {len(ents)} 个实体：")
    for e in ents:
        print(f"    {e.get('entity_id')} kind={e.get('kind')} "
              f"cls={e.get('class_name', e.get('className', ''))} "
              f"labels={e.get('labels')} hp={e.get('hp')}")

    items = [e for e in ents if e.get("kind") == "item"
             or "Item" in (e.get("class_name") or e.get("className") or "")]
    npcs = [e for e in ents if e.get("kind") == "npc"]

    records = []
    if items:
        tid = items[0]["entity_id"]
        print(f"[3] 拾取物品 {tid} ...")
        resp = gc.execute_action("player_pickup", {"target_id": tid})
        print(f"    pickup 回执: accepted={resp.get('accepted')} "
              f"reason={resp.get('reason')} removed={resp.get('removed')}")
        time.sleep(1.5)
        still = any(e.get("entity_id") == tid for e in scan())
        evs = gc.execute_action("get_events").get("events") or []
        acquired = [e for e in evs if e.get("kind") == "item_acquired"]
        print(f"    再扫描仍在={still} item_acquired 事件={len(acquired)}")
        ok = resp.get("accepted") is True and not still and len(acquired) >= 1
        print(f"    pickup 验证: {'PASS' if ok else 'FAIL'}")
        records.append({"test": "pickup", "target": tid,
                        "accepted": resp.get("accepted"),
                        "reason": resp.get("reason"),
                        "removed": resp.get("removed"),
                        "still_present": still,
                        "item_acquired_events": len(acquired)})
    else:
        print(f"[3] 当前区域无 item（NPC {len(npcs)} 个）。")
        print("    选项：攻击 NPC 击杀后可能产生掉落物（item），")
        print("    需确认后再执行（攻击会引发通缉风险，目标为非敌对 NPC）。")
        records.append({"test": "pickup", "skipped": "no_item",
                        "npcs": len(npcs),
                        "entity_ids": [e.get("entity_id") for e in npcs]})

    print("\n===== 汇总 =====")
    for r in records:
        print(json.dumps(r, ensure_ascii=False))
    (OUT / "live_pickup_only.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    gc.stop()


if __name__ == "__main__":
    main()
