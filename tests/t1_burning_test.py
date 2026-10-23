"""t1_burning_test.py — T1 受控燃烧测试。

流程：
1. 版本探针：set_attack_variant_policy 仅存在于 0.6.15+，调用成功即证明机制已加载
2. 扫描实体，挑选普通 NPC 目标（排除 EID:1 强尼·银手，排除玩家/非 NPC/无 HP 实体）
3. 强制 variant='burning' 施加燃烧
4. 延迟调用 check_status_effect 确认燃烧状态（规避 Apply 后立即查询的假阴性）
5. 每 2s 扫描一次、共 40s，记录目标 HP 轨迹与实体存续
6. 全部结果落盘 logs/t1_burning/（磁盘证据纪律）

通过条件（满足其一即通过）：
- 40s 内目标 HP 出现下降；或
- 延迟状态确认检测到 burning 状态为 present；或
- 目标在追踪期间消失（可解释为燃烧致死）。

硬性失败条件：
- player_attack 未返回 reason='status_effect_applied'（说明 0.6.16 未部署或状态效果未挂上）。
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w0.game_connector import GameConnector

EXCLUDE_EIDS = {"EID:1"}  # 强尼·银手（用户确认），不作为攻击目标
SCAN_INTERVAL = 2.0
TRACK_SECONDS = 40.0
STATUS_CHECK_DELAY_S = 3.0
STATUS_CHECK_MILESTONES_S = [10, 20]


def scan(conn):
    resp = conn.call_raw("scan_entities", timeout=15.0)
    if not resp.get("ok"):
        raise RuntimeError(f"scan failed: {resp.get('error')}")
    return resp.get("data") or {}


def check_status(conn, target_id):
    """调用游戏端 check_status_effect，返回解析后的 effects 列表（失败返回空列表）。"""
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
        hostile = "hostile" in (e.get("labels") or [])
        candidates.append((hostile, e))
    if not candidates:
        return None
    # 优先敌对，其次 HP 高者（燃烧需要时间起效，HP 太低会瞬死）
    candidates.sort(key=lambda t: (not t[0], -(t[1].get("hp") or 0)))
    return candidates[0][1]


def main():
    out_dir = ROOT / "logs" / "t1_burning"
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {"test": "T1_burning", "started_at": datetime.now().isoformat(), "steps": []}
    exit_code = 0

    def step(name, data):
        entry = {"t": datetime.now().isoformat(), "step": name, "data": data}
        record["steps"].append(entry)
        print(f"[T1] {name}: {json.dumps(data, ensure_ascii=False)}")

    conn = GameConnector.from_config(ROOT / "config" / "experiment_config.json")
    conn.start()
    print("[T1] 等待游戏接入...")
    if not conn.wait_for_game(timeout=20.0):
        print("[FAIL] 游戏未在 20s 内接入")
        conn.stop()
        return 1
    try:
        # 1. 版本探针
        r = conn.call_raw("set_attack_variant_policy", {"enabled": True}, timeout=10.0)
        step("version_probe", r)
        if not r.get("ok"):
            print("[FAIL] variant 机制不可用（游戏端可能未加载 0.6.15）")
            exit_code = 1
            return exit_code

        # 2. 扫描选目标
        data = scan(conn)
        ents = data.get("entities", [])
        step("scan_before", {
            "count": len(ents),
            "entities": [{"eid": e.get("entity_id"), "kind": e.get("kind"),
                          "labels": e.get("labels"), "hp": e.get("hp")} for e in ents],
        })
        target = pick_target(ents)
        if target is None:
            print("[FAIL] 无可攻击 NPC 目标（已排除 EID:1）")
            exit_code = 1
            return exit_code
        tid = target["entity_id"]
        step("target_selected", {"eid": tid, "labels": target.get("labels"),
                                 "hp": target.get("hp")})

        # 3. 强制 burning
        r = conn.call_raw("player_attack", {"target_id": tid, "variant": "burning"},
                          timeout=15.0)
        step("attack_burning", r)
        if not r.get("ok"):
            print(f"[FAIL] 攻击调用失败: {r.get('error')}")
            exit_code = 1
            return exit_code
        atk = r.get("data") or {}
        if not atk.get("accepted"):
            print(f"[FAIL] 攻击被拒: {atk.get('reason')}")
            exit_code = 1
            return exit_code
        if atk.get("reason") != "status_effect_applied":
            print(f"[FAIL] 未走状态效果路径，当前 reason={atk.get('reason')} "
                  "（0.6.16 未部署或状态效果未挂上）")
            exit_code = 1
            return exit_code

        # 4. 延迟确认燃烧状态（规避 Apply 后同帧 HasStatusEffect 假阴性）
        print(f"[T1] 等待 {STATUS_CHECK_DELAY_S}s 后确认 burning 状态...")
        time.sleep(STATUS_CHECK_DELAY_S)
        effects, status_resp = check_status(conn, tid)
        step("status_check_early", {"effects": effects, "raw": status_resp})
        status_confirmed = burning_present(effects)

        # 5. HP 轨迹追踪 + 中期状态再确认
        print(f"[T1] 开始追踪 {tid} 的 HP 轨迹（{TRACK_SECONDS:.0f}s）...")
        t0 = time.monotonic()
        seq = []
        initial_hp = target.get("hp")
        hp_decreased = False
        disappeared = False
        next_milestone_idx = 0
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
            seq.append({"elapsed": round(elapsed, 1),
                        "present": present,
                        "hp": hp})
            if present and initial_hp is not None and hp is not None and hp < initial_hp - 0.001:
                hp_decreased = True
            if not present and elapsed > 5.0:
                disappeared = True

            # 中期里程碑状态确认
            if (next_milestone_idx < len(STATUS_CHECK_MILESTONES_S)
                    and elapsed >= STATUS_CHECK_MILESTONES_S[next_milestone_idx]):
                ms = STATUS_CHECK_MILESTONES_S[next_milestone_idx]
                effects, status_resp = check_status(conn, tid)
                step(f"status_check_{ms}s", {"effects": effects, "raw": status_resp})
                if burning_present(effects):
                    status_confirmed = True
                next_milestone_idx += 1

        step("hp_track", seq)

        # 6. 结果判定
        passed = hp_decreased or status_confirmed or disappeared
        if not passed:
            print(f"[FAIL] 40s 内既未观测到 HP 下降（initial={initial_hp}），"
                  f"也未确认 burning 状态，目标亦未消失")
            exit_code = 1
        else:
            print(f"[PASS] 燃烧测试通过：hp_decreased={hp_decreased}, "
                  f"status_confirmed={status_confirmed}, disappeared={disappeared}")
    finally:
        conn.stop()
        record["finished_at"] = datetime.now().isoformat()
        log_file = out_dir / f"t1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        log_file.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"[T1] 结果已落盘: {log_file}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
