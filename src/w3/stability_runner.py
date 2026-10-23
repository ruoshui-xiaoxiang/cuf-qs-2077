"""S7 稳定性联调脚本。

串联 W0 采集 → W3 动作 → W3 归档，连续 N 轮闭环。
验收：连续 20 轮无中断，脏数据占比 <1%。

用法（实机）：
    py src\\w3\\stability_runner.py [--port 20770] [--rounds 20]

用法（离线自测）：
    py tests\\s7_selftest.py
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# 确保 src 在 path
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from w0.game_connector import GameConnector, GameConnectorError
from w0.data_sampler import DataSampler
from w3.action_executor import ActionExecutor
from w3.log_recorder import LogRecorder

log = logging.getLogger("stability_runner")

DEFAULT_ROUNDS = 20
RETRY_MAX = 2
DIRTY_RATIO_THRESHOLD = 0.01
CONFIRMED_ACTION_OUTCOMES = {
    "position_observed",
    "damage_observed",
    "death_observed",
    "death_confirmed",
    "item_acquired",
    "item_remove",
    "combat_signal_observed",
    "attack_evidence_observed",
}


class StabilityRunner:
    """串联采集→动作→归档的稳定性联调器。"""

    def __init__(self, connector, sampler, executor, recorder, rounds=DEFAULT_ROUNDS,
                 round_delay_s=0.0):
        self.connector = connector
        self.sampler = sampler
        self.executor = executor
        self.recorder = recorder
        self.rounds = int(rounds)
        self.round_delay_s = float(round_delay_s)
        self.results = []

    def run(self):
        """跑 N 轮闭环，返回汇总报告。"""
        report = {
            "total_rounds": self.rounds,
            "completed": 0,
            "failed": 0,
            "round_details": [],
            "total_entities": 0,
            "total_events": 0,
            "total_invalid_entities": 0,
            "total_invalid_events": 0,
            "total_dirty": 0,
            "total_samples": 0,
            "dirty_ratio": 0.0,
            "action_completed": 0,
            "action_completed_confirmed": 0,
            "action_completed_unconfirmed": 0,
            "action_failed": 0,
            "action_rejected_or_failed": 0,
            "stashed_events_merged": 0,
            "interruption": False,
            "interruption_reason": None,
        }
        for i in range(self.rounds):
            round_id = f"r{i}"
            detail = self._run_single_round(round_id, i)
            report["round_details"].append(detail)
            if detail["status"] == "ok":
                report["completed"] += 1
            else:
                report["failed"] += 1
                if detail.get("fatal"):
                    report["interruption"] = True
                    report["interruption_reason"] = detail.get("error", "unknown")
                    break
            report["total_entities"] += detail.get("ok_entities", 0)
            report["total_events"] += detail.get("ok_events", 0)
            report["total_invalid_entities"] += detail.get("invalid_entities", 0)
            report["total_invalid_events"] += detail.get("invalid_events", 0)
            report["total_samples"] += detail.get("total_samples", 0)
            if detail.get("action") is not None:
                if detail.get("action_status") == "completed":
                    report["action_completed"] += 1
                    if self._is_confirmed_action(detail):
                        report["action_completed_confirmed"] += 1
                    else:
                        report["action_completed_unconfirmed"] += 1
                else:
                    report["action_failed"] += 1
                    report["action_rejected_or_failed"] += 1
            report["stashed_events_merged"] += detail.get("stashed_merged", 0)
            if self.round_delay_s > 0 and i < self.rounds - 1:
                time.sleep(self.round_delay_s)

        report["total_dirty"] = (report["total_invalid_entities"] +
                                  report["total_invalid_events"])
        total = report["total_samples"]
        report["dirty_ratio"] = (report["total_dirty"] / total) if total > 0 else 0.0
        report["passed"] = (report["interruption"] is False and
                            report["failed"] == 0 and
                            report["dirty_ratio"] < DIRTY_RATIO_THRESHOLD)
        self.results = report
        return report

    def _run_single_round(self, round_id, index):
        """单轮：采集 → （可选）动作 → 归档。"""
        detail = {
            "round_id": round_id,
            "index": index,
            "status": "ok",
            "error": None,
            "fatal": False,
            "ok_entities": 0,
            "ok_events": 0,
            "invalid_entities": 0,
            "invalid_events": 0,
            "total_samples": 0,
            "action": None,
            "action_status": None,
            "action_channel": None,
            "action_control_tier": None,
            "action_reject_reason": None,
            "action_receipt_reason": None,
            "action_outcome_semantics": None,
            "action_confirmation_status": None,
            "stashed_merged": 0,
            "c_h_total": 0,
        }
        # ── 采集 ──
        w0_data = None
        for attempt in range(RETRY_MAX + 1):
            try:
                w0_data = self.sampler.collect_round(round_id)
                break
            except GameConnectorError as e:
                if attempt < RETRY_MAX:
                    log.warning("round %s collect retry %d: %s", round_id, attempt + 1, e)
                    time.sleep(1.0)
                else:
                    detail["status"] = "collect_failed"
                    detail["error"] = str(e)
                    detail["fatal"] = True
                    return detail
            except Exception as e:
                detail["status"] = "collect_error"
                detail["error"] = f"{type(e).__name__}: {e}"
                detail["fatal"] = True
                return detail

        stats = w0_data.get("stats", {}) if isinstance(w0_data, dict) else {}
        detail["ok_entities"] = stats.get("ok_entities", 0)
        detail["ok_events"] = len(w0_data.get("events", [])) if isinstance(w0_data, dict) else 0
        detail["invalid_entities"] = stats.get("invalid_entities", 0)
        detail["invalid_events"] = stats.get("invalid_events", 0)
        detail["total_samples"] = (stats.get("raw_entities", 0) +
                                    stats.get("raw_events", 0))

        # ── 动作（隔轮交替 attack/move，无目标时跳过）──
        action_result = None
        action_label = None
        if index % 4 == 1:
            # 尝试攻击最近 NPC
            entities = w0_data.get("entities", []) if isinstance(w0_data, dict) else []
            target = self._pick_attack_target(entities)
            if target:
                action_result = self._safe_execute("attack", {"target_id": target})
                action_label = f"attack:{target}"
        elif index % 4 == 2:
            # 移动到附近一点
            player_pos = self._get_player_pos(w0_data)
            if player_pos:
                goal = (player_pos[0] + 5.0, player_pos[1], player_pos[2])
                action_result = self._safe_execute("move",
                    {"x": goal[0], "y": goal[1], "z": goal[2]})
                action_label = "move:+5x"

        w3_data = self._build_w3_data(action_label, action_result)
        if action_result is not None:
            detail["action"] = action_label
            detail["action_status"] = action_result.get("status")
            detail["action_channel"] = action_result.get("channel")
            detail["action_control_tier"] = action_result.get("control_tier")
            detail["action_reject_reason"] = action_result.get("reject_reason")
            detail["action_receipt_reason"] = action_result.get("receipt_reason")
            detail["action_outcome_semantics"] = action_result.get("outcome_semantics")
            detail["action_confirmation_status"] = action_result.get(
                "confirmation_status")
            stashed = self.executor.pop_stashed_events()
            if stashed and isinstance(w0_data, dict):
                events = w0_data.setdefault("events", [])
                if isinstance(events, list):
                    events.extend(stashed)
                    detail["stashed_merged"] = len(stashed)
                    detail["ok_events"] += len(stashed)

        # ── 归档 ──
        try:
            entry = self.recorder.record_round(
                round_id,
                w0_data=w0_data,
                w2_data={"axioms": [], "cumulative_counterexamples": 0,
                         "downgrade_threshold": 5, "model_update_count": 0},
                w3_data=w3_data,
            )
            detail["c_h_total"] = entry["c_h"]["total"]
        except Exception as e:
            detail["status"] = "archive_error"
            detail["error"] = f"{type(e).__name__}: {e}"
            detail["fatal"] = True
            return detail

        return detail

    def _pick_attack_target(self, entities):
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            if ent.get("kind") == "npc" and ent.get("entity_id"):
                return ent["entity_id"]
        return None

    def _get_player_pos(self, w0_data):
        if not isinstance(w0_data, dict):
            return None
        for ent in w0_data.get("entities", []):
            if isinstance(ent, dict) and ent.get("kind") == "player":
                pos = ent.get("position")
                if isinstance(pos, dict):
                    vals = (pos.get("x"), pos.get("y"), pos.get("z"))
                    if all(isinstance(v, (int, float)) for v in vals):
                        return tuple(float(v) for v in vals)
        return None

    def _safe_execute(self, action_type, params):
        try:
            return self.executor.execute(action_type, params)
        except Exception as e:
            log.warning("action %s failed (non-fatal): %s", action_type, e)
            return {
                "action_type": action_type,
                "status": "error",
                "channel": None,
                "control_tier": None,
                "reject_reason": str(e),
                "receipt_reason": None,
                "outcome_semantics": "executor_exception",
                "confirmation_status": "not_started",
                "error": str(e),
            }

    @staticmethod
    def _build_w3_data(action_label, action_result):
        if action_label is None and action_result is None:
            return None
        action_result = action_result or {}
        return {
            "action": action_label,
            "action_status": action_result.get("status"),
            "action_channel": action_result.get("channel"),
            "action_control_tier": action_result.get("control_tier"),
            "action_reject_reason": action_result.get("reject_reason"),
            "action_receipt_reason": action_result.get("receipt_reason"),
            "action_outcome_semantics": action_result.get("outcome_semantics"),
            "action_confirmation_status": action_result.get(
                "confirmation_status"),
        }

    @staticmethod
    def _is_confirmed_action(detail):
        confirmation = detail.get("action_confirmation_status")
        if confirmation == "confirmed":
            return True
        semantics = detail.get("action_outcome_semantics")
        return semantics in CONFIRMED_ACTION_OUTCOMES

    def print_report(self):
        r = self.results
        print(f"\n{'='*60}")
        print(f"S7 Stability Run Report")
        print(f"{'='*60}")
        print(f"Rounds: {r['total_rounds']} | Completed: {r['completed']} | Failed: {r['failed']}")
        print(f"Interruption: {r['interruption']}" +
              (f" ({r['interruption_reason']})" if r['interruption_reason'] else ""))
        print(f"Total samples: {r['total_samples']}")
        print(f"Total dirty: {r['total_dirty']} (entities={r['total_invalid_entities']}, events={r['total_invalid_events']})")
        print(f"Dirty ratio: {r['dirty_ratio']:.4f} (threshold: {DIRTY_RATIO_THRESHOLD})")
        print("Actions: "
              f"completed={r['action_completed']} | "
              f"confirmed={r['action_completed_confirmed']} | "
              f"unconfirmed={r['action_completed_unconfirmed']} | "
              f"rejected_or_failed={r['action_rejected_or_failed']}")
        print(f"Result: {'PASS' if r['passed'] else 'FAIL'}")
        print(f"{'='*60}")
        for d in r["round_details"]:
            action_str = d.get("action") or "-"
            status = "OK" if d["status"] == "ok" else f"FAIL:{d['status']}"
            action_state = d.get("action_status") or "-"
            confirm_state = d.get("action_confirmation_status") or "-"
            semantics = d.get("action_outcome_semantics") or "-"
            print(f"  {d['round_id']:>4} | {status:20s} | ents={d['ok_entities']:3d} evts={d['ok_events']:3d} "
                  f"| dirty={d['invalid_entities']+d['invalid_events']:2d} "
                  f"| action={action_str:15s} | a={action_state}/{confirm_state}/{semantics} "
                  f"| c_H={d['c_h_total']}")


def main():
    parser = argparse.ArgumentParser(description="S7 Stability Runner")
    parser.add_argument("--port", type=int, default=20770)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--delay", type=float, default=0.0, help="delay between rounds (seconds)")
    parser.add_argument("--log", type=str, default=None, help="log file path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    connector = GameConnector.from_config()
    connector.port = args.port
    connector.start()
    log.info("waiting for game connection on port %s...", args.port)
    if not connector.wait_for_game(timeout=60.0):
        log.error("game did not connect within 60s")
        connector.stop()
        sys.exit(1)
    log.info("game connected")

    sampler = DataSampler(connector)
    executor = ActionExecutor.from_config(connector)
    log_path = args.log or str(Path(_SRC).parent / "logs" / "stability_test.jsonl")
    recorder = LogRecorder(log_path)

    runner = StabilityRunner(connector, sampler, executor, recorder, rounds=args.rounds,
                             round_delay_s=args.delay)
    report = runner.run()
    runner.print_report()

    connector.stop()
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
