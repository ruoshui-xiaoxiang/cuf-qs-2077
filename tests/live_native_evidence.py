"""S30 实机取证：短程闭环（~6 轮），验证「kill → entity_death 证据 →
native_state 记录」完整事件流。

前置：游戏运行并进入固定实验区域；CufBridge（20770）与 CufNative
（20771，含 list_npcs）就绪。

数据口径（用户裁定 2026-08-27）：实机数据必须走本地 7B 双路（真实
对齐分）——零模型/确定性嵌入的实机数据属无证据低质量数据，不得作
为取证依据。故本脚本按 live_7b_loop.py 惯例：`py -3.12`（torch GPU），
驻留服务在线走远程双路（推理锁保护），否则回退本地加载。

kill 只经 planner 建议路径进入（active 模式）：注入 plan_fn 在第 2/4
轮建议 kill（半径 30），其余轮返回 None 回退 heuristic——默认编排
语义不被改动（计划 §4）。取证目标：至少一次「kill 执行 →
entity_death 证据（EID:<hash> 口径）→ 下一轮 native_state 落档」，
产物归档 logs/live_native_<ts>/。

用法：py -3.12 tests/live_native_evidence.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from common.native_client import NativeClient, NativeUnavailableError
from w0.data_sampler import DataSampler
from w0.game_connector import GameConnector
from w2.model_adapter import ModelAdapter, RemoteModelAdapter
from w3.action_executor import ActionExecutor
from w3.action_planner import ActionPlanner
from w3.closed_loop_runner import (ClosedLoopRunner,
                                   make_model_embedding_fn,
                                   make_model_state_fn)
from w3.log_recorder import LogRecorder

ROUNDS = 6
KILL_ROUNDS = (2, 4)
KILL_RADIUS = 30.0
PORT = 20770


def kill_plan_fn(planner, observation, round_id):
    """定向注入：仅在指定轮建议 kill，其余轮回退默认策略。"""
    if round_id in KILL_ROUNDS:
        return {"action_type": "kill",
                "params": {"radius": KILL_RADIUS},
                "reason": f"S30 live evidence r{round_id}"}
    return None


def main():
    # ── 前置检查（fail-loud）──────────────────────────────
    native = NativeClient.from_config()
    if native is None:
        print("[FATAL] config native.enabled=false，取证无意义")
        return 1
    try:
        st = native.get_state()
    except NativeUnavailableError as e:
        print(f"[FATAL] CufNative(20771) 不可用：{e}")
        return 1
    print(f"[pre] native get_state ok: heatStage={st.get('heatStage')} "
          f"chasingPlayer={st.get('chasingPlayer')}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "logs" / f"live_native_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "rounds.jsonl"

    # ── 7B 双路（数据质量硬口径，不走确定性嵌入）──────────
    if RemoteModelAdapter.ping():
        print("[7B] model_server 在线 → 远程双路（推理锁保护）")
        adapter = RemoteModelAdapter()
    else:
        adapter = ModelAdapter.from_config()
        print("[7B] loading Qwen2.5-7B-Instruct (NF4)...")
        adapter.load()
    print(f"[7B] hidden_size={len(adapter.encode('x'))}")

    gc = GameConnector(host="127.0.0.1", port=PORT)
    gc.start()
    if not gc.wait_for_game(timeout=60.0):
        print("[FATAL] CufBridge(20770) 未连接（游戏未运行或未加载 mod）")
        gc.stop()
        return 1
    print("[pre] CufBridge connected")

    try:
        sampler = DataSampler.from_config(gc)
        executor = ActionExecutor.from_config(gc)
        recorder = LogRecorder(str(log_path))
        runner = ClosedLoopRunner.from_config(
            gc, sampler, executor, recorder, rounds=ROUNDS,
            state_fn=make_model_state_fn(adapter))
        # from_config 默认确定性嵌入，此处按实机数据口径覆写为 7B 双路
        runner.embedding_fn = make_model_embedding_fn(adapter)
        runner.action_planner = ActionPlanner.from_config(plan_fn=kill_plan_fn)
        print(f"[run] planner mode={runner.action_planner.mode} "
              f"allowed={runner.action_planner.allowed_actions}")
        report = runner.run()
    finally:
        gc.stop()
        if hasattr(adapter, "unload"):
            adapter.unload()

    # ── 取证汇总 ──────────────────────────────────────────
    kills, deaths, natives = [], [], []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            rd = json.loads(line)
        except ValueError:
            continue
        w0 = rd.get("w0") or {}
        w3 = rd.get("w3") or {}
        act = w3.get("action") or ""
        if act.startswith("kill"):
            kills.append({
                "round": rd.get("round_id"),
                "status": w3.get("action_status"),
                "control_tier": w3.get("action_control_tier"),
                "receipt_reason": w3.get("action_receipt_reason"),
                "outcome_semantics": w3.get("action_outcome_semantics"),
                "confirmation_status": w3.get("action_confirmation_status"),
            })
        for ev in (w0.get("events") or []):
            if isinstance(ev, dict) and ev.get("kind") == "entity_death":
                deaths.append({"round": rd.get("round_id"),
                               "target": ev.get("target_entity"),
                               "unconfirmed": ev.get("unconfirmed", False)})
        ns = w0.get("native_state")
        if isinstance(ns, dict):
            natives.append({"round": rd.get("round_id"),
                            "available": ns.get("available"),
                            "heat_stage": ns.get("heat_stage")})
    confirmed_kill = any(
        k.get("confirmation_status") == "confirmed" or
        k.get("outcome_semantics") == "death_confirmed"
        for k in kills
    )
    confirmed_death_event = any(not d["unconfirmed"] for d in deaths)
    summary = {
        "ts": ts, "rounds": ROUNDS,
        "completed": report.get("completed"),
        "kill_actions": kills,
        "entity_death_events": deaths,
        "native_states": natives,
        "alignment_series": [round(s, 4) if s is not None else None
                             for s in (report.get("qs_history") or [])],
        "chain_ok": bool(
            kills and
            (confirmed_kill or confirmed_death_event) and
            any(n["available"] for n in natives)
        ),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[done] archived -> {out_dir}")
    return 0 if summary["chain_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
