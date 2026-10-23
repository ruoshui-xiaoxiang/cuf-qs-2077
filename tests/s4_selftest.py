import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w0.game_connector import GameConnector
from w0.data_sampler import DataSampler
from mock_game_server import MockGameServer

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


def wait_until(pred, timeout=5.0, interval=0.05):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def ent(eid, hp=100.0, labels=("civilian",), kind="npc"):
    return {"entity_id": eid, "kind": kind, "labels": list(labels), "hp": hp,
            "position": {"x": 1.0, "y": 2.0, "z": 3.0}}


def ev(eid, kind="combat_attack", src="npc_a", tgt="player", ts=100.5):
    return {"event_id": eid, "kind": kind, "source_entity": src,
            "target_entity": tgt, "game_ts": ts}


class ScriptedMock(MockGameServer):
    def __init__(self, port):
        super().__init__(port=port, initial_scenario="normal")
        self.scripted_entities = []
        self.scripted_events = []

    def set_script(self, entities, events):
        with self._lock:
            self.scripted_entities = copy.deepcopy(entities)
            self.scripted_events = copy.deepcopy(events)

    def _h_scan_entities(self, args):
        with self._lock:
            return copy.deepcopy(self.scripted_entities)

    def _h_get_events(self, args):
        with self._lock:
            out = self.scripted_events
            self.scripted_events = []
            return out


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 20770
    gc = GameConnector(host="127.0.0.1", port=port, response_timeout_s=5.0,
                       heartbeat_interval_s=0.5, heartbeat_miss_count=3)
    gc.start()
    mock = ScriptedMock(port=port)
    mock.connect()
    assert gc.wait_for_game(timeout=5.0), "mock connection not detected"
    assert wait_until(lambda: gc.game_info is not None), "hello not received"

    def t_normal_round():
        mock.set_script(
            [ent("player", hp=100.0, labels=("player",), kind="player"),
             ent("npc_a", labels=("civilian", "vendor")),
             ent("turret_1", labels=("device", "hostile"), kind="device")],
            [ev("e1"), ev("e2", kind="intrusion_attempt", src="player", tgt="turret_1")],
        )
        sampler = DataSampler(gc)
        pkt = sampler.collect_round(round_id=7)
        assert pkt["round_id"] == 7, pkt
        assert isinstance(pkt["collection_ts"], float), pkt
        assert len(pkt["entities"]) == 3, pkt["entities"]
        for e in pkt["entities"]:
            assert e["sampling_status"] == "ok", e
        assert len(pkt["events"]) == 2, pkt["events"]
        for v in pkt["events"]:
            assert v["round_id"] == 7, v
            assert isinstance(v["collection_ts"], float), v
        s = pkt["stats"]
        assert s["raw_entities"] == 3 and s["ok_entities"] == 3, s
        assert s["invalid_entities"] == 0 and s["failed_entities"] == 0, s
        assert s["anomalous_entities"] == 0 and s["invalid_events"] == 0, s

    check("normal round: all ok + events stamped with round_id", t_normal_round)

    def t_missing_field_failed():
        mock.set_script([{"entity_id": "npc_bad", "kind": "npc"}], [])
        sampler = DataSampler(gc)
        pkt = sampler.collect_round(round_id=1)
        assert pkt["stats"]["invalid_entities"] == 1, pkt["stats"]
        assert pkt["stats"]["failed_entities"] == 1, pkt["stats"]
        assert pkt["stats"]["anomalous_entities"] == 0, pkt["stats"]
        rec = pkt["entities"][0]
        assert rec["sampling_status"] == "failed", rec
        assert rec["kind"] == "unknown" and rec["hp"] == 0.0, rec

    check("missing-field entity marked failed with fallback record", t_missing_field_failed)

    def t_three_strikes_anomalous():
        mock.set_script([{"entity_id": "npc_x", "kind": "npc"}], [])
        sampler = DataSampler(gc)
        statuses = []
        for r in (1, 2, 3):
            pkt = sampler.collect_round(round_id=r)
            statuses.append(pkt["entities"][0]["sampling_status"])
        assert statuses == ["failed", "failed", "anomalous"], statuses

    check("3 consecutive failures escalate to anomalous", t_three_strikes_anomalous)

    def t_recovery_resets_streak():
        sampler = DataSampler(gc)
        mock.set_script([{"entity_id": "npc_r", "kind": "npc"}], [])
        p1 = sampler.collect_round(round_id=1)
        assert p1["entities"][0]["sampling_status"] == "failed", p1
        mock.set_script([ent("npc_r", hp=55.0)], [])
        p2 = sampler.collect_round(round_id=2)
        e2 = p2["entities"][0]
        assert e2["sampling_status"] == "ok" and e2["hp"] == 55.0, e2
        mock.set_script([{"entity_id": "npc_r", "kind": "npc"}], [])
        p3 = sampler.collect_round(round_id=3)
        assert p3["entities"][0]["sampling_status"] == "failed", p3

    check("recovery resets fail streak (no stale anomalous)", t_recovery_resets_streak)

    def t_fallback_uses_last_valid():
        sampler = DataSampler(gc)
        mock.set_script([ent("npc_s", hp=88.0, labels=("civilian", "guard"))], [])
        sampler.collect_round(round_id=1)
        mock.set_script([{"entity_id": "npc_s", "kind": "npc"}], [])
        pkt = sampler.collect_round(round_id=2)
        rec = pkt["entities"][0]
        assert rec["sampling_status"] == "failed", rec
        assert rec["hp"] == 88.0 and rec["labels"] == ["civilian", "guard"], rec

    check("failed entity falls back to last valid snapshot", t_fallback_uses_last_valid)

    def t_invalid_event_dropped():
        mock.set_script(
            [ent("npc_a")],
            [{"kind": "combat_attack", "source_entity": "npc_a",
              "target_entity": "player", "game_ts": 1.0},
             ev("e_ok", kind="area_alert", src="turret_1", tgt=None)],
        )
        sampler = DataSampler(gc)
        pkt = sampler.collect_round(round_id=2)
        assert pkt["stats"]["invalid_events"] == 1, pkt["stats"]
        assert len(pkt["events"]) == 1, pkt["events"]
        assert pkt["events"][0]["event_id"] == "e_ok", pkt["events"]
        assert pkt["events"][0]["round_id"] == 2, pkt["events"]

    check("invalid event dropped, valid event stamped", t_invalid_event_dropped)

    def t_idless_entity_dropped():
        mock.set_script([{"kind": "npc", "labels": [], "hp": 1.0,
                          "position": {"x": 0.0, "y": 0.0, "z": 0.0}}], [])
        sampler = DataSampler(gc)
        pkt = sampler.collect_round(round_id=3)
        assert pkt["stats"]["invalid_entities"] == 1, pkt["stats"]
        assert pkt["entities"] == [], pkt["entities"]

    check("id-less invalid entity counted and dropped without crash", t_idless_entity_dropped)

    mock.close()
    gc.stop()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== S4 selftest: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ===")
    if failed:
        for name, _, err in failed:
            print(f"  failed: {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
