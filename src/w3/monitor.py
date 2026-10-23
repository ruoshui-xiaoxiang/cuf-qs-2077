"""W3 实验实时监控面板（S25）——本地 HTTP 服务，零第三方依赖。

形态：python 标准库 ThreadingHTTPServer 提供静态页 + JSON API；
前端轮询 JSONL/JSON 归档实现"运行中实时刷新"。只读，不参与任何
裁决（反套壳对齐：监控层是观测窗口，核心逻辑零改动）。

数据源（--dir 批次目录 或 --log 单 JSONL）：
- rounds.jsonl：逐轮四层全量归档（实时追加，面板即读即新）
- report.json：批次末报告（completed/criterion/virtual_lifespan/system_dead）
- stats_summary.json：S22 统计汇总（存在时展示）

S29.19 动态解析：数据源按请求实时解析——目标目录可后于面板启动
出现（实机循环先后面板不再空白），批次布局自动跟踪最近更新的运行。

API：
- GET /            → monitor_ui.html（同目录读取）
- GET /api/config  → 数据源/刷新信息
- GET /api/rounds  → 每轮 {round_id, alignment_score, c_h, axioms,
                     action, downgrade_events, warn_events, w0 stats}
- GET /api/report  → report.json（若存在）
- GET /api/stats   → stats_summary.json（若存在）

用法：py src\\w3\\monitor.py --dir logs\\formal_batch --port 8090
      浏览器打开 http://127.0.0.1:8090/
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_UI_PATH = Path(__file__).resolve().parent / "monitor_ui.html"

DEFAULT_PORT = 8090


def load_rounds(log_path):
    """解析 JSONL 归档为轮次列表（损坏行跳过，S6/D3 容错语义一致）。"""
    rounds = []
    p = Path(log_path)
    if not p.exists():
        return rounds
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rounds.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rounds


def summarize_rounds(rounds):
    """把归档轮压缩为面板需要的轻量视图。

    含公理完整定义（hypothesis/anchor/边界列表）与每轮假设生成流
    （hypotheses），供面板实时展示"生成的公理是什么"。
    """
    out = []
    for entry in rounds:
        w0 = entry.get("w0", {}) or {}
        w2 = entry.get("w2", {}) or {}
        w3 = entry.get("w3", {}) or {}
        c_h = entry.get("c_h", {}) or {}
        axioms = {}
        for a in w2.get("axioms", []) or []:
            if isinstance(a, dict):
                axioms[a.get("id")] = {
                    "status": a.get("status"),
                    "boundary_count": len(a.get("boundary_conditions") or []),
                    "hypothesis": a.get("hypothesis"),
                    "anchor": a.get("anchor"),
                    "boundary_conditions": a.get("boundary_conditions") or [],
                }
        hypotheses = []
        for h in w3.get("hypotheses", []) or []:
            if isinstance(h, dict):
                hypotheses.append({
                    "hypothesis_id": h.get("hypothesis_id"),
                    "axiom_id": h.get("axiom_id"),
                    "type": h.get("type"),
                    "description": h.get("description"),
                    "is_counterexample": h.get("is_counterexample", False),
                })
        out.append({
            "round_id": entry.get("round_id"),
            "alignment_score": w3.get("alignment_score"),
            "model_state_unavailable": w3.get("model_state_unavailable", False),
            "action": w3.get("action"),
            "action_status": w3.get("action_status"),
            "action_channel": w3.get("action_channel"),
            "action_control_tier": w3.get("action_control_tier"),
            "action_receipt_reason": w3.get("action_receipt_reason"),
            "action_outcome_semantics": w3.get("action_outcome_semantics"),
            "action_confirmation_status": w3.get("action_confirmation_status"),
            "c_h": {
                "struct": c_h.get("struct"),
                "adapt": c_h.get("adapt"),
                "extend": c_h.get("extend"),
                "within_budget": c_h.get("within_budget"),
            },
            "axioms": axioms,
            "hypotheses": hypotheses,
            "downgrade_events": w3.get("downgrade_events", []) or [],
            "warn_events": w3.get("warn_events", []) or [],
            "entities_count": len(w0.get("entities", []) or []),
            "events_count": len(w0.get("events", []) or []),
            "stats": w0.get("stats", {}) or {},
            "empty_scan": (len(w0.get("entities", []) or []) == 0),
        })
    return out


def read_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


class MonitorHandler(BaseHTTPRequestHandler):
    """只读 HTTP 处理器：静态页 + JSON API。"""

    target = None  # 由 serve 注入：--dir/--log 目标（动态解析，S29.19）

    # ── 工具 ──────────────────────────────────────────────

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        if not _UI_PATH.exists():
            self._send_json({"error": "monitor_ui.html missing"}, 404)
            return
        body = _UI_PATH.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志（面板轮询高频，避免刷屏）

    # ── 路由 ──────────────────────────────────────────────

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send_html()
        if path == "/api/config":
            log_path, report_path, stats_path = resolve_sources(self.target)
            return self._send_json({
                "log_path": str(log_path),
                "report_path": str(report_path),
                "stats_path": str(stats_path),
            })
        if path == "/api/rounds":
            log_path, _, _ = resolve_sources(self.target)
            rounds = load_rounds(log_path)
            return self._send_json({
                "count": len(rounds),
                "rounds": summarize_rounds(rounds),
            })
        if path == "/api/report":
            _, report_path, _ = resolve_sources(self.target)
            rep = read_json(report_path)
            if rep is None:
                return self._send_json({"error": "report.json not ready"}, 404)
            return self._send_json(rep)
        if path == "/api/stats":
            _, _, stats_path = resolve_sources(self.target)
            stats = read_json(stats_path)
            if stats is None:
                return self._send_json({"error": "stats_summary.json missing"},
                                       404)
            return self._send_json(stats)
        self._send_json({"error": f"unknown path: {path}"}, 404)


def resolve_sources(target):
    """把 --dir 或 --log 解析为 (log_path, report_path, stats_path)。

    S29.19 动态解析：每次请求重新解析（此前启动时解析一次并固定——
    面板先于数据目录启动时路径被错误钉死，实机/批次运行中面板空白）。
    语义：
    - 目标是文件 → 按 JSONL 文件模式；
    - 目标是目录且顶层有 rounds.jsonl（实机平铺布局）→ 直接用；
    - 目录含多个 rounds.jsonl（批次布局）→ 取**最近修改**的一个
      （批次运行中自动跟踪当前正在写入的运行）；
    - 目标尚不存在 → 按后缀推断（.jsonl 为文件模式，否则目录模式
      等待 rounds.jsonl 出现），文件出现前 /api/rounds 返回空。
    """
    target = Path(target)
    if target.exists() and target.is_file():
        return (target, target.parent / "report.json",
                target.parent / "stats" / "stats_summary.json")
    if target.is_dir():
        flat = target / "rounds.jsonl"
        if flat.exists():
            return (flat, target / "report.json",
                    target / "stats" / "stats_summary.json")
        subs = sorted(target.rglob("rounds.jsonl"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        if subs:
            sub = subs[0]
            stats_top = target / "stats" / "stats_summary.json"
            stats = stats_top if stats_top.exists() else (
                sub.parent.parent / "stats" / "stats_summary.json")
            return sub, sub.parent / "report.json", stats
        return (flat, target / "report.json",
                target / "stats" / "stats_summary.json")
    # 尚不存在：按后缀推断等待
    if target.suffix == ".jsonl":
        return (target, target.parent / "report.json",
                target.parent / "stats" / "stats_summary.json")
    return (target / "rounds.jsonl", target / "report.json",
            target / "stats" / "stats_summary.json")


def serve(target, host="127.0.0.1", port=DEFAULT_PORT):
    """启动监控服务（阻塞）。Ctrl+C 退出。"""
    MonitorHandler.target = target
    log_path, _, _ = resolve_sources(target)
    print(f"[monitor] watching {log_path}（动态解析：目录后现/新运行自动跟踪）")
    print(f"[monitor] open http://{host}:{port}/  (Ctrl+C to stop)")
    httpd = ThreadingHTTPServer((host, port), MonitorHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="CUF-QS 实验实时监控面板（S25，零依赖 HTTP 服务）")
    parser.add_argument("target", help="批次目录（logs/formal_batch）或 JSONL 文件")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    serve(args.target, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
