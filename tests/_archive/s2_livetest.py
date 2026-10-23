import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w0.game_connector import GameConnector


def main():
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
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
    lat = gc.ping()
    print(f"ping latency: {lat:.2f} ms", flush=True)
    data = gc.execute_action("add", [1, 1])
    print(f"call add(1,1) -> {data!r}", flush=True)
    assert data == 2, data
    ver = gc.execute_action("lua_version")
    print(f"call lua_version() -> {ver!r}", flush=True)
    assert ver == "Lua 5.1", ver
    print("LIVE TEST PASSED", flush=True)
    time.sleep(3.0)
    gc.stop()


if __name__ == "__main__":
    main()
