"""loop_mock.py —— 阶段 0：无游戏、无 GPU、零装机地验"多轮决策连贯性"

跑法：
    py -3.12 loop_mock.py --rounds 12
    py -3.12 loop_mock.py --rounds 6 --quota attack=1 --quota pickup=0

它复用的东西（都在 cuf-qs-2077 公开仓，不重写）：
- src/w3/action_planner.py 的 parse_plan_json（LLM 输出容错解析）与
  ActionPlanner.validate（白名单 + 固定区域位置约束的硬校验）；
- tests/mock_scenarios.json 的实体拓扑当观测源。

⚠ 这一层能验的和不能验的：
  能验——模型是否持续产出可解析、过白名单、不越界的动作；无反馈时会不会卡死循环。
  不能验——"它真做到了什么"。mock_game_server.py 没有任何 hp/damage 逻辑，
  所以本回路里动作**不产生后果**（executed=false / effect=not_simulated），
  跑绿了也不等于闭环成立。真后果要等阶段 1 装回游戏 + 键鼠输入通道。
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time

REPO = os.environ.get("QSP_PLAY_REPO",
                      r"D:\AIWorkflow\publish\cuf-qs-2077")
PLANNER_PATH = os.path.join(REPO, "src", "w3", "action_planner.py")
SCENARIOS_PATH = os.path.join(REPO, "tests", "mock_scenarios.json")

# deepseek-flash 低档价（2026-10-23 官方价目页重查：输入未命中 1、命中 0.02–0.15、
# 输出 4 CNY / 百万 token，单位人民币）。档位随上下文长度上浮，此处按短上下文算。
PRICE_IN, PRICE_IN_CACHED, PRICE_OUT = 1.0, 0.15, 4.0


def load_planner():
    if not os.path.isfile(PLANNER_PATH):
        sys.exit("找不到 %s —— 先确认公开仓在位" % PLANNER_PATH)
    spec = importlib.util.spec_from_file_location("cuf_planner", PLANNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def obs_from_scenario(scen):
    ents = []
    for e in scen.get("entities", []):
        ents.append({"entity_id": e["entity_id"], "kind": e.get("kind"),
                     "labels": e.get("labels"), "hp": e.get("hp"),
                     "position": e.get("position"),
                     "sampling_status": "ok"})
    player = next((e for e in ents if e.get("kind") == "player"), None)
    return {"entities": ents,
            "player_position": (player or {}).get("position", {}),
            "events": []}


def obs_hash(obs):
    return hashlib.sha256(json.dumps(
        [(e["entity_id"], e["hp"], e["position"]) for e in obs["entities"]],
        sort_keys=True).encode()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--scenario", default="normal")
    ap.add_argument("--quota", action="append", default=[],
                    help="形如 attack=2，覆盖默认配额")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-coach", action="store_true",
                    help="关掉「无变化就换策略」引导句，做 A/B 验自发适应性")
    args = ap.parse_args()

    coach = not args.no_coach
    here = os.path.dirname(os.path.abspath(__file__))
    run_dir = args.out or os.path.join(
        here, "runs", "%s_%s" % (time.strftime("%Y%m%d_%H%M%S"),
                                 "coach" if coach else "nocoach"))
    os.makedirs(run_dir, exist_ok=True)

    planner_mod = load_planner()
    sys.path.insert(0, here)
    from brain import Brain
    from gate import WriteGate

    quotas = {}
    for item in args.quota:
        k, _, v = item.partition("=")
        quotas[k.strip()] = int(v)

    scenarios = json.loads(open(SCENARIOS_PATH, encoding="utf-8").read())
    if args.scenario not in scenarios:
        sys.exit("未知场景 %s，可选：%s" % (args.scenario, list(scenarios)))
    base = obs_from_scenario(scenarios[args.scenario])

    player_pos = base["player_position"]
    planner = planner_mod.ActionPlanner(
        mode="active",
        allowed_actions=("attack", "move", "pickup", "kill"),
        area_center=player_pos, area_radius=80.0)   # 以玩家当前位置为中心，对齐 mock 坐标
    gate = WriteGate(quotas=quotas)
    brain = Brain(move_max=planner_mod.MOVE_MAX_STEP, coach=coach)

    history, rows = [], []
    prev_hash, last_sig, repeat_streak, max_streak = None, None, 0, 0
    rounds_path = os.path.join(run_dir, "rounds.jsonl")

    for rid in range(args.rounds):
        obs = dict(base, round_id=rid)
        h = obs_hash(obs)
        world_changed = (prev_hash is not None and h != prev_hash) or prev_hash is None
        prev_hash = h

        t0 = time.time()
        plan, err = brain.ask(obs, history, world_changed,
                              planner_mod.parse_plan_json)
        latency = round(time.time() - t0, 2)

        row = {"round_id": rid, "ts": time.time(), "obs_hash": h,
               "world_changed": bool(world_changed), "latency_s": latency,
               "finish_reason": brain.last_finish,
               "reasoning_chars": brain.last_reasoning_len,
               "n_entities": len(obs["entities"])}
        if plan is None:
            row.update(decision="rejected", stage="parse", note=err)
        else:
            ok_v, why = planner.validate(plan, obs, player_position=player_pos)
            row.update(proposed=plan, validate_ok=bool(ok_v), validate_reason=why)
            if not ok_v:
                row.update(decision="rejected", stage="validate", note=why)
            else:
                ok_g, labels = gate.decide(plan["action_type"])
                row.update(gate=labels)
                if ok_g:
                    row.update(decision="accepted", stage="gate",
                               action_type=plan["action_type"],
                               target_entity_id=plan.get("target_entity_id"),
                               executed=False, effect="not_simulated")
                else:
                    row.update(decision="rejected", stage="gate",
                               note=labels["gate_reason"],
                               action_type=plan["action_type"])

        sig = "%s|%s" % (row.get("action_type") or plan and plan.get("action_type")
                         or "-", row.get("target_entity_id")
                         or (plan or {}).get("target_entity_id") or "-")
        repeat_streak = repeat_streak + 1 if sig == last_sig else 1
        max_streak = max(max_streak, repeat_streak)
        last_sig = sig

        history.append({"round_id": rid,
                        "action_type": (plan or {}).get("action_type"),
                        "target_entity_id": (plan or {}).get("target_entity_id"),
                        "decision": row["decision"],
                        "note": row.get("note") or row.get("gate", {}).get("gate_reason", "")})
        rows.append(row)
        with open(rounds_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        reason = ((plan or {}).get("reason") or "")[:64].replace("\n", " ")
        print("r%-2d %-8s %-7s %-16s %-6s %s" % (
            rid, row["decision"], (plan or {}).get("action_type") or "-",
            row.get("target_entity_id") or "-",
            ("wch=%s" % ("Y" if world_changed else "N")), reason))

    u = brain.usage
    cost = ((u["prompt_tokens"] - u["cached_tokens"]) * PRICE_IN
            + u["cached_tokens"] * PRICE_IN_CACHED
            + u["completion_tokens"] * PRICE_OUT) / 1e6
    accepted = [r for r in rows if r["decision"] == "accepted"]
    summary = {
        "run": os.path.basename(run_dir), "rounds": args.rounds,
        "coach_line_in_prompt": coach,
        "scenario": args.scenario, "repo": REPO,
        "decision_counts": {
            "accepted": len(accepted),
            "rejected_parse": sum(1 for r in rows if r["decision"] == "rejected"
                                  and r.get("stage") == "parse"),
            "rejected_validate": sum(1 for r in rows if r["decision"] == "rejected"
                                     and r.get("stage") == "validate"),
            "rejected_gate": sum(1 for r in rows if r["decision"] == "rejected"
                                 and r.get("stage") == "gate")},
        "proposed_action_distribution": {      # 含被拦下的建议，不等于被执行
            a: sum(1 for r in rows if r.get("action_type") == a)
            for a in ("attack", "move", "pickup", "kill")},
        "accepted_action_distribution": {
            a: sum(1 for r in accepted if r.get("action_type") == a)
            for a in ("attack", "move", "pickup", "kill")},
        "latency_s": {"min": min([r["latency_s"] for r in rows], default=None),
                      "max": max([r["latency_s"] for r in rows], default=None),
                      "mean": round(sum(r["latency_s"] for r in rows)
                                    / len(rows), 2) if rows else None},
        "max_same_action_streak": max_streak,
        "distinct_accepted_targets": len({r.get("target_entity_id")
                                          for r in accepted
                                          if r.get("target_entity_id")}),
        "world_changed_rounds": sum(1 for r in rows if r["world_changed"]),
        "gate": gate.snapshot(),
        "token_usage": u, "cost_cny_estimate": round(cost, 4),
        "price_assumption": "deepseek-flash 短档位：输入 1 / 命中缓存 0.15 / 输出 4 CNY 每百万 token",
        "limitation": ("mock 无后果模拟：executed=false，本轮任何 accepted 都不构成"
                       "“它真做到了”的证据；只测决策连贯性与闸门行为。"),
    }
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n── 汇总 ──")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("归档目录: %s" % run_dir)


if __name__ == "__main__":
    main()
