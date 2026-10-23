"""S29.10 攻击 NPC 制造掉落物 + pickup 实机验证。

连游戏 → 攻击最近 NPC → 观测击杀（hp 归零/实体消失）→ 扫描掉落物
（item）→ 有则 pickup 验证（消失 + item_acquired 事件）→ 汇总。

运行：py -3.12 tests/live_attack_pickup.py
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

    def show(tag):
        ents = scan()
        print(f"[{tag}] {len(ents)} 实体：")
        for e in ents:
            print(f"    {e.get('entity_id')} kind={e.get('kind')} "
                  f"cls={e.get('class_name', e.get('className', ''))} "
                  f"labels={e.get('labels')} hp={e.get('hp')}")
        return ents

    ents = show("2 攻击前")
    npcs = [e for e in ents if e.get("kind") == "npc"]
    if not npcs:
        print("[FAIL] 无 NPC 可攻击")
        gc.stop()
        sys.exit(1)
    tid = npcs[0]["entity_id"]
    print(f"[3] 攻击 {tid} ...")
    resp = gc.execute_action("player_attack", {"target_id": tid})
    print(f"    攻击回执: accepted={resp.get('accepted')} "
          f"reason={resp.get('reason')} hp_before={resp.get('hp_before')} "
          f"hp_after={resp.get('hp_after')}")

    # 观测击杀（连续攻击直至 hp 归零/消失）
    dead = False
    for i in range(6):
        time.sleep(1.0)
        ents = show(f"4.{i} 攻击后")
        alive = [e for e in ents if e.get("entity_id") == tid]
        if not alive:
            print(f"    NPC {tid} 已消失（击杀完成）")
            dead = True
            break
        hp = alive[0].get("hp")
        print(f"    {tid} hp={hp}")
        if isinstance(hp, (int, float)) and hp <= 0:
            dead = True
            break
        if not dead:
            gc.execute_action("player_attack", {"target_id": tid})
    print(f"    击杀: {'PASS' if dead else 'FAIL'}")

    # [5] 扫描掉落物
    time.sleep(1.5)
    ents = show("5 掉落物")
    items = [e for e in ents if e.get("kind") == "item"
             or "Item" in (e.get("class_name") or e.get("className") or "")]

    records = [{"test": "attack", "target": tid,
                "accepted": resp.get("accepted"), "killed": dead}]
    if items:
        itid = items[0]["entity_id"]
        print(f"[6] 拾取掉落物 {itid} ...")
        resp2 = gc.execute_action("player_pickup", {"target_id": itid})
        print(f"    pickup 回执: accepted={resp2.get('accepted')} "
              f"reason={resp2.get('reason')} removed={resp2.get('removed')}")
        time.sleep(1.5)
        still = any(e.get("entity_id") == itid for e in scan())
        evs = gc.execute_action("get_events").get("events") or []
        acquired = [e for e in evs if e.get("kind") == "item_acquired"]
        print(f"    再扫描仍在={still} item_acquired 事件={len(acquired)}")
        ok = resp2.get("accepted") is True and not still and len(acquired) >= 1
        print(f"    pickup 验证: {'PASS' if ok else 'FAIL'}")
        records.append({"test": "pickup", "target": itid,
                        "accepted": resp2.get("accepted"),
                        "reason": resp2.get("reason"),
                        "removed": resp2.get("removed"),
                        "still_present": still,
                        "item_acquired_events": len(acquired)})
    else:
        print("[6] 击杀后无掉落物（item 未出现）")
        records.append({"test": "pickup", "skipped": "no_drop"})

    print("\n===== 汇总 =====")
    for r in records:
        print(json.dumps(r, ensure_ascii=False))
    (OUT / "live_attack_pickup.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    gc.stop()


if __name__ == "__main__":
    main()
