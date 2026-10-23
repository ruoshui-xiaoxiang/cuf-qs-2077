import json
import socket
import threading


class FakeGameClient:
    def __init__(self, host="127.0.0.1", port=20770,
                 mod="CufBridgeFake", mod_version="0.0.1"):
        self.host = host
        self.port = port
        self.mod = mod
        self.mod_version = mod_version
        self.sock = None
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.received = []
        self.drop_fns = set()
        self.fail_fns = set()
        self.results = {}
        self.freeze = False

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self._stop.clear()
        self._send({"t": "hello", "mod": self.mod, "mod_version": self.mod_version,
                    "proto": 2, "lua_version": "Lua 5.1 (fake)"})
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _send(self, obj):
        data = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
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
                    msg = json.loads(line.decode("utf-8"))
                    self.received.append(msg)
                    self._handle(msg)
        except (OSError, json.JSONDecodeError):
            pass

    def _handle(self, msg):
        if self.freeze:
            return
        t = msg.get("t")
        rid = msg.get("id")
        if t == "ping":
            self._send({"t": "pong", "id": rid})
            return
        if t != "call":
            return
        fn = msg.get("fn", "")
        args = msg.get("args") or []
        if fn in self.drop_fns:
            return
        if fn in self.fail_fns:
            self._send({"t": "resp", "id": rid, "ok": False, "error": "fake lua error"})
            return
        if fn in self.results:
            data = self.results[fn]
        elif fn == "add":
            data = args[0] + args[1]
        elif fn == "lua_version":
            data = "Lua 5.1 (fake)"
        elif fn == "echo":
            data = args
        else:
            data = {"echo": fn}
        self._send({"t": "resp", "id": rid, "ok": True, "data": data})

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
