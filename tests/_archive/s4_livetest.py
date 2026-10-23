import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector
from w0.entity_schema import validate_entity, validate_event


def tally(events):
    out = {}
    for x in events:
        out[x.get("kind")] = out.get(x.get("kind"), 0) + 1
    return out


def main():
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    port = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if port is not None:
        gc = GameConnector(host="127.0.0.1", port=port, response_timeout_s=5.0)
    else:
        gc = GameConnector.from_config()
    gc.start()
    print(f"listening on {gc.host}:{gc.port}, waiting for game (timeout {timeout:.0f}s) ...", flush=True)
    if not gc.wait_for_game(timeout=timeout):
        print(f"TIMEOUT: game did not connect within {timeout:.0f}s")
        gc.stop()
        sys.exit(1)
    deadline = time.time() + 10.0
    while gc.game_info is None and time.time() < deadline:
        time.sleep(0.1)
    print(f"hello: {gc.game_info}", flush=True)

    fns = gc.execute_action("list_fns")
    print(f"list_fns -> {fns}", flush=True)
    assert "scan_entities" in fns and "get_events" in fns, fns

    fails = []

    scan = gc.execute_action("scan_entities")
    ents = scan.get("entities", [])
    print(f"scan_entities count={scan.get('count')} radius={scan.get('radius')} limit={scan.get('limit')}", flush=True)
    if scan.get("count", 0) < 1:
        fails.append(f"scan_entities returned no entities: {scan}")
    for e in ents:
        errs = validate_entity(e)
        if errs:
            fails.append(f"entity {e.get('entity_id')!r}: {errs}")
    print(f"kinds: {sorted(set(e.get('kind') for e in ents))}", flush=True)
    hostile = [e.get("entity_id") for e in ents if "hostile" in e.get("labels", [])]
    print(f"hostile: {hostile}", flush=True)

    time.sleep(3.0)
    ev = gc.execute_action("get_events")
    events = ev.get("events", [])
    print(f"get_events count={ev.get('count')} kinds={tally(events)}", flush=True)
    for x in events:
        errs = validate_event(x)
        if errs:
            fails.append(f"event {x.get('event_id')!r}: {errs}")

    ev2 = gc.execute_action("get_events")
    if ev2.get("count", 0) != 0:
        fails.append(f"get_events did not drain queue: {ev2}")
    else:
        print("get_events drain check ok", flush=True)

    print("waiting 8s for scan-diff events (move / attack in game for richer data) ...", flush=True)
    time.sleep(8.0)
    ev3 = gc.execute_action("get_events")
    events3 = ev3.get("events", [])
    print(f"events after wait: count={ev3.get('count')} kinds={tally(events3)}", flush=True)
    for x in events3:
        errs = validate_event(x)
        if errs:
            fails.append(f"event {x.get('event_id')!r}: {errs}")

    if fails:
        print("FAIL:")
        for f in fails:
            print(" -", f)
        gc.stop()
        sys.exit(1)
    print("S4 LIVE TEST PASSED", flush=True)
    gc.stop()


if __name__ == "__main__":
    main()
