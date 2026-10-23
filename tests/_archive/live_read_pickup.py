"""读取玩家手动拾取的观测痕迹：事件队列 + 当前扫描 + 背包快照通道状态。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector


def main():
    gc = GameConnector(host="127.0.0.1", port=20770, response_timeout_s=20.0)
    gc.start()
    if not gc.wait_for_game(timeout=60.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    probe0 = gc.execute_action("probe_loot", {})
    print(f"版本: {probe0.get('mod_version')}  "
          f"背包快照: ok={probe0.get('snapshot_before', {}).get('ok')} "
          f"reason={probe0.get('snapshot_before', {}).get('reason')}")
    evs = gc.execute_action("get_events").get("events") or []
    print(f"\n事件队列 {len(evs)} 条（时间正序）：")
    for e in evs:
        print(f"  {e.get('event_id')} kind={e.get('kind')} "
              f"src={e.get('source_entity')} tgt={e.get('target_entity')} "
              f"ts={e.get('game_ts')}")
    ents = gc.execute_action("scan_entities").get("entities") or []
    items = [e for e in ents if e.get("kind") == "item"]
    npcs = [e for e in ents if e.get("kind") == "npc"]
    player = next((e for e in ents if e.get("kind") == "player"), {})
    print(f"\n当前扫描：实体 {len(ents)}（NPC {len(npcs)}，item {len(items)}），"
          f"玩家 hp={player.get('hp')} pos={player.get('position')}")
    for e in items:
        print(f"  item: {e.get('entity_id')} {e.get('labels')} "
              f"@{e.get('position')}")
    (Path(__file__).resolve().parents[1] / "logs" / "live_probe_loot"
     / "manual_pickup_read.json").write_text(
        json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "events": evs, "items": items,
                    "player": player}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    gc.stop()


if __name__ == "__main__":
    main()
