"""CufNative（RED4ext，TCP 20771）单行 JSON 协议客户端。

协议：发送单行命令文本（如 "get_state" / "kill_nearby 100"），
读回单行 JSON 响应（与 _probe_save 等探针脚本同口径）。

契约（工程铁律 #2）：通道不可用一律抛 NativeUnavailableError（fail-loud，
调用方按「系统前置条件不可用」口径标记跳过，绝不回退伪造数据）。
"""

import json
import logging
import socket
from pathlib import Path

log = logging.getLogger("native_client")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "experiment_config.json"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 20771
DEFAULT_TIMEOUT_S = 10.0


class NativeUnavailableError(RuntimeError):
    """CufNative 通道不可用（未启用 / 连接失败 / 响应异常）。"""


class NativeClient:
    """CufNative 短连接客户端：每次调用独立连接，无跨轮状态。"""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 timeout_s=DEFAULT_TIMEOUT_S):
        self.host = str(host)
        self.port = int(port)
        self.timeout_s = float(timeout_s)

    @classmethod
    def from_config(cls, path=None):
        """从配置单一事实源构造；未启用或无配置段返回 None（铁律 #5）。"""
        cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")).get("native", {})
        except (OSError, ValueError):
            return None
        if not cfg.get("enabled"):
            return None
        return cls(host=cfg.get("host", DEFAULT_HOST),
                   port=cfg.get("port", DEFAULT_PORT),
                   timeout_s=cfg.get("timeout_s", DEFAULT_TIMEOUT_S))

    def call(self, cmd, args=None):
        """发送单行命令并返回解析后的 JSON dict。"""
        args = list(args or [])
        line = " ".join([str(cmd)] + [str(a) for a in args])
        try:
            s = socket.create_connection((self.host, self.port),
                                         timeout=self.timeout_s)
        except OSError as e:
            raise NativeUnavailableError(
                f"connect {self.host}:{self.port} failed: {e}") from e
        try:
            s.settimeout(self.timeout_s)
            s.sendall((line + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(262144)
                if not chunk:
                    break
                buf += chunk
        except OSError as e:
            raise NativeUnavailableError(f"i/o error on {line!r}: {e}") from e
        finally:
            s.close()
        first = buf.split(b"\n", 1)[0]
        if not first:
            raise NativeUnavailableError(f"empty response for {line!r}")
        try:
            resp = json.loads(first.decode("utf-8", errors="replace"))
        except ValueError as e:
            raise NativeUnavailableError(
                f"malformed JSON response for {line!r}: {e}") from e
        if not isinstance(resp, dict):
            raise NativeUnavailableError(
                f"non-object response for {line!r}: {type(resp).__name__}")
        return resp

    def get_state(self):
        return self.call("get_state")

    def kill_nearby(self, radius):
        return self.call("kill_nearby", [radius])

    def list_npcs(self, radius):
        return self.call("list_npcs", [radius])
