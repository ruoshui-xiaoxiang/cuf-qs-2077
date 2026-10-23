"""quick_scan.py — 临时实体扫描：起连接器→等游戏接入→scan→打印→退出。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w0.game_connector import GameConnector


def main():
    conn = GameConnector.from_config(ROOT / "config" / "experiment_config.json")
    conn.start()
    print("[scan] 等待游戏接入...")
    if not conn.wait_for_game(timeout=20.0):
        print("[FAIL] 游戏未在 20s 内接入")
        conn.stop()
        return 1
    try:
        resp = conn.call_raw("scan_entities", timeout=15.0)
        ents = resp.get("entities", [])
        player = resp.get("player") or {}
        print(f"[scan] player_pos={player.get('pos')} n_entities={len(ents)}")
        for e in ents:
            print(f"  EID:{e.get('eid')} {str(e.get('name',''))[:30]:30s} "
                  f"tags={e.get('tags','')} hp={e.get('hp')} dist={e.get('dist')}")
    finally:
        conn.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
