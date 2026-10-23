"""定向核验：按候选记录名查玩家背包数量（只读，0.6.10 probe_inventory_check）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector

CANDIDATES = [
    "Items.Preset_Saratoga",
    "Items.Preset_Saratoga_Default",
    "Items.Preset_Saratoga_Military",
    "Items.Preset_Saratoga_Neon",
    "Items.Preset_Saratoga_Legendary",
    "Items.Saratoga",
    "Items.HauntedCyberdeck_Legendary",
]


def main():
    gc = GameConnector(host="127.0.0.1", port=20770, response_timeout_s=20.0)
    gc.start()
    if not gc.wait_for_game(timeout=90.0):
        print("[FAIL] 游戏未连入")
        gc.stop()
        sys.exit(1)
    resp = gc.execute_action("probe_inventory_check",
                             {"candidates": CANDIDATES})
    mv = resp.get("mod_version")
    print(f"版本: {mv}（期望 0.6.11） 玩家可用={resp.get('player_available')} "
          f"tsys={resp.get('tsys_available')}")
    hits = []
    for rec in resp.get("results") or []:
        qty = rec.get("qty")
        flag = ""
        if isinstance(qty, (int, float)) and qty > 0:
            flag = "  <-- 背包持有"
            hits.append(rec)
        print(f"  {rec.get('record')}: tdbid={rec.get('tdbid_ok')} "
              f"qty={qty}{flag}")
        for v in rec.get("variants") or []:
            detail = ("qty=%s" % v.get("qty") if "qty" in v
                      else ("ret=%s(%s)" % (v.get("ret"), v.get("ret_type"))
                            if v.get("ok") else "ERR: %s" % v.get("err")))
            print(f"      [{v.get('form')}] ok={v.get('ok')} {detail}")
    print("\n判定: %s" % (
        "拾取确认（%d 条记录数量>0）" % len(hits) if hits
        else "候选记录均未持有（记录名不中或拾取未入账）"))
    (Path(__file__).resolve().parents[1] / "logs" / "live_probe_loot"
     / "inventory_check.json").write_text(
        json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    gc.stop()


if __name__ == "__main__":
    main()
