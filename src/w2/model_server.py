"""S29.11 模型驻留服务（方案 A）——常驻进程持有 Qwen2.5-7B，本地端口
提供 generate_chat_text / encode（JSON 行协议）。测试脚本经
RemoteModelAdapter 远程调用，避免每次测试重复加载 7B（13-18 分钟）。

反套壳：本服务只做模型推理（文本生成 / 隐状态提取），不参与任何
裁决/规则判定。

并发契约：每连接一线程，但模型为进程内单例且隐状态提取经实例级
hook 缓存（_captured）——所有推理类 op 在全局推理锁内串行执行，
杜绝并发客户端互相覆盖隐状态（静默数据损坏）。

协议：TCP 127.0.0.1:20772，逐行 JSON。
  请求: {"op":"ping"}
        {"op":"generate_chat_text","messages":[...],"max_new_tokens":80,
         "do_sample":false,"temperature":0.7,"forced_prefix":null}
        {"op":"encode","text":"..."}
        {"op":"set_steering","layer":16,"alpha":2.0,"vector":[...]}
        {"op":"clear_steering"}
        {"op":"steering_status"}
        {"op":"extract_direction","layer":16,
         "pos_texts":[...],"neg_texts":[...]}
  响应: {"ok":true,"pong":true}
        {"ok":true,"text":"..."}
        {"ok":true,"vector":[...]}
        {"ok":true,"status":{"layer":16,"alpha":2.0,"dim":3584}}
        {"ok":true,"vector":[...],"meta":{...}}
        {"ok":false,"error":"..."}
        {"ok":false,"error":"...","error_type":"context_overflow"}
        （context_overflow：前置条件②破坏 → 客户端须还原为
         ContextOverflowError「系统不可信」，不得吞成普通错误）

D1 纪律：steering 只作用于生成调用（generate_chat_text），encode/
extract_direction 等度量通道不受影响——干预只加生成侧，禁止掰仪表。

用法：py -3.12 src\\w2\\model_server.py（常驻，Ctrl+C 退出）
"""
import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.utils import ContextOverflowError  # noqa: E402
from w2.model_adapter import ModelAdapter  # noqa: E402

HOST = "127.0.0.1"
PORT = 20772

_adapter = None  # 进程内单例模型
# 推理锁：模型单例 + _captured 实例缓存非线程安全，generate/encode
# 必须串行（并发客户端同时在场时保证各自拿到自己的结果）。
_INFER_LOCK = threading.Lock()


def _respond(conn, payload):
    conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n")
                 .encode("utf-8"))


def handle(conn):
    try:
        f = conn.makefile("r", encoding="utf-8")
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except Exception as e:
                _respond(conn, {"ok": False, "error": f"bad json: {e}"})
                continue
            try:
                op = req.get("op")
                if op == "ping":
                    resp = {"ok": True, "pong": True}
                elif op == "generate_chat_text":
                    with _INFER_LOCK:
                        text = _adapter.generate_chat_text(
                            req.get("messages") or [],
                            max_new_tokens=req.get("max_new_tokens", 80),
                            do_sample=bool(req.get("do_sample", False)),
                            temperature=float(req.get("temperature", 0.7)),
                            forced_prefix=req.get("forced_prefix"),
                            enable_thinking=bool(req.get("enable_thinking", False)))
                    resp = {"ok": True, "text": text}
                elif op == "encode":
                    with _INFER_LOCK:
                        vec = _adapter.encode(req.get("text", ""))
                    resp = {"ok": True, "vector": vec}
                elif op == "set_steering":
                    with _INFER_LOCK:
                        status = _adapter.set_steering(
                            req.get("layer", -1),
                            float(req.get("alpha", 0.0)),
                            req.get("vector") or [])
                    resp = {"ok": True, "status": status}
                elif op == "clear_steering":
                    with _INFER_LOCK:
                        _adapter.clear_steering()
                    resp = {"ok": True}
                elif op == "steering_status":
                    resp = {"ok": True,
                            "status": _adapter.steering_status()}
                elif op == "extract_direction":
                    with _INFER_LOCK:
                        vec, meta = _adapter.extract_direction(
                            req.get("layer", -1),
                            req.get("pos_texts") or [],
                            req.get("neg_texts") or [])
                    resp = {"ok": True, "vector": vec, "meta": meta}
                else:
                    resp = {"ok": False, "error": f"unknown op: {op}"}
            except Exception as e:
                resp = {"ok": False, "error": str(e)}
                if isinstance(e, ContextOverflowError):
                    resp["error_type"] = "context_overflow"
            _respond(conn, resp)
        conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def main():
    global _adapter
    print("[model_server] loading Qwen2.5-7B-Instruct (NF4)...", flush=True)
    _adapter = ModelAdapter.from_config()
    _adapter.load()
    print(f"[model_server] loaded hidden_size={len(_adapter.encode('x'))}",
          flush=True)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(4)
    print(f"[model_server] ready on {HOST}:{PORT}", flush=True)
    try:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=handle, args=(conn,),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("[model_server] shutdown", flush=True)
    finally:
        srv.close()


if __name__ == "__main__":
    main()
