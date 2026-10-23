"""D3 构成① 实机验收（CufBridge 0.6.8 真拾取语义 + probe_loot 取证）。

流程：连游戏 → 版本核对（probe_loot.mod_version）→ 扫描找 item →
probe_loot 取证真实 loot API 面 → 按取证结果执行 pickup →
判定真拾取（item_acquired/semantics=acquired）或诚实降级（item_remove）。

运行：py -3.12 tests/live_probe_loot.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

PORT = 20770
OUT = Path(__file__).resolve().parents[1] / "logs" / "live_probe_loot"


def dump(title, obj):
    print(f"===== {title} =====")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    gc = GameConnector(host="127.0.0.1", port=PORT, response_timeout_s=20.0)
    gc.start()
    print("[1] 等待游戏连入...")
    if not gc.wait_for_game(timeout=90.0):
        print("[FAIL] 游戏未连入（90s 超时）")
        gc.stop()
        sys.exit(1)
    print("[OK] 已连入")

    probe0 = gc.execute_action("probe_loot", {})
    record["version_probe"] = probe0
    mv = probe0.get("mod_version")
    print(f"[2] 版本核对: mod_version={mv} (期望 0.6.9)")
    if mv != "0.6.9":
        print("[FAIL] 游戏侧未加载 0.6.8，终止验收")
        gc.stop()
        sys.exit(1)

    ents = gc.execute_action("scan_entities").get("entities") or []
    print(f"[3] 扫描到 {len(ents)} 个实体：")
    for e in ents:
        print(f"    {e.get('entity_id')} kind={e.get('kind')} "
              f"labels={e.get('labels')} hp={e.get('hp')}")
    items = [e for e in ents if e.get("kind") == "item"]
    record["scan_items"] = [e.get("entity_id") for e in items]

    if not items:
        print("[4] 当前区域无 item——请移动到战利品容器附近后重跑本脚本")
        record["verdict"] = "no_item_in_area"
        (OUT / "probe_receipt.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        gc.stop()
        return

    tid = items[0]["entity_id"]
    print(f"[4] probe_loot 取证目标 {tid} ...")
    probe = gc.execute_action("probe_loot", {"target_id": tid})
    dump("probe_loot 取证", probe)
    record["probe"] = probe

    print(f"[5] pickup {tid} ...")
    resp = gc.execute_action("player_pickup", {"target_id": tid})
    dump("pickup 回执", resp)
    record["pickup"] = resp
    time.sleep(2.0)

    still = any(e.get("entity_id") == tid
                for e in (gc.execute_action("scan_entities").get("entities") or []))
    evs = gc.execute_action("get_events").get("events") or []
    record["events_after"] = evs
    record["still_present"] = still
    acq = [e for e in evs if e.get("kind") == "item_acquired"]
    rem = [e for e in evs if e.get("kind") == "item_remove"]
    print(f"[6] 再扫描仍在={still}  item_acquired={len(acq)}  item_remove={len(rem)}")

    reason = resp.get("reason")
    if reason == "item_acquired":
        ok = (resp.get("semantics") == "acquired"
              and resp.get("verified_count", 0) >= 1
              and len(acq) >= 1 and not still)
        verdict = "TRUE_PICKUP_PASS" if ok else "TRUE_PICKUP_FAIL"
    elif reason == "item_removed":
        ok = len(rem) >= 1 and not still
        verdict = "HONEST_REMOVE_PASS" if ok else "HONEST_REMOVE_FAIL"
    else:
        verdict = "PICKUP_REJECTED"
    record["verdict"] = verdict
    print(f"[7] 验收判定: {verdict}")
    print("    取证要点: loot_list_readable=%s tsys_methods=%s" %
          (probe.get("loot_list_readable"),
           json.dumps(probe.get("tsys_methods") or {}, ensure_ascii=False)))

    (OUT / "probe_receipt.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[8] 回执已归档 {OUT / 'probe_receipt.json'}")
    gc.stop()


if __name__ == "__main__":
    main()
