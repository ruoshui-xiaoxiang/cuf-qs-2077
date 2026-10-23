"""D3 构成① 实机验收（螺旋搜索版）：以回归点为中心逐环传送搜索战利品容器。

每点 3s 沉降 + 双重扫描（应对传送后流式加载延迟），找到 item 即
probe_loot 取证 + pickup 验证，最后回回归点。

运行：py -3.12 tests/live_probe_loot_spiral.py
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

PORT = 20770
OUT = Path(__file__).resolve().parents[1] / "logs" / "live_probe_loot"
RINGS = [
    (10, 0), (-10, 0), (0, 10), (0, -10),
    (10, 10), (-10, 10), (10, -10), (-10, -10),
    (20, 0), (-20, 0), (0, 20), (0, -20),
]
SETTLE_S = 3.0


def scan(gc, retries=4):
    last = None
    for i in range(retries):
        try:
            return gc.execute_action("scan_entities").get("entities") or []
        except Exception as e:
            last = e
            print(f"    scan 失败（{e}），{(i + 1) * 2}s 后重试")
            time.sleep(2.0)
    raise last


def player_pos(ents):
    for e in ents:
        if e.get("kind") == "player":
            p = e.get("position") or {}
            return (p.get("x"), p.get("y"), p.get("z"))
    return None


def kind_counts(ents):
    out = {}
    for e in ents:
        k = e.get("kind") or "?"
        out[k] = out.get(k, 0) + 1
    return out


def dist3(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "points": []}
    gc = GameConnector(host="127.0.0.1", port=PORT, response_timeout_s=20.0)
    gc.start()
    if not gc.wait_for_game(timeout=90.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    probe0 = gc.execute_action("probe_loot", {})
    if probe0.get("mod_version") != "0.6.9":
        print("[FAIL] 版本不符:", probe0.get("mod_version"))
        gc.stop()
        sys.exit(1)
    print("[OK] CufBridge 0.6.8 在线")

    home = player_pos(scan(gc))
    if home is None:
        print("[FAIL] 读不到玩家坐标")
        gc.stop()
        sys.exit(1)
    record["regression_point"] = home
    print(f"[1] 回归点: {home}")

    item_tid = None
    found_at = None
    for i, (dx, dy) in enumerate(RINGS):
        tx, ty, tz = home[0] + dx, home[1] + dy, home[2]
        gc.execute_action("player_move", {"x": tx, "y": ty, "z": tz})
        time.sleep(SETTLE_S)
        ents = scan(gc)
        time.sleep(1.0)
        ents2 = scan(gc)
        if len(ents2) > len(ents):
            ents = ents2
        items = [e for e in ents if e.get("kind") == "item"]
        kc = kind_counts(ents)
        print(f"[2.{i}] ({tx:.0f},{ty:.0f}) 实体{len(ents)} {kc}")
        for u in [e for e in ents if e.get("kind") == "unknown"][:6]:
            print(f"      unknown: {u.get('entity_id')} labels={u.get('labels')}")
        record["points"].append({"at": [tx, ty], "ents": len(ents),
                                 "kinds": kc})
        if items:
            item_tid = items[0]["entity_id"]
            found_at = (tx, ty)
            record["items_found"] = [
                {"entity_id": e.get("entity_id"), "labels": e.get("labels"),
                 "position": e.get("position")} for e in items]
            break

    record["item_tid"] = item_tid
    record["found_at"] = found_at
    if item_tid is None:
        print("[3] 螺旋搜索未发现 item")
    else:
        print(f"[3] 发现目标 {item_tid} @ {found_at}，取证中")
        probe = gc.execute_action("probe_loot", {"target_id": item_tid})
        record["probe"] = probe
        print("    class_name=%s loot_list_readable=%s loot_count=%s" %
              (probe.get("class_name"), probe.get("loot_list_readable"),
               probe.get("loot_count")))
        print("    tsys_methods=%s" %
              json.dumps(probe.get("tsys_methods") or {}, ensure_ascii=False))
        print("    entity_methods=%s" %
              json.dumps(probe.get("entity_methods") or {}, ensure_ascii=False))

        resp = gc.execute_action("player_pickup", {"target_id": item_tid})
        record["pickup"] = resp
        print(f"    pickup: accepted={resp.get('accepted')} "
              f"reason={resp.get('reason')} semantics={resp.get('semantics')} "
              f"verified_count={resp.get('verified_count')}")
        for a in (resp.get("acquired_items") or [])[:5]:
            print(f"      - {a.get('name')} qty {a.get('qty_before')}->"
                  f"{a.get('qty_after')} verified={a.get('verified')} "
                  f"add_ret={a.get('add_ret')}")
        time.sleep(2.0)
        still = any(e.get("entity_id") == item_tid for e in scan(gc))
        evs = gc.execute_action("get_events").get("events") or []
        record["events_after"] = evs
        record["still_present"] = still
        acq = [e for e in evs if e.get("kind") == "item_acquired"]
        rem = [e for e in evs if e.get("kind") == "item_remove"]
        print(f"    再扫描仍在={still} item_acquired={len(acq)} "
              f"item_remove={len(rem)}")
        reason = resp.get("reason")
        if reason == "item_acquired":
            ok = (resp.get("semantics") == "acquired"
                  and (resp.get("verified_count") or 0) >= 1
                  and len(acq) >= 1 and not still)
            verdict = "TRUE_PICKUP_PASS" if ok else "TRUE_PICKUP_FAIL"
        elif reason == "item_removed":
            verdict = ("HONEST_REMOVE_PASS"
                       if len(rem) >= 1 and not still else "HONEST_REMOVE_FAIL")
        else:
            verdict = "PICKUP_REJECTED"
        record["verdict"] = verdict
        print(f"    验收判定: {verdict}")

    print("[4] 传送回回归点 ...")
    gc.execute_action("player_move",
                      {"x": home[0], "y": home[1], "z": home[2]})
    time.sleep(SETTLE_S)
    back = player_pos(scan(gc))
    record["back_pos"] = back
    if back and all(v is not None for v in back):
        d = dist3(back, home)
        record["return_dist_m"] = d
        print(f"    距回归点 {d:.2f}m {'OK' if d <= 2.5 else 'WARN'}")

    (OUT / "probe_spiral_receipt.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[5] 回执已归档 {OUT / 'probe_spiral_receipt.json'}")
    gc.stop()


if __name__ == "__main__":
    main()
