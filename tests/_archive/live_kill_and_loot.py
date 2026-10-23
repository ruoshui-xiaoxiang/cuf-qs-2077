"""D3 构成① 实机验收（多目标取证版，无传送）：
优先拾取现存掉落物（逐个 probe + pickup）；无掉落物时才击杀存活 NPC 制造。
跳过尸体（hp<=0）。全程不移动玩家。

运行：py -3.12 tests/live_kill_and_loot.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

PORT = 20770
OUT = Path(__file__).resolve().parents[1] / "logs" / "live_probe_loot"
MAX_KILLS = 3
MAX_ATTACKS_PER_NPC = 8
MAX_PICKUPS = 3


def scan(gc, retries=4):
    last = None
    for i in range(retries):
        try:
            return gc.execute_action("scan_entities").get("entities") or []
        except Exception as e:
            last = e
            print(f"    scan 失败（{e}），2s 后重试")
            time.sleep(2.0)
    raise last


def probe_and_pickup(gc, record, item):
    tid = item["entity_id"]
    print(f"[P] probe_loot 取证 {tid}（{item.get('labels')}）")
    probe = gc.execute_action("probe_loot", {"target_id": tid})
    rec = {"item": item, "probe": probe}
    print("    class=%s loot_list=%s container_list=%s err=%s" %
          (probe.get("class_name"), probe.get("loot_list_readable"),
           probe.get("container_item_list_readable"),
           probe.get("container_item_list_err")))
    print("    entity_methods=%s" % json.dumps(
        probe.get("entity_methods") or {}, ensure_ascii=False))
    resp = gc.execute_action("player_pickup", {"target_id": tid})
    rec["pickup"] = resp
    print(f"    pickup: accepted={resp.get('accepted')} "
          f"reason={resp.get('reason')} semantics={resp.get('semantics')} "
          f"verified_count={resp.get('verified_count')}")
    for a in (resp.get("acquired_items") or [])[:5]:
        print(f"      - {a.get('name')} qty {a.get('qty_before')}->"
              f"{a.get('qty_after')} verified={a.get('verified')} "
              f"via={a.get('via')} add_ret={a.get('add_ret')}")
    if resp.get("transfer") is not None:
        print(f"    transfer: {json.dumps(resp.get('transfer'), ensure_ascii=False)}")
    time.sleep(2.0)
    still = any(e.get("entity_id") == tid for e in scan(gc))
    evs = gc.execute_action("get_events").get("events") or []
    rec["still_present"] = still
    acq = [e for e in evs if e.get("kind") == "item_acquired"]
    rem = [e for e in evs if e.get("kind") == "item_remove"]
    rec["ev_acquired"] = len(acq)
    rec["ev_remove"] = len(rem)
    print(f"    再扫描仍在={still} item_acquired={len(acq)} item_remove={len(rem)}")
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
    rec["verdict"] = verdict
    print(f"    判定: {verdict}")
    record["pickups"].append(rec)
    return verdict


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "pickups": [],
              "kills": []}
    gc = GameConnector(host="127.0.0.1", port=PORT, response_timeout_s=20.0)
    gc.start()
    if not gc.wait_for_game(timeout=90.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    probe0 = gc.execute_action("probe_loot", {})
    mv = probe0.get("mod_version")
    if mv != "0.6.9":
        print("[FAIL] 版本不符:", mv)
        gc.stop()
        sys.exit(1)
    print(f"[OK] CufBridge {mv} 在线（全程不移动玩家）")

    ents = scan(gc)
    record["player_hp_start"] = next(
        (e.get("hp") for e in ents if e.get("kind") == "player"), None)
    items = [e for e in ents if e.get("kind") == "item"]
    alive_npcs = [e for e in ents if e.get("kind") == "npc"
                  and (e.get("hp") or 0) > 0]
    print(f"[1] 实体 {len(ents)}：存活 NPC {len(alive_npcs)}，"
          f"现存掉落物 {len(items)}")

    pickup_targets = sorted(
        items,
        key=lambda e: 0 if "gameItemDropObject" in (e.get("labels") or [""])
        else 1)[:MAX_PICKUPS]

    kill_idx = 0
    while True:
        for item in pickup_targets:
            probe_and_pickup(gc, record, item)
        ents = scan(gc)
        items = [e for e in ents if e.get("kind") == "item"]
        remaining = [e for e in items
                     if e.get("entity_id") not in
                     {p["item"]["entity_id"] for p in record["pickups"]}]
        if remaining and len(record["pickups"]) < MAX_PICKUPS:
            pickup_targets = remaining[:MAX_PICKUPS - len(record["pickups"])]
            continue
        if kill_idx >= min(MAX_KILLS, len(alive_npcs)):
            break
        if not remaining and len(record["pickups"]) >= 1:
            break
        tgt = alive_npcs[kill_idx]
        kill_idx += 1
        tid = tgt["entity_id"]
        print(f"[K] 击杀 {tid}（hp={tgt.get('hp')}）")
        kill_rec = {"target": tid, "attacks": []}
        record["kills"].append(kill_rec)
        for a in range(MAX_ATTACKS_PER_NPC):
            resp = gc.execute_action("player_attack", {"target_id": tid})
            kill_rec["attacks"].append(
                {"accepted": resp.get("accepted"),
                 "reason": resp.get("reason")})
            print(f"    attack#{a + 1}: {resp.get('reason')}")
            if not resp.get("accepted"):
                break
            time.sleep(5.0)
            ents = scan(gc)
            cur = next((e for e in ents if e.get("entity_id") == tid), None)
            if cur is None or (cur.get("hp") or 0) <= 0:
                kill_rec["dead"] = True
                print("    目标已死亡")
                break
            print(f"    目标 hp={cur.get('hp')}")
        time.sleep(3.0)
        ents = scan(gc)
        items = [e for e in ents if e.get("kind") == "item"]
        pickup_targets = [e for e in items
                          if e.get("entity_id") not in
                          {p["item"]["entity_id"] for p in record["pickups"]}
                          ][:MAX_PICKUPS - len(record["pickups"])]
        if not pickup_targets:
            print("    击杀后无掉落物")
            if kill_idx >= MAX_KILLS:
                break

    ents = scan(gc)
    record["player_hp_end"] = next(
        (e.get("hp") for e in ents if e.get("kind") == "player"), None)
    verdicts = [p["verdict"] for p in record["pickups"]]
    record["overall"] = ("TRUE_PICKUP_PASS" if "TRUE_PICKUP_PASS" in verdicts
                         else "HONEST_REMOVE_PASS" if verdicts and all(
                             v in ("HONEST_REMOVE_PASS",) for v in verdicts)
                         else "INCONCLUSIVE")
    print(f"[END] 总体判定: {record['overall']}（{verdicts}）")
    (OUT / "kill_and_loot_receipt.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ARCHIVE] {OUT / 'kill_and_loot_receipt.json'}")
    gc.stop()


if __name__ == "__main__":
    main()
