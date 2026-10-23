import json
import logging
import socket
import threading
import time
from pathlib import Path

log = logging.getLogger("game_connector")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "experiment_config.json"


class GameConnectorError(Exception):
    pass


class NotConnectedError(GameConnectorError):
    pass


class ResponseTimeoutError(GameConnectorError):
    pass


class _Pending:
    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


class GameConnector:
    def __init__(self, host="127.0.0.1", port=20770, response_timeout_s=10.0,
                 heartbeat_interval_s=5.0, heartbeat_miss_count=3):
        self.host = host
        self.port = port
        self.response_timeout_s = float(response_timeout_s)
        self.heartbeat_interval_s = float(heartbeat_interval_s)
        self.heartbeat_miss_count = int(heartbeat_miss_count)

        self._server_sock = None
        self._client_sock = None
        self._send_lock = threading.Lock()
        self._conn_lock = threading.Lock()

        self._next_id = 0
        self._id_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()

        self._connected_event = threading.Event()
        self._stop_event = threading.Event()
        self._threads = []

        self.game_info = None
        self.last_pong_ts = None
        self.heartbeat_misses = 0
        self.on_connect_hooks = []
        self.on_disconnect_hooks = []

    @classmethod
    def from_config(cls, path=None):
        cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))["connection"]
        return cls(
            host=cfg.get("host", "127.0.0.1"),
            port=cfg.get("port", 20770),
            response_timeout_s=cfg.get("response_timeout_s", 10.0),
            heartbeat_interval_s=cfg.get("heartbeat_interval_s", 5.0),
            heartbeat_miss_count=cfg.get("heartbeat_miss_count", 3),
        )

    def start(self):
        if self._server_sock is not None:
            return
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(2)
        self._server_sock.settimeout(1.0)
        self._stop_event.clear()

        accept_t = threading.Thread(target=self._accept_loop, name="gc-accept", daemon=True)
        hb_t = threading.Thread(target=self._heartbeat_loop, name="gc-heartbeat", daemon=True)
        self._threads = [accept_t, hb_t]
        accept_t.start()
        hb_t.start()
        log.info("server listening on %s:%d", self.host, self.port)

    def stop(self):
        self._stop_event.set()
        with self._conn_lock:
            if self._client_sock is not None:
                try:
                    self._client_sock.close()
                except OSError:
                    pass
                self._client_sock = None
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        self._connected_event.clear()
        self._fail_all_pending(NotConnectedError("connector stopped"))
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []
        log.info("server stopped")

    @property
    def is_game_connected(self):
        return self._connected_event.is_set()

    def wait_for_game(self, timeout=None):
        return self._connected_event.wait(timeout)

    def _alloc_id(self):
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def _send(self, obj):
        data = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\r\n").encode("utf-8")
        with self._send_lock:
            sock = self._client_sock
            if sock is None:
                raise NotConnectedError("game is not connected")
            sock.sendall(data)

    def _request(self, obj, timeout):
        rid = obj["id"]
        p = _Pending()
        with self._pending_lock:
            self._pending[rid] = p
        try:
            self._send(obj)
            if not p.event.wait(timeout):
                raise ResponseTimeoutError(f"request id={rid} type={obj.get('t')} timed out after {timeout}s")
            if p.error is not None:
                raise p.error
            return p.result
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def ping(self, timeout=None):
        t0 = time.perf_counter()
        self._request({"t": "ping", "id": self._alloc_id()},
                      timeout if timeout is not None else self.response_timeout_s)
        return (time.perf_counter() - t0) * 1000.0

    def call_raw(self, fn, args=None, timeout=None):
        rid = self._alloc_id()
        if args is None:
            payload_args = []
        elif isinstance(args, (dict, list)):
            payload_args = args
        else:
            payload_args = list(args)
        resp = self._request({"t": "call", "id": rid, "fn": fn, "args": payload_args},
                             timeout if timeout is not None else self.response_timeout_s)
        if not isinstance(resp, dict) or resp.get("t") != "resp":
            raise GameConnectorError(f"unexpected response for id={rid}: {resp!r}")
        return resp

    def execute_action(self, fn, args=None, timeout=None):
        resp = self.call_raw(fn, args, timeout)
        if not resp.get("ok"):
            raise GameConnectorError(f"call {fn!r} failed: {resp.get('error')}")
        return resp.get("data")

    def _fail_all_pending(self, error):
        with self._pending_lock:
            items = list(self._pending.values())
            self._pending.clear()
        for p in items:
            p.error = error
            p.event.set()

    def _accept_loop(self):
        while not self._stop_event.is_set():
            try:
                conn, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            log.info("incoming connection from %s:%d", *addr)
            with self._conn_lock:
                old = self._client_sock
                self._client_sock = conn
            if old is not None:
                log.warning("replacing previous connection")
                try:
                    old.close()
                except OSError:
                    pass
            self.game_info = None
            self.heartbeat_misses = 0
            self._connected_event.set()
            reader = threading.Thread(target=self._reader_loop, args=(conn,),
                                      name="gc-reader", daemon=True)
            reader.start()
            for hook in self.on_connect_hooks:
                try:
                    hook()
                except Exception:
                    log.exception("on_connect hook failed")

    def _reader_loop(self, conn):
        buf = b""
        try:
            while not self._stop_event.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip(b"\r")
                    if not line:
                        continue
                    self._dispatch_line(line)
        except OSError as e:
            log.warning("reader error: %s", e)
        finally:
            self._force_disconnect(conn, "reader exited")

    def _force_disconnect(self, conn, reason):
        """强制断连统一处置（读循环退出 / 心跳超阈）。

        幂等：只有当前活动连接正是 conn 时才执行，避免重复触发 hook。
        """
        with self._conn_lock:
            if self._client_sock is not conn:
                return
            self._client_sock = None
            self._connected_event.clear()
        try:
            conn.close()
        except OSError:
            pass
        self._fail_all_pending(NotConnectedError(reason))
        log.warning("game disconnected: %s", reason)
        for hook in self.on_disconnect_hooks:
            try:
                hook()
            except Exception:
                log.exception("on_disconnect hook failed")

    def _dispatch_line(self, line):
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            log.error("malformed line dropped: %s (%r)", e, line[:200])
            return
        t = msg.get("t")
        if t == "hello":
            self.game_info = msg
            log.info("game hello: %s", json.dumps(msg, ensure_ascii=False))
        elif t == "pong":
            self.last_pong_ts = time.time()
            self.heartbeat_misses = 0
            self._resolve(msg.get("id"), msg, None)
        elif t == "resp":
            self._resolve(msg.get("id"), msg, None)
        elif t == "event":
            log.debug("event received: %s", json.dumps(msg, ensure_ascii=False)[:300])
        else:
            log.warning("unknown message type: %r", t)

    def _resolve(self, rid, result, error):
        if rid is None:
            return
        with self._pending_lock:
            p = self._pending.get(rid)
        if p is None:
            log.warning("response for unknown id=%s dropped", rid)
            return
        p.result = result
        p.error = error
        p.event.set()

    def _heartbeat_loop(self):
        while not self._stop_event.wait(self.heartbeat_interval_s):
            if not self._connected_event.is_set():
                continue
            try:
                self.ping(timeout=self.heartbeat_interval_s)
            except GameConnectorError as e:
                self.heartbeat_misses += 1
                if self.heartbeat_misses >= self.heartbeat_miss_count:
                    # 审计修复：原来只打日志不处置，僵死连接会一直挂着。
                    # 超阈即强制断连，让上层观测到 is_game_connected=False 并可重连。
                    log.warning("heartbeat lost x%d, forcing disconnect: %s",
                                self.heartbeat_misses, e)
                    with self._conn_lock:
                        conn = self._client_sock
                    if conn is not None:
                        self._force_disconnect(
                            conn, f"heartbeat lost x{self.heartbeat_misses}")


def _cli():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    gc = GameConnector.from_config()
    gc.start()
    print(f"CUF-QS game connector listening on {gc.host}:{gc.port}, waiting for game...", flush=True)
    try:
        while True:
            if gc.wait_for_game(timeout=5.0):
                break
            print("... still waiting for game connection ...", flush=True)
        print("game connected, entering monitor mode (Ctrl+C to exit)", flush=True)
        while True:
            time.sleep(gc.heartbeat_interval_s)
            if gc.is_game_connected:
                info = gc.game_info or {}
                last = f"{time.time() - gc.last_pong_ts:.1f}s ago" if gc.last_pong_ts else "n/a"
                print(f"[alive] mod={info.get('mod')} last_pong={last}", flush=True)
            else:
                print("[disconnected] waiting for game to reconnect...", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        gc.stop()


if __name__ == "__main__":
    _cli()
