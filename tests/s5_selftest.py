import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w0.game_connector import GameConnector
from w3.action_executor import ActionExecutor
from mock_game_server import MockGameServer, MockCallError

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except AssertionError as e:
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}: {e}")
    except Exception as e:
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")


class ActionMock(MockGameServer):
    def __init__(self, port):
        super().__init__(port=port, initial_scenario="normal")
        self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.attack_policy = {"accept": True, "evidence_delay_s": 0.15}
        self.move_policy = {"accept": True, "move_delay_s": 0.15}
        self.pending_events = []
        self.move_plan = None
        self.actions_seen = []
        self._next_action = 0
        self.handlers["player_attack"] = self._h_player_attack
        self.handlers["player_move"] = self._h_player_move

    def reset(self, attack_policy=None, move_policy=None):
        with self._lock:
            self.attack_policy = attack_policy or {"accept": True, "evidence_delay_s": 0.15}
            self.move_policy = move_policy or {"accept": True, "move_delay_s": 0.15}
            self.pending_events = []
            self.move_plan = None
            self.actions_seen = []
            self.player_pos = {"x": 0.0, "y": 0.0, "z": 0.0}

    def _aid(self):
        with self._lock:
            self._next_action += 1
            return "mock-act-%d" % self._next_action

    def _h_player_attack(self, args):
        with self._lock:
            self.actions_seen.append(("player_attack", copy.deepcopy(args)))
            pol = dict(self.attack_policy)
        if pol.get("raise"):
            raise MockCallError("simulated_mod_failure")
        aid = self._aid()
        if pol.get("malformed"):
            return {"weird": 1}
        if not pol.get("accept", True):
            return {"action_id": aid, "accepted": False, "reason": "target_not_found"}
        target = args.get("target_id") if isinstance(args, dict) else None
        with self._lock:
            self.pending_events.append((
                time.time() + pol.get("evidence_delay_s", 0.15),
                {"event_id": "ev_" + aid, "kind": "combat_attack",
                 "source_entity": "EID:1", "target_entity": target,
                 "game_ts": time.time()},
            ))
        return {"action_id": aid, "accepted": True}

    def _h_player_move(self, args):
        with self._lock:
            self.actions_seen.append(("player_move", copy.deepcopy(args)))
            pol = dict(self.move_policy)
        aid = self._aid()
        if not pol.get("accept", True):
            return {"action_id": aid, "accepted": False, "reason": "nav_blocked"}
        with self._lock:
            self.move_plan = {
                "ready_ts": time.time() + pol.get("move_delay_s", 0.15),
                "pos": {"x": float(args["x"]), "y": float(args["y"]), "z": float(args["z"])},
            }
        return {"action_id": aid, "accepted": True}

    def _h_scan_entities(self, args):
        with self._lock:
            if self.move_plan is not None and time.time() >= self.move_plan["ready_ts"]:
                self.player_pos = self.move_plan["pos"]
                self.move_plan = None
            pos = dict(self.player_pos)
        return {"count": 2, "entities": [
            {"entity_id": "EID:1", "kind": "player", "labels": ["player"],
             "hp": 100.0, "position": pos},
            {"entity_id": "npc_target", "kind": "npc", "labels": ["hostile"],
             "hp": 100.0, "position": {"x": 5.0, "y": 0.0, "z": 0.0}},
        ]}

    def _h_get_events(self, args):
        now = time.time()
        out = []
        with self._lock:
            still = []
            for ready_ts, ev in self.pending_events:
                if now >= ready_ts:
                    out.append(ev)
                else:
                    still.append((ready_ts, ev))
            self.pending_events = still
        return {"count": len(out), "events": out}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 20773
    gc = GameConnector(host="127.0.0.1", port=port, response_timeout_s=5.0,
                       heartbeat_interval_s=0.5, heartbeat_miss_count=3)
    gc.start()
    mock = ActionMock(port=port)
    mock.connect()
    assert gc.wait_for_game(timeout=5.0), "mock connection not detected"
    ex = ActionExecutor(gc, poll_interval_s=0.05, attack_timeout_s=3.0,
                        move_timeout_s=3.0, arrival_eps_m=0.5, move_min_progress_m=0.5)

    def t_whitelist_scope():
        # S30：kill 入白名单（native 通道，半径制）；前三项仍走 connector。
        assert ActionExecutor.allowed_actions() == \
            ("attack", "move", "pickup", "kill")

    check("whitelist scope is exactly attack+move+pickup+kill (S29.7/S30)", t_whitelist_scope)

    def t_whitelist_blocks():
        mock.reset()
        before = len(mock.call_log)
        r = ex.execute("intrude", {"target_id": "turret_1"})
        assert r["status"] == "rejected", r
        assert r["reject_reason"] == "not_in_whitelist", r
        assert r["fn"] is None and r["action_id"] is None, r
        assert len(mock.call_log) == before, "whitelist breach: traffic was sent"

    check("non-whitelisted action blocked before any wire traffic", t_whitelist_blocks)

    def t_invalid_params():
        mock.reset()
        before = len(mock.call_log)
        r1 = ex.execute("attack", {"target_id": ""})
        r2 = ex.execute("move", {"x": "not_a_number", "y": 0.0, "z": 0.0})
        assert r1["status"] == "rejected" and r1["reject_reason"].startswith("invalid_params"), r1
        assert r2["status"] == "rejected" and r2["reject_reason"].startswith("invalid_params"), r2
        assert len(mock.call_log) == before, "invalid params leaked to wire"

    check("invalid params rejected locally with reason", t_invalid_params)

    def t_attack_completed():
        mock.reset()
        r = ex.execute("attack", {"target_id": "npc_target"}, timeout_s=2.0)
        assert r["status"] == "completed", r
        assert r["accepted"] is True and r["action_id"], r
        assert r["issued_ts"] is not None and r["duration_s"] is not None, r
        assert len(r["evidence"]) == 1, r["evidence"]
        evd = r["evidence"][0]
        assert evd["kind"] == "combat_attack", evd
        assert evd["target_entity"] == "npc_target", evd
        assert r["polls"] >= 1, r

    check("attack completes on observed combat evidence (receipt+evidence)", t_attack_completed)

    def t_attack_stashes_nonmatching_events():
        mock.reset()
        ex.pop_stashed_events()
        mock.pending_events.append((time.time() - 1.0, {
            "event_id": "stashed-1", "kind": "hp_changed",
            "source_entity": "EID:9", "target_entity": "EID:9", "game_ts": time.time(),
        }))
        r = ex.execute("attack", {"target_id": "npc_target"}, timeout_s=2.0)
        assert r["status"] == "completed", r
        assert all(ev.get("event_id") != "stashed-1" for ev in r["evidence"]), r["evidence"]
        stashed = ex.pop_stashed_events()
        assert len(stashed) == 1 and stashed[0]["event_id"] == "stashed-1", stashed
        assert ex.pop_stashed_events() == [], "pop_stashed_events must clear the stash"

    check("non-matching events stashed and popped", t_attack_stashes_nonmatching_events)

    def t_attack_mod_reject():
        mock.reset(attack_policy={"accept": False})
        r = ex.execute("attack", {"target_id": "npc_target"}, timeout_s=1.0)
        assert r["status"] == "rejected", r
        assert r["accepted"] is False and r["action_id"], r
        assert r["reject_reason"] == "target_not_found", r
        assert r["polls"] == 0, "no observation polling after mod rejection"

    check("mod-side rejection surfaced without polling", t_attack_mod_reject)

    def t_malformed_receipt():
        mock.reset(attack_policy={"accept": True, "malformed": True})
        r = ex.execute("attack", {"target_id": "npc_target"}, timeout_s=1.0)
        assert r["status"] == "error", r
        assert r["reject_reason"] == "malformed_receipt", r

    check("malformed receipt flagged as error (no black-box acceptance)", t_malformed_receipt)

    def t_call_error():
        mock.reset(attack_policy={"accept": True, "raise": True})
        r = ex.execute("attack", {"target_id": "npc_target"}, timeout_s=1.0)
        assert r["status"] == "error", r
        assert r["reject_reason"].startswith("call_failed"), r

    check("mod call failure surfaces as structured error", t_call_error)

    def t_move_completed():
        mock.reset()
        r = ex.execute("move", {"x": 3.0, "y": 0.0, "z": 0.0}, timeout_s=2.0)
        assert r["status"] == "completed", r
        assert r["accepted"] is True and r["evidence"], r
        last = r["evidence"][-1]["position"]
        assert abs(last["x"] - 3.0) < 1e-6, last
        assert len(r["evidence"][0]["position"]) == 3, r["evidence"][0]

    check("move completes on observed position change", t_move_completed)

    def t_move_timeout():
        mock.reset(move_policy={"accept": True, "move_delay_s": 999.0})
        r = ex.execute("move", {"x": 3.0, "y": 0.0, "z": 0.0}, timeout_s=0.5)
        assert r["status"] == "timeout", r
        assert r["accepted"] is True and r["polls"] >= 2, r
        for sample in r["evidence"]:
            assert abs(sample["position"]["x"]) < 1e-6, sample

    check("move times out with position trace when nothing moves", t_move_timeout)

    mock.close()
    gc.stop()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== S5 selftest: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ===")
    if failed:
        for name, _, err in failed:
            print(f"  failed: {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
