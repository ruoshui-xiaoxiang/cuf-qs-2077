r"""S5 实机验收：CufBridge 0.6.4 player_attack / player_move 在机闭环。

前置：游戏已启动（或将启动），运行 0.6.4，CufBridge 会自动重连到本脚本监听端口。
流程：hello 校验 → list_fns 校验 → 扫描找 NPC → move 到 NPC 旁 → attack 该 NPC → 打印事件窗口。
用法: py tests\s5_livetest.py [port]   （默认 20770）
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w0.game_connector import GameConnector
from w3.action_executor import ActionExecutor


def show(title, obj):
    print("\n=== %s ===" % title)
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 20770
    conn = GameConnector(host="127.0.0.1", port=port)
    conn.start()
    print("listening on %d, waiting for game connection..." % port)
    deadline = time.time() + 90.0
    while time.time() < deadline:
        if conn.game_info:
            break
        time.sleep(0.5)
    if not conn.game_info:
        print("FAIL: no game connection within 90s")
        conn.stop()
        return 1
    show("hello", conn.game_info)
    if str(conn.game_info.get("mod_version", "")) != "0.6.4":
        print("WARNING: mod_version != 0.6.4, game may still run old mod (full restart required)")

    try:
        fns = conn.execute_action("list_fns")
        show("list_fns", fns)
        fn_list = fns if isinstance(fns, list) else []
        if not all(n in fn_list for n in ("player_attack", "player_move", "probe_blackwall")):
            print("FAIL: player_attack / player_move / probe_blackwall not registered")
            return 1

        ex = ActionExecutor.from_config(conn)

        scan = conn.execute_action("scan_entities")
        ents = scan.get("entities", []) if isinstance(scan, dict) else []
        player = next((e for e in ents if e.get("kind") == "player"), None)
        npcs = [e for e in ents if e.get("kind") == "npc"]
        show("scan", {"count": len(ents), "player": player, "npc_sample": npcs[:5]})
        if player is None:
            print("FAIL: player entity not found")
            return 1
        if not npcs:
            print("FAIL: no npc in scan range, move to a populated area and retry")
            return 1

        target = npcs[0]
        tpos = target.get("position") or {}
        goal = {"x": tpos.get("x", 0) + 1.5, "y": tpos.get("y", 0), "z": tpos.get("z", 0)}

        move_res = ex.execute("move", goal)
        show("move result", move_res)

        attack_res = ex.execute("attack", {"target_id": target.get("entity_id")})
        show("attack result", attack_res)

        ev = conn.execute_action("get_events")
        show("event window", ev)

        print("\nacceptance summary: move=%s attack=%s" % (move_res.get("status"), attack_res.get("status")))
        return 0
    finally:
        conn.stop()


if __name__ == "__main__":
    sys.exit(main())
