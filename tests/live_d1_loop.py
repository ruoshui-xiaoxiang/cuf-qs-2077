"""D1.4 实机转向长跑——转向开启下的真实闭环 20 轮，测干预后动力学。

设计（D1 收尾阶段，用户批准后执行）：
- 驻留服务必须在线（转向配置在 server 侧）；离线即失败退出。
- 转向 = 逃跑方向（logs/d1/direction_flee_L16.json）@ 生成侧，α 默认 +4
  （α 扫描结论：+4 位于行为调制生效区且无输出崩解）。
- planner active 模式：转向后的 LLM 建议经白名单+位置约束校验后执行，
  转向因果作用于 行为→证据 回路（开环干预的完整形态）。
- 位置对齐预检：玩家超出区域约束时，临时配置区域中心更新到玩家当前
  位置（沿用既定实践），避免 move 建议全被拒。
- 对照基线：live_7b_v3（无转向同结构闭环 20 轮），事后比较对齐分
  轨迹/反例/预算动力学。

运行：py -3.12 tests\\live_d1_loop.py [--alpha 4.0]
"""
import argparse
import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from w0.game_connector import GameConnector  # noqa: E402
from w2.model_adapter import RemoteModelAdapter  # noqa: E402
from w3.auto_experiment import run_auto_experiment  # noqa: E402
from w3.closed_loop_runner import (make_model_embedding_fn,  # noqa: E402
                                   make_model_state_fn)
from llm_planner_shadow import make_llm_plan_fn  # noqa: E402
from w3.action_planner import ActionPlanner  # noqa: E402

ROUNDS = 20
PORT = 20770
DIRECTION = ROOT / "logs" / "d1" / "direction_flee_L16.json"
CONFIG_SRC = ROOT / "config" / "experiment_config.json"

# D1.3 电池定型结论：完整 SYSTEM_PROMPT（含大量 few-shot 示例）贪心下
# 使模型陷入"示例续写"回显模式（实机首轮 20+ 连坐实）；改用无示例
# 精简提示恢复指令遵循（4 变体矩阵钉死）。
D1_LIVE_SYSTEM = (
    "你在一个虚拟具身实验系统中担任行动规划器。请依据用户提供的当前观测，"
    "只输出一行动作决策，不要输出任何其他文字、不要复述或回显观测内容。\n"
    "- 攻击：attack <目标ID>（目标 ID 必须原样复制观测中的 id，含 EID: 前缀）\n"
    "- 拾取：pickup <目标ID>（目标必须是观测中 kind=item 的实体）\n"
    "- 移动：move <dx> <dz>（dx/dz 为 ±20 内整数）\n"
    "决策后可用 # 写一句简短的中文理由。\n"
    "规则：attack 目标须来自观测、非 player 非 item、hp>0；"
    "pickup 目标必须是 item；有可攻击敌人时优先 attack，无可攻击目标时才 move。\n"
    "反例（严禁）：直接复述观测行（如 'EID:1 (npc, hp=100, dist≈5m)'），"
    "必判无输出。")


def precheck_position():
    gc = GameConnector(host="127.0.0.1", port=PORT, response_timeout_s=20.0)
    gc.start()
    try:
        if not gc.wait_for_game(timeout=120.0):
            print("[FAIL] 游戏未连入（120s 超时）")
            return None, None
        probe = gc.execute_action("probe_loot", {})
        print(f"[PRE] CufBridge {probe.get('mod_version')}")
        ents = gc.execute_action("scan_entities").get("entities") or []
        player = next((e for e in ents if e.get("kind") == "player"), None)
        items = [e for e in ents if e.get("kind") == "item"]
        npcs = [e for e in ents if e.get("kind") == "npc"]
        if player is None:
            print("[FAIL] 扫描无玩家实体")
            return None, None
        pos = player.get("position") or {}
        print(f"[PRE] 玩家 pos=({pos.get('x'):.1f},{pos.get('y'):.1f},"
              f"{pos.get('z'):.1f}) hp={player.get('hp')} "
              f"NPC {len(npcs)} item {len(items)}")
        return pos, probe.get("mod_version")
    finally:
        gc.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=4.0)
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    ap.add_argument("--out", default="live_d1",
                    help="logs/ 下输出目录名（默认 live_d1）")
    args = ap.parse_args()
    log_dir = ROOT / "logs" / args.out
    log_path = log_dir / "rounds.jsonl"
    report_path = log_dir / "report.json"
    config_tmp = log_dir / "config_d1_active.json"

    if not RemoteModelAdapter.ping():
        print("[FAIL] 模型服务不在线（转向须驻留服务承载）")
        sys.exit(1)

    pos, bridge_ver = precheck_position()
    if pos is None:
        sys.exit(1)

    log_dir.mkdir(parents=True, exist_ok=True)
    for p in (log_path, report_path):
        if p.exists():
            p.unlink()

    cfg = json.loads(CONFIG_SRC.read_text(encoding="utf-8"))
    area = cfg.get("planner", {}).get("area", {})
    center = area.get("center") or {}
    radius = float(area.get("radius_m", 80.0))
    dist = math.sqrt((pos["x"] - center.get("x", 0)) ** 2
                     + (pos["y"] - center.get("y", 0)) ** 2)
    if dist > radius:
        print(f"[PRE] 玩家距区域中心 {dist:.0f}m > {radius:.0f}m → "
              f"临时配置区域中心更新为玩家位置（沿用既定实践）")
        cfg["planner"]["area"]["center"] = {
            "x": pos["x"], "y": pos["y"], "z": pos.get("z", 0)}
    config_tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    adapter = RemoteModelAdapter()
    drec = json.loads(DIRECTION.read_text(encoding="utf-8"))
    status = adapter.set_steering(drec["layer"], args.alpha, drec["vector"])
    print(f"[D1] steering set: layer={drec['layer']} alpha={args.alpha:+.1f} "
          f"status={status}")

    plan_fn = make_llm_plan_fn(adapter, system_prompt=D1_LIVE_SYSTEM)
    planner = ActionPlanner.from_config(
        config_path=str(config_tmp), plan_fn=plan_fn)
    print(f"[D1] planner mode={planner.mode} prompt_regime=compact_no_examples")

    report = run_auto_experiment(
        rounds=args.rounds, config_path=str(config_tmp), port=PORT,
        log_path=str(log_path), report_path=str(report_path),
        wait_timeout_s=120.0,
        embedding_fn=make_model_embedding_fn(adapter),
        state_fn=make_model_state_fn(adapter),
        action_planner=planner,
    )
    adapter.clear_steering()
    print(f"[D1] steering cleared: status={adapter.steering_status()}")

    print(f"[D1] completed={report['completed']} "
          f"converged={report['converged_final']} "
          f"criterion={report['criterion']} "
          f"lifespan={report.get('virtual_lifespan')}")
    scores = [rd["alignment_score"] for rd in report["round_details"]]
    print("[D1] alignment_series="
          f"{[round(s, 4) if s is not None else None for s in scores]}")
    c_ext = [rd.get("c_h", {}).get("extend") for rd in report["round_details"]]
    print(f"[D1] c_extend_series={c_ext}")
    ces = report.get("counterexample_summary") or {}
    print(f"[D1] counterexamples: {json.dumps(ces, ensure_ascii=False)[:300]}")
    n_planner = sum(1 for rd in report["round_details"]
                    if rd.get("action_source") == "planner")
    print(f"[D1] planner_executed={n_planner}/{report['completed']} "
          f"(bridge={bridge_ver})")
    st = plan_fn.stats
    rate = (st["greedy_ok"] / st["calls"] * 100) if st["calls"] else 0.0
    print(f"[D1] parse_stats: calls={st['calls']} greedy_ok={st['greedy_ok']}"
          f" sampling_ok={st['sampling_ok']} all_fail={st['all_fail']}"
          f" → 贪心命中率={rate:.0f}%")
    print(f"[D1] report={report_path}")
    print("[D1] 对照：live_7b_v3（无转向同结构 20 轮）事后比较")


if __name__ == "__main__":
    main()
