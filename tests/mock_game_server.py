import copy
import json
import socket
import sys
import threading
import time
from pathlib import Path

DEFAULT_SCENARIOS_PATH = Path(__file__).resolve().parent / "mock_scenarios.json"
MOD_NAME = "CufBridgeMock"
MOD_VERSION = "0.1.0"


class MockGameServer:
    def __init__(self, host="127.0.0.1", port=20770, scenarios_path=None,
                 initial_scenario="normal"):
        self.host = host
        self.port = port
        self.scenarios_path = Path(scenarios_path) if scenarios_path else DEFAULT_SCENARIOS_PATH
        self.scenarios = json.loads(self.scenarios_path.read_text(encoding="utf-8"))
        if initial_scenario not in self.scenarios:
            raise ValueError(f"unknown initial scenario: {initial_scenario}")
        self._lock = threading.Lock()
        self.scenario = initial_scenario
        self.round_id = 0
        self.event_queue = copy.deepcopy(self.scenarios[initial_scenario]["events"])
        self.sock = None
        self._stop = threading.Event()
        self._thread = None
        self._send_lock = threading.Lock()
        self.call_log = []
        self.handlers = {
            "echo": self._h_echo,
            "add": self._h_add,
            "lua_version": self._h_lua_version,
            "list_fns": self._h_list_fns,
            "scan_entities": self._h_scan_entities,
            "get_events": self._h_get_events,
            "get_round_info": self._h_get_round_info,
            "set_scenario": self._h_set_scenario,
        }

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self._stop.clear()
        self._send({"t": "hello", "mod": MOD_NAME, "mod_version": MOD_VERSION,
                    "proto": 2, "lua_version": "Lua 5.1 (mock)"})
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def connect_with_retry(self, interval_s=2.0):
        while not self._stop.is_set():
            try:
                self.connect()
                return True
            except OSError as e:
                print(f"[mock] connect failed ({e}), retry in {interval_s}s", flush=True)
                if self.sock is not None:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                    self.sock = None
                time.sleep(interval_s)
        return False

    def _send(self, obj):
        data = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\r\n").encode("utf-8")
        with self._send_lock:
            if self.sock is None:
                raise OSError("socket closed")
            self.sock.sendall(data)

    def _reader(self):
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip(b"\r")
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    self._handle(msg)
        except OSError:
            pass

    def _handle(self, msg):
        t = msg.get("t")
        rid = msg.get("id")
        if t == "ping":
            self._send({"t": "pong", "id": rid})
            return
        if t != "call":
            self._send({"t": "resp", "id": rid, "ok": False,
                        "error": f"unknown_type:{t}"})
            return
        fn = msg.get("fn", "")
        args = msg.get("args") or []
        with self._lock:
            self.call_log.append({"fn": fn, "args": args, "id": rid})
        h = self.handlers.get(fn)
        if h is None:
            self._send({"t": "resp", "id": rid, "ok": False,
                        "error": f"unknown_fn:{fn}"})
            return
        try:
            data = h(args)
            self._send({"t": "resp", "id": rid, "ok": True, "data": data})
        except MockCallError as e:
            self._send({"t": "resp", "id": rid, "ok": False, "error": str(e)})
        except Exception as e:
            self._send({"t": "resp", "id": rid, "ok": False,
                        "error": f"handler_error:{type(e).__name__}:{e}"})

    def _h_echo(self, args):
        return args

    def _h_add(self, args):
        return args[0] + args[1]

    def _h_lua_version(self, args):
        return "Lua 5.1 (mock)"

    def _h_list_fns(self, args):
        return sorted(self.handlers.keys())

    def _h_scan_entities(self, args):
        radius = 20.0
        limit = 30
        if isinstance(args, dict):
            if isinstance(args.get("radius"), (int, float)):
                radius = args["radius"]
            if isinstance(args.get("limit"), (int, float)):
                limit = args["limit"]
        with self._lock:
            ents = copy.deepcopy(self.scenarios[self.scenario]["entities"])
        return {"radius": radius, "limit": limit, "count": len(ents), "entities": ents}

    def _h_get_events(self, args):
        with self._lock:
            out = self.event_queue
            self.event_queue = []
            return {"count": len(out), "events": out}

    def _h_get_round_info(self, args):
        with self._lock:
            return {"round_id": self.round_id, "scenario": self.scenario,
                    "pending_events": len(self.event_queue)}

    def _h_set_scenario(self, args):
        name = args[0] if args else None
        if name not in self.scenarios:
            raise MockCallError(f"unknown_scenario:{name}")
        with self._lock:
            self.scenario = name
            self.round_id += 1
            self.event_queue = copy.deepcopy(self.scenarios[name]["events"])
            return {"round_id": self.round_id, "scenario": self.scenario,
                    "entity_count": len(self.scenarios[name]["entities"]),
                    "event_count": len(self.event_queue)}

    def close(self):
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class MockCallError(Exception):
    pass


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="CUF-QS mock game endpoint (proto 2)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20770)
    ap.add_argument("--scenario", default="normal",
                    help="initial scenario name")
    ap.add_argument("--scenarios", default=None,
                    help="path to scenarios json")
    args = ap.parse_args()
    mock = MockGameServer(host=args.host, port=args.port,
                          scenarios_path=args.scenarios,
                          initial_scenario=args.scenario)
    print(f"[mock] connecting to {args.host}:{args.port} as {MOD_NAME} "
          f"(scenario={args.scenario}) ...", flush=True)
    try:
        if not mock.connect_with_retry():
            sys.exit(1)
        print("[mock] connected, serving preset scenarios (Ctrl+C to exit)", flush=True)
        while not mock._stop.is_set():
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        mock.close()
    print("[mock] stopped", flush=True)


if __name__ == "__main__":
    _cli()
