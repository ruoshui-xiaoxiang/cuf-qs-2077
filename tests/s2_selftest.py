import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "_archive"))  # fake_game_client 已归档（系统整理）

from w0.game_connector import (
    GameConnector,
    GameConnectorError,
    NotConnectedError,
    ResponseTimeoutError,
)
from fake_game_client import FakeGameClient

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


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 20770
    gc = GameConnector(host="127.0.0.1", port=port, response_timeout_s=5.0,
                       heartbeat_interval_s=0.5, heartbeat_miss_count=3)
    gc.start()
    client = FakeGameClient(port=port)

    def t_connect_hello():
        client.connect()
        assert gc.wait_for_game(timeout=5.0), "game connection not detected"
        assert gc.is_game_connected
        assert wait_until(lambda: gc.game_info is not None), "hello not received"
        assert gc.game_info.get("mod") == "CufBridgeFake", gc.game_info

    check("connect + hello handshake", t_connect_hello)

    def t_heartbeat():
        assert wait_until(lambda: gc.last_pong_ts is not None, timeout=3.0), \
            "heartbeat ping/pong never happened"
        before = gc.last_pong_ts
        assert wait_until(lambda: gc.last_pong_ts > before, timeout=3.0), \
            "heartbeat not recurring"
        assert gc.heartbeat_misses == 0

    check("heartbeat keeps ponging", t_heartbeat)

    def t_ping():
        latency_ms = gc.ping()
        assert latency_ms > 0, latency_ms

    check("ping returns positive latency", t_ping)

    def t_call_basic():
        data = gc.execute_action("add", [1, 1])
        assert data == 2, data

    check("call add(1,1) == 2", t_call_basic)

    def t_call_echo():
        data = gc.execute_action("echo", ["abc"])
        assert data == ["abc"], data

    check("call echo roundtrip", t_call_echo)

    def t_concurrent():
        errors = []

        def worker(i):
            payload = f"concurrent_{i}"
            try:
                data = gc.execute_action("echo", [payload])
                if data != [payload]:
                    errors.append(f"{payload} -> {data}")
            except Exception as e:
                errors.append(f"{payload} raised {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)
        assert not errors, errors

    check("8 concurrent call requests", t_concurrent)

    def t_timeout():
        client.drop_fns.add("no_reply")
        try:
            gc.execute_action("no_reply", timeout=0.5)
        except ResponseTimeoutError:
            return
        raise AssertionError("expected ResponseTimeoutError")

    check("unanswered call raises ResponseTimeoutError", t_timeout)

    def t_lua_error():
        client.fail_fns.add("boom")
        try:
            gc.execute_action("boom")
        except GameConnectorError as e:
            assert not isinstance(e, (NotConnectedError, ResponseTimeoutError))
            assert "fake lua error" in str(e), str(e)
            return
        raise AssertionError("expected GameConnectorError")

    check("lua-side failure surfaces as GameConnectorError", t_lua_error)

    def t_heartbeat_force_disconnect():
        client.freeze = True
        assert wait_until(lambda: not gc.is_game_connected, timeout=10.0), \
            "heartbeat threshold exceeded but stale connection not force-dropped"
        assert gc.heartbeat_misses >= 3, gc.heartbeat_misses
        client.freeze = False

    check("heartbeat misses force disconnect", t_heartbeat_force_disconnect)

    def t_disconnect():
        client.close()
        assert wait_until(lambda: not gc.is_game_connected, timeout=5.0), \
            "disconnect not detected"
        try:
            gc.execute_action("add", [1, 1], timeout=1.0)
        except NotConnectedError:
            return
        except ResponseTimeoutError:
            return
        raise AssertionError("expected NotConnectedError/ResponseTimeoutError after disconnect")

    check("disconnect detection + pending request failure", t_disconnect)

    def t_reconnect():
        client2 = FakeGameClient(port=port)
        client2.connect()
        assert gc.wait_for_game(timeout=5.0), "reconnect not detected"
        try:
            data = gc.execute_action("add", [1, 1])
            assert data == 2, data
        finally:
            client2.close()

    check("reconnect restores call capability", t_reconnect)

    gc.stop()

    def t_stop_clean():
        assert not gc.is_game_connected
        try:
            gc.execute_action("add", [1, 1], timeout=0.5)
        except GameConnectorError:
            return
        raise AssertionError("expected error after stop")

    check("stopped connector rejects requests", t_stop_clean)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== S2 selftest: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ===")
    if failed:
        for name, _, err in failed:
            print(f"  failed: {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
