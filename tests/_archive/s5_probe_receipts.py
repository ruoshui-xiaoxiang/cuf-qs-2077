"""S5 live probe: dump full receipts (with attempts) for player_attack / player_move.

Usage:
    py tests\\s5_probe_receipts.py [port] [mode] [radius]

mode: both | attack | move | scan | debug | raw | probe | blackwall  (default: both)
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector


def dump(title, obj):
    print(f"=== {title} ===")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 20770
    mode = sys.argv[2] if len(sys.argv) > 2 else "both"
    radius = float(sys.argv[3]) if len(sys.argv) > 3 else None
    conn = GameConnector(host="127.0.0.1", port=port)
    conn.start()
    deadline = time.time() + 90
    while time.time() < deadline and conn.game_info is None:
        time.sleep(0.5)
    if conn.game_info is None:
        print("FAIL: game did not connect within 90s")
        conn.stop()
        return 1
    dump("hello", conn.game_info)

    scan_args = {"radius": radius} if radius else {}
    scan = conn.execute_action("scan_entities", scan_args, timeout=10)
    if not isinstance(scan, dict):
        print("FAIL: scan_entities bad response")
        conn.stop()
        return 1
    entities = scan.get("entities") or []
    player = next((e for e in entities if e.get("kind") == "player"), None)
    npcs = [e for e in entities if e.get("kind") == "npc"]
    dump("scan", {"count": scan.get("count"), "player": player, "npc_count": len(npcs)})

    if mode == "raw":
        dump("scan_all_entities", scan)
        conn.stop()
        return 0

    if mode == "probe":
        info = conn.execute_action("probe_move_apis", {}, timeout=10)
        dump("probe_move_apis", info)
        fns = conn.execute_action("list_fns", {}, timeout=10)
        dump("list_fns", fns)
        ctors = conn.execute_action("probe_constructors", {}, timeout=15)
        dump("probe_constructors", ctors)
        conn.stop()
        return 0

    if mode == "blackwall":
        if player is None:
            print("FAIL: player not in scan (game paused / menu / loading?)")
            conn.stop()
            return 1
        safe = conn.execute_action("probe_blackwall", {}, timeout=10)
        dump("probe_blackwall enumerate-only", safe)
        apply = conn.execute_action("probe_blackwall", {"apply_to_player": True}, timeout=15)
        dump("probe_blackwall apply_to_player", apply)
        inject = conn.execute_action("probe_blackwall", {"direct_inject": True}, timeout=15)
        dump("probe_blackwall direct_inject", inject)
        conn.stop()
        return 0

    if player is None:
        print("FAIL: player not in scan (game paused / menu / loading?)")
        conn.stop()
        return 1
    if not npcs:
        print("FAIL: no npc in scan range")
        conn.stop()
        return 1

    npc = npcs[0]
    npc_pos = npc.get("position") or {}

    if mode == "scan":
        dump("npc_status", npc)
        conn.stop()
        return 0

    if mode == "debug":
        dump("npc_status", npc)
        dbg = conn.execute_action("attack_debug", timeout=10)
        dump("attack_debug", dbg)
        conn.stop()
        return 0

    if mode in ("both", "move"):
        goal = {
            "x": npc_pos.get("x", 0) + 1.5,
            "y": npc_pos.get("y", 0),
            "z": npc_pos.get("z", 0),
        }
        resp = conn.execute_action("player_move", goal, timeout=10)
        dump("player_move receipt", resp)
        time.sleep(1.0)

    if mode in ("both", "attack"):
        resp = conn.execute_action(
            "player_attack",
            {"target_id": npc.get("entity_id"), "damage": 10},
            timeout=10,
        )
        dump("player_attack receipt", resp)

    conn.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
