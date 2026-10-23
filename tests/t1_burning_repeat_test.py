"""t1_burning_repeat_test.py — 连续燃烧验证。

对同一 NPC 多次施加 variant='burning'，验证累积伤害是否足以击杀目标。
记录每次施加后的状态确认、HP 轨迹、实体存续。
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w0.game_connector import GameConnector

EXCLUDE_EIDS = {"EID:1"}
REPEAT_COUNT = 5
ATTACK_INTERVAL_S = 12.0
SCAN_INTERVAL = 2.0
TRACK_SECONDS = 90.0
STATUS_CHECK_DELAY_S = 3.0


def scan(conn):
    resp = conn.call_raw("scan_entities", timeout=15.0)
    if not resp.get("ok"):
        raise RuntimeError(f"scan failed: {resp.get('error')}")
    return resp.get("data") or {}


def check_status(conn, target_id):
    r = conn.call_raw("check_status_effect", {"target_id": target_id}, timeout=10.0)
    if not r.get("ok"):
        return [], r
    data = r.get("data") or {}
    return data.get("effects") or [], r


def burning_present(effects):
    return any(e.get("name") == "burning" and e.get("present") for e in effects)


def pick_target(ents):
    candidates = []
    for e in ents:
        eid = e.get("entity_id", "")
        if e.get("kind") != "npc":
            continue
        if eid in EXCLUDE_EIDS:
            continue
        if (e.get("hp") or 0) <= 0:
            continue
        candidates.append(e)
    if not candidates:
        return None
    candidates.sort(key=lambda e: -(e.get("hp") or 0))
    return candidates[0]


def main():
    out_dir = ROOT / "logs" / "t1_burning"
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"test": "T1_burning_repeat", "started_at": datetime.now().isoformat(), "steps": []}
    exit_code = 0

    def step(name, data):
        entry = {"t": datetime.now().isoformat(), "step": name, "data": data}
        record["steps"].append(entry)
        print(f"[T1-R] {name}: {json.dumps(data, ensure_ascii=False)}")

    conn = GameConnector.from_config(ROOT / "config" / "experiment_config.json")
    conn.start()
    print("[T1-R] 等待游戏接入...")
    if not conn.wait_for_game(timeout=20.0):
        print("[FAIL] 游戏未接入")
        conn.stop()
        return 1
    try:
        r = conn.call_raw("set_attack_variant_policy", {"enabled": True}, timeout=10.0)
        step("version_probe", r)
        if not r.get("ok"):
            print("[FAIL] variant 机制不可用")
            exit_code = 1
            return exit_code

        data = scan(conn)
        ents = data.get("entities", [])
        step("scan_before", {"count": len(ents), "entities": [
            {"eid": e.get("entity_id"), "kind": e.get("kind"),
             "labels": e.get("labels"), "hp": e.get("hp")} for e in ents]})
        target = pick_target(ents)
        if target is None:
            print("[FAIL] 无 NPC 目标")
            exit_code = 1
            return exit_code
        tid = target["entity_id"]
        initial_hp = target.get("hp")
        step("target_selected", {"eid": tid, "hp": initial_hp})

        # 连续施加 burning
        for i in range(REPEAT_COUNT):
            r = conn.call_raw("player_attack", {"target_id": tid, "variant": "burning"},
                              timeout=15.0)
            step(f"attack_{i+1}", r)
            if not r.get("ok"):
                print(f"[FAIL] 第 {i+1} 次攻击调用失败")
                exit_code = 1
                return exit_code
            atk = r.get("data") or {}
            if not atk.get("accepted") or atk.get("reason") != "status_effect_applied":
                print(f"[FAIL] 第 {i+1} 次攻击未应用燃烧：{atk.get('reason')}")
                exit_code = 1
                return exit_code
            if i < REPEAT_COUNT - 1:
                time.sleep(ATTACK_INTERVAL_S)

        # 最终追踪
        print(f"[T1-R] 开始最终追踪 {tid}（{TRACK_SECONDS:.0f}s）...")
        t0 = time.monotonic()
        seq = []
        status_confirmed = False
        hp_decreased = False
        disappeared = False
        while time.monotonic() - t0 < TRACK_SECONDS:
            time.sleep(SCAN_INTERVAL)
            elapsed = time.monotonic() - t0
            try:
                d = scan(conn)
            except Exception as ex:
                seq.append({"elapsed": round(elapsed, 1), "error": str(ex)})
                continue
            ent = next((e for e in d.get("entities", []) if e.get("entity_id") == tid), None)
            hp = ent.get("hp") if ent else None
            present = ent is not None
            seq.append({"elapsed": round(elapsed, 1), "present": present, "hp": hp})
            if present and initial_hp is not None and hp is not None and hp < initial_hp - 0.001:
                hp_decreased = True
            if not present and elapsed > 5.0:
                disappeared = True

            # 每 10 秒确认一次状态
            if int(elapsed) % 10 == 0 and int(elapsed) > 0:
                effects, _ = check_status(conn, tid)
                if burning_present(effects):
                    status_confirmed = True

        step("hp_track", seq)

        final_data = scan(conn)
        final_ent = next((e for e in final_data.get("entities", []) if e.get("entity_id") == tid), None)
        final_hp = final_ent.get("hp") if final_ent else None
        killed = disappeared
        step("summary", {
            "target_id": tid,
            "initial_hp": initial_hp,
            "final_hp": final_hp,
            "hp_decreased": hp_decreased,
            "status_confirmed_any": status_confirmed,
            "disappeared": disappeared,
            "killed": killed,
            "attack_count": REPEAT_COUNT,
        })
        if killed:
            print(f"[PASS] 连续 {REPEAT_COUNT} 次燃烧击杀目标")
        else:
            print(f"[RESULT] 未击杀：initial={initial_hp}, final={final_hp}, "
                  f"hp_decreased={hp_decreased}, status_confirmed={status_confirmed}")
    finally:
        conn.stop()
        record["finished_at"] = datetime.now().isoformat()
        log_file = out_dir / f"t1_repeat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        log_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[T1-R] 结果已落盘: {log_file}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
