"""S29.5 planner 正式批次——自主行动（heuristic/LLM）vs 默认编排的实验收益统计。

动作响应式 mock（ActionAwareMock）：attack 真实减目标 HP（耗尽即移除
实体）、move 改变玩家位置 → 动作选择改变世界状态（存活实体轨迹）→
间接影响对齐分/反例/降级（反套壳不变：planner 不直接改裁决）。

口径：
- 对照组（control）：无 planner，默认编排（奇偶轮攻 scan 首个 NPC / ±10m 交替）
- 实验组 heuristic：ActionPlanner(mode=active) 默认策略（攻最近 / ±10m）
- 实验组 LLM：ActionPlanner(mode=active, plan_fn=真实 7B 决策串)

场景：3 NPC——G1(gang hostile, 远 x=50) C1/C2(civilian, 近 x=5/12)；
scan 顺序 G1 在前 → 默认编排攻 npcs[0]=G1（远敌），heuristic 攻最近=C1
（近平民）→ 行为分化，世界演化轨迹不同。

收益指标（对照组 vs 实验组）：
- 存活实体轨迹（每轮 scan 实体数）——清除效率
- 目标选择分布（attack_log：打敌对 gang vs 误伤 civilian）
- 对齐分均值/轨迹（LLM 模式为 7B 真实判别分；heuristic 模式为离线路）
- 虚拟寿命 / 是否解离 / 终止判据
- 动作来源分布（planner 占比）

运行：
- heuristic 全量：py -3.12 tests/planner_batch.py --mode heuristic --seeds 7 42 2077
- LLM 少量：   py -3.12 tests/planner_batch.py --mode llm --seeds 7 --rounds 10
"""
import argparse
import copy
import json
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_game_server import MockGameServer
from w3.action_planner import ActionPlanner
from w3.auto_experiment import run_auto_experiment

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "logs" / "planner_batch"
DEFAULT_CONFIG = (Path(__file__).resolve().parents[1]
                  / "config" / "experiment_config.json")

_TEMP_CONFIG = None


def _temp_config_no_activation():
    """生成临时 config 副本：关闭候选激活引擎。

    planner_batch 聚焦"动作收益"实验——50 条 2077 候选规则若激活，
    其断言会与 mock 场景标签不匹配产生大量反例（正确证伪行为，但
    干扰动作收益对比）。候选激活由 candidate_pool_check / s28 单独
    验证，本实验关闭 activation。
    """
    global _TEMP_CONFIG
    if _TEMP_CONFIG is not None and _TEMP_CONFIG.exists():
        return _TEMP_CONFIG
    cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("activation", {})["enabled"] = False
    tmp = Path(__file__).resolve().parent / "_tmp_planner_batch_config.json"
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    _TEMP_CONFIG = tmp
    return tmp


class ActionAwareMock(MockGameServer):
    """动作响应式自由世界：攻击真实减目标 HP（耗尽移除）、移动改变位置。

    场景（与默认编排行为分化）：G1(gang hostile) 远、C1/C2(civilian) 近；
    scan 顺序 G1 在前。默认编排攻 npcs[0]=G1（清敌），heuristic planner
    攻最近=C1（误伤平民）→ 世界演化轨迹不同。
    """

    def __init__(self, port, seed):
        super().__init__(port=port, initial_scenario="empty")
        self.rng = random.Random(seed)
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.entities = [
            {"entity_id": "EID:G1", "kind": "npc",
             "labels": ["gang", "hostile"], "hp": 100.0,
             "position": {"x": 50.0, "y": 0.0, "z": 0.0}},
            {"entity_id": "EID:C1", "kind": "npc",
             "labels": ["civilian"], "hp": 50.0,
             "position": {"x": 5.0, "y": 0.0, "z": 0.0}},
            {"entity_id": "EID:C2", "kind": "npc",
             "labels": ["civilian"], "hp": 50.0,
             "position": {"x": 12.0, "y": 0.0, "z": 0.0}},
            {"entity_id": "EID:I1", "kind": "item",
             "labels": ["item", "cyberware", "pickup"], "hp": 0.0,
             "position": {"x": 20.0, "y": 0.0, "z": 15.0}},
        ]
        self._events_queue = []
        self._next_evt = 0
        self.attack_log = []
        self.pickup_log = []
        self._last_scan_ids = set()
        self.handlers["scan_entities"] = self._h_scan
        self.handlers["get_events"] = self._h_get_events
        self.handlers["player_attack"] = self._h_attack
        self.handlers["player_move"] = self._h_move
        self.handlers["player_pickup"] = self._h_pickup

    def _h_scan(self, args):
        with self._lock:
            ents = copy.deepcopy(self.entities)
            ents.insert(0, {"entity_id": "EID:1", "kind": "player",
                            "labels": ["player"], "hp": 100.0,
                            "position": dict(self.player_pos)})
            # S29.9 L2：scan 差分（对齐 CufBridge 实机 perform_scan_diff）——
            # 新出现 entity_appeared、消失 entity_disappeared 事件
            cur_ids = {e["entity_id"] for e in ents}
            for eid in cur_ids - self._last_scan_ids:
                self._next_evt += 1
                self._events_queue.append({
                    "event_id": f"evt_{self._next_evt}",
                    "kind": "entity_appeared", "source_entity": eid,
                    "target_entity": eid,
                    "game_ts": int(time.time() * 1000)})
            for eid in self._last_scan_ids - cur_ids:
                self._next_evt += 1
                self._events_queue.append({
                    "event_id": f"evt_{self._next_evt}",
                    "kind": "entity_disappeared", "source_entity": eid,
                    "target_entity": eid,
                    "game_ts": int(time.time() * 1000)})
            self._last_scan_ids = cur_ids
        return {"count": len(ents), "entities": ents, "player": ents[0]}

    def _h_get_events(self, args):
        with self._lock:
            evs = list(self._events_queue)
            self._events_queue.clear()
        return {"count": len(evs), "events": evs}

    def _h_attack(self, args):
        target_id = args.get("target_id", "")
        with self._lock:
            self.attack_log.append(target_id)
            for e in self.entities:
                if e["entity_id"] != target_id:
                    continue
                hp_before = e["hp"]
                e["hp"] -= 60.0
                self._next_evt += 1
                self._events_queue.append({
                    "event_id": f"evt_{self._next_evt}",
                    "kind": "combat_attack", "source_entity": "EID:1",
                    "target_entity": target_id,
                    "game_ts": int(time.time() * 1000)})
                self._next_evt += 1
                self._events_queue.append({
                    "event_id": f"evt_{self._next_evt}",
                    "kind": "hp_changed", "source_entity": target_id,
                    "target_entity": target_id, "hp_before": hp_before,
                    "hp_after": e["hp"],
                    "game_ts": int(time.time() * 1000)})
                if e["hp"] <= 0:
                    self._next_evt += 1
                    self._events_queue.append({
                        "event_id": f"evt_{self._next_evt}",
                        "kind": "entity_death", "source_entity": target_id,
                        "target_entity": target_id,
                        "game_ts": int(time.time() * 1000)})
                    self.entities.remove(e)
                break
        return {"action_id": f"atk_{target_id}", "accepted": True,
                "reason": "damage_dispatched"}

    def _h_move(self, args):
        with self._lock:
            self.player_pos = {"x": float(args.get("x", 0.0)),
                               "y": float(args.get("y", 0.0)),
                               "z": float(args.get("z", 0.0))}
            # A_M2 候选触发事件（移动因果）：每轮 move 计入激活证据
            self._next_evt += 1
            self._events_queue.append({
                "event_id": f"evt_{self._next_evt}",
                "kind": "move_confirmed", "source_entity": "EID:1",
                "target_entity": "EID:1",
                "game_ts": int(time.time() * 1000)})
        return {"action_id": "act_move", "accepted": True,
                "reason": "teleport_dispatched"}

    def _h_pickup(self, args):
        target_id = args.get("target_id", "")
        with self._lock:
            self.pickup_log.append(target_id)
            for e in self.entities:
                if e["entity_id"] != target_id:
                    continue
                if e.get("kind") != "item":
                    return {"action_id": f"pick_{target_id}",
                            "accepted": False,
                            "reason": f"target_not_item:{target_id}"}
                self._next_evt += 1
                self._events_queue.append({
                    "event_id": f"evt_{self._next_evt}",
                    "kind": "item_acquired", "source_entity": "EID:1",
                    "target_entity": target_id,
                    "game_ts": int(time.time() * 1000)})
                self.entities.remove(e)
                return {"action_id": f"pick_{target_id}", "accepted": True,
                        "reason": "item_picked_up"}
            return {"action_id": f"pick_{target_id}", "accepted": False,
                    "reason": f"target_not_found:{target_id}"}


def _run_one_condition(port, rounds, seed, planner, embedding_fn, state_fn,
                       log_dir, disable_planner=False):
    """单种子单变体运行：mock 线程 + 闭环 + 指标提取。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "rounds.jsonl"
    report_path = log_dir / "report.json"
    # LogRecorder 为追加语义：重复运行须清空旧归档，防指标污染
    for p in (log_path, report_path):
        if p.exists():
            p.unlink()

    mock = ActionAwareMock(port=port, seed=seed)
    t = threading.Thread(target=mock.connect_with_retry,
                         kwargs={"interval_s": 0.1}, daemon=True)
    t.start()
    try:
        report = run_auto_experiment(
            rounds=rounds, port=port, seed=seed,
            config_path=str(_temp_config_no_activation()),
            log_path=str(log_path), report_path=str(report_path),
            wait_timeout_s=30.0,
            embedding_fn=embedding_fn, state_fn=state_fn,
            action_planner=planner, disable_planner=disable_planner)
    finally:
        mock.close()

    details = report.get("round_details", []) or []
    aligns = [d.get("alignment_score") for d in details
              if isinstance(d.get("alignment_score"), (int, float))]
    alive_ids = []
    ce_counts = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ents = entry.get("w0", {}).get("entities", []) or []
            alive_ids.append(sorted(
                e.get("entity_id") for e in ents
                if e.get("kind") != "player"))
            ce_counts.append(
                entry.get("w1", {}).get("valid_counterexamples", 0))
    return {
        "seed": seed, "port": port,
        "completed": report.get("completed"),
        "virtual_lifespan": report.get("virtual_lifespan"),
        "disintegrated": report.get("disintegrated"),
        "criterion": report.get("criterion"),
        "align_mean": (sum(aligns) / len(aligns)) if aligns else None,
        "align_count": len(aligns),
        "attack_log": list(mock.attack_log),
        "alive_ids": alive_ids,
        "ce_counts": ce_counts,
        "activation": report.get("activation"),
        "sources": [d.get("action_source") for d in details],
        "actions": [d.get("action") for d in details],
    }


def _attack_summary(attack_log):
    if not attack_log:
        return "(无攻击)"
    seq = ",".join(attack_log)
    hostile = sum(1 for t in attack_log if "G" in t)
    civilian = sum(1 for t in attack_log if "C" in t)
    return f"[{seq}] 敌{hostile}/民{civilian}"


def _print_table(variant_runs):
    print(f"\n{'变体':<10}{'seed':<6}{'完成':<5}{'寿命':<5}{'解离':<6}"
          f"{'对齐均':<9}{'反例数':<14}{'激活':<12}{'攻击顺序':<28}")
    for name, runs in variant_runs.items():
        for r in runs:
            ce_s = ("->".join(str(x) for x in r["ce_counts"])
                    if r["ce_counts"] else "-")
            act = r["activation"] or {}
            act_s = ",".join(act.get("activated", []) or []) or "-"
            align_s = ("-" if r["align_mean"] is None
                       else f"{r['align_mean']:.3f}")
            print(f"{name:<10}{r['seed']:<6}{r['completed']:<5}"
                  f"{r['virtual_lifespan']:<5}"
                  f"{str(r['disintegrated']):<6}{align_s:<9}{ce_s:<14}"
                  f"{act_s:<12}{_attack_summary(r['attack_log']):<28}")


def run_variant(name, planner, seeds, rounds, port_cursor, embedding_fn,
                state_fn, out_dir, disable_planner=False):
    runs = []
    for seed in seeds:
        run_dir = out_dir / name / f"seed_{seed}"
        runs.append(_run_one_condition(
            port_cursor, rounds, seed, planner, embedding_fn, state_fn,
            run_dir, disable_planner=disable_planner))
        port_cursor += 1
    return runs, port_cursor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("heuristic", "llm"), default="heuristic")
    ap.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 2077])
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--base-port", type=int, default=20890)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    port = int(args.base_port)
    embedding_fn = state_fn = None
    variant_runs = {}

    if args.mode == "heuristic":
        # 离线路（对齐分恒 1.0）：机制 + 行为指标为主，快
        control, port = run_variant(
            "control", None, args.seeds, args.rounds, port,
            embedding_fn, state_fn, out_dir, disable_planner=True)
        exp, port = run_variant(
            "heuristic", ActionPlanner(mode="active"), args.seeds,
            args.rounds, port, embedding_fn, state_fn, out_dir)
        variant_runs = {"control": control, "heuristic": exp}
    else:
        # 7B 双路：真实判别分；对照组 + LLM 自主行动
        from w2.model_adapter import ModelAdapter
        from w3.closed_loop_runner import (make_model_embedding_fn,
                                           make_model_state_fn)
        from llm_planner_shadow import make_llm_plan_fn
        adapter = ModelAdapter.from_config()
        print("[7B] loading Qwen2.5-7B-Instruct (NF4)...")
        adapter.load()
        embedding_fn = make_model_embedding_fn(adapter)
        state_fn = make_model_state_fn(adapter)
        control, port = run_variant(
            "control", None, args.seeds, args.rounds, port,
            embedding_fn, state_fn, out_dir, disable_planner=True)
        exp, port = run_variant(
            "llm", ActionPlanner(mode="active",
                                 plan_fn=make_llm_plan_fn(adapter)),
            args.seeds, args.rounds, port, embedding_fn, state_fn, out_dir)
        adapter.unload()
        variant_runs = {"control": control, "llm": exp}

    _print_table(variant_runs)

    summary_path = out_dir / f"{args.mode}_summary.json"
    summary_path.write_text(
        json.dumps({k: v for k, v in variant_runs.items()},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print(f"\n[planner_batch] {args.mode} 完成，总汇落盘 {summary_path}")


if __name__ == "__main__":
    main()
