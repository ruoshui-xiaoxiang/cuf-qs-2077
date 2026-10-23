import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w0.game_connector import GameConnector, GameConnectorError
from mock_game_server import MockGameServer

RESULTS = []

ENTITY_FIELDS = {"entity_id", "kind", "labels", "hp", "position"}
EVENT_FIELDS = {"event_id", "kind", "source_entity", "target_entity", "game_ts"}


def unwrap_entities(resp):
    return resp["entities"] if isinstance(resp, dict) else resp


def unwrap_events(resp):
    return resp["events"] if isinstance(resp, dict) else resp


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


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 20770
    gc = GameConnector(host="127.0.0.1", port=port, response_timeout_s=5.0,
                       heartbeat_interval_s=0.5, heartbeat_miss_count=3)
    gc.start()
    mock = MockGameServer(port=port, initial_scenario="normal")

    def t_connect_hello():
        mock.connect()
        assert gc.wait_for_game(timeout=5.0), "mock connection not detected"
        assert wait_until(lambda: gc.game_info is not None), "hello not received"
        assert gc.game_info.get("mod") == "CufBridgeMock", gc.game_info
        assert gc.game_info.get("proto") == 2, gc.game_info

    check("mock connect + hello handshake", t_connect_hello)

    def t_heartbeat():
        assert wait_until(lambda: gc.last_pong_ts is not None, timeout=3.0), \
            "heartbeat ping/pong never happened"

    check("heartbeat keeps ponging", t_heartbeat)

    def t_scan_entities():
        resp = gc.execute_action("scan_entities")
        entities = unwrap_entities(resp)
        assert isinstance(entities, list) and len(entities) == 7, len(entities)
        for ent in entities:
            assert set(ent.keys()) == ENTITY_FIELDS, ent
            assert isinstance(ent["labels"], list) and ent["labels"], ent
            assert set(ent["position"].keys()) == {"x", "y", "z"}, ent

    check("scan_entities returns schema-complete entities", t_scan_entities)

    def t_get_events_drain():
        events = unwrap_events(gc.execute_action("get_events"))
        assert isinstance(events, list) and len(events) == 3, len(events)
        for ev in events:
            assert set(ev.keys()) == EVENT_FIELDS, ev
        again = unwrap_events(gc.execute_action("get_events"))
        assert again == [], again

    check("get_events drains queue (second call empty)", t_get_events_drain)

    def t_round_info():
        info = gc.execute_action("get_round_info")
        assert info["scenario"] == "normal", info
        assert info["round_id"] == 0, info
        assert info["pending_events"] == 0, info

    check("get_round_info reflects drained state", t_round_info)

    def t_switch_anomaly():
        r = gc.execute_action("set_scenario", ["anomaly"])
        assert r["scenario"] == "anomaly" and r["round_id"] == 1, r
        entities = unwrap_entities(gc.execute_action("scan_entities"))
        assert len(entities) == 4, len(entities)
        hostile_civ = [e for e in entities
                       if "civilian" in e["labels"] and "hostile" in e["labels"]]
        assert len(hostile_civ) == 1, entities
        neg_hp = [e for e in entities if e["hp"] < 0]
        assert len(neg_hp) == 1, entities

    check("set_scenario anomaly injects label conflicts", t_switch_anomaly)

    def t_anomaly_events():
        events = unwrap_events(gc.execute_action("get_events"))
        assert len(events) == 3, len(events)
        kinds = {ev["kind"] for ev in events}
        assert {"combat_attack", "entity_death", "intrusion_attempt"} <= kinds, kinds

    check("anomaly scenario events drain correctly", t_anomaly_events)

    def t_switch_empty():
        r = gc.execute_action("set_scenario", ["empty"])
        assert r["scenario"] == "empty" and r["entity_count"] == 0, r
        assert unwrap_entities(gc.execute_action("scan_entities")) == []
        assert unwrap_events(gc.execute_action("get_events")) == []

    check("set_scenario empty returns empty world", t_switch_empty)

    def t_unknown_scenario():
        try:
            gc.execute_action("set_scenario", ["no_such_scenario"])
        except GameConnectorError as e:
            assert "unknown_scenario:no_such_scenario" in str(e), str(e)
            return
        raise AssertionError("expected GameConnectorError")

    check("unknown scenario surfaces structured error", t_unknown_scenario)

    def t_unknown_fn():
        try:
            gc.execute_action("not_registered_fn")
        except GameConnectorError as e:
            assert "unknown_fn:not_registered_fn" in str(e), str(e)
            return
        raise AssertionError("expected GameConnectorError")

    check("unknown fn surfaces structured error", t_unknown_fn)

    def t_list_fns():
        fns = gc.execute_action("list_fns")
        for required in ("scan_entities", "get_events", "get_round_info", "set_scenario"):
            assert required in fns, fns

    check("list_fns exposes all collectors", t_list_fns)

    mock.close()
    gc.stop()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== S3 selftest: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ===")
    if failed:
        for name, _, err in failed:
            print(f"  failed: {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
