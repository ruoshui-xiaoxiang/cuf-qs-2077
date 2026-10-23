"""Supervisor 中文监控台：本地 HTTP 服务 + JSON API。"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_UI_PATH = Path(__file__).resolve().with_name("monitor_ui.html")
DEFAULT_PORT = 8091

ACTION_TEXT = {
    "complete": "已完成",
    "retry": "重试中",
    "manual_review": "人工复核",
    "block": "已阻断",
    "allow": "继续运行",
}
OBSERVATION_STATE_TEXT = {
    "completed": "已完成",
    "failed": "失败",
    "running": "运行中",
    "pending": "待启动",
}
CONFIRMATION_TEXT = {
    "confirmed": "已确认",
    "unconfirmed": "未确认",
    "not_started": "未开始",
}
OWNER_TEXT = {
    "supervisor": "监督器",
    "human": "人工",
}
PREVIEW_SUFFIXES = {".json", ".jsonl", ".txt", ".md", ".log", ".csv"}
DECISION_PRIORITY = {
    "block": 0,
    "manual_review": 1,
    "retry": 2,
    "allow": 3,
    "complete": 4,
}


def read_jsonl(log_path):
    """读取 JSONL 审计日志，损坏行跳过。"""
    path = Path(log_path)
    if not path.exists():
        return []
    items = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def resolve_log_path(target):
    """把文件或目录解析为实际的 supervisor JSONL 日志路径。"""
    target = Path(target)
    if target.exists() and target.is_file():
        return target
    if target.exists() and target.is_dir():
        candidates = sorted(
            target.rglob("*.jsonl"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else target / "agent_supervisor_demo.jsonl"
    if target.suffix.lower() == ".jsonl":
        return target
    return target / "agent_supervisor_demo.jsonl"


def _fmt_ts(timestamp):
    if not timestamp:
        return "-"
    return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M:%S")


def _safe_get(entry, *keys, default=None):
    current = entry
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _label(mapping, value):
    if value is None:
        return "-"
    return mapping.get(value, value)


def _event_tone(action):
    if action == "complete":
        return "ok"
    if action in {"manual_review", "block"}:
        return "danger"
    if action == "retry":
        return "warn"
    return "neutral"


def summarize_entries(entries):
    """把审计条目汇总成控制台首页需要的结构。"""
    groups = defaultdict(list)
    for entry in entries:
        groups[entry.get("task_id", "unknown")].append(entry)
    for items in groups.values():
        items.sort(key=lambda item: float(item.get("timestamp", 0)))

    tasks = []
    recent = []
    stream = []
    alerts = []
    for entry in sorted(entries, key=lambda item: float(item.get("timestamp", 0)), reverse=True):
        observation = _safe_get(entry, "w0", "observation", default={}) or {}
        task_spec = _safe_get(entry, "w0", "task_spec", default={}) or {}
        decision = _safe_get(entry, "w2", "decision", default={}) or {}
        action = decision.get("action")
        item = {
            "task_id": entry.get("task_id", "unknown"),
            "task_name": task_spec.get("name") or entry.get("task_id", "unknown"),
            "attempt": observation.get("attempt") or 1,
            "action": action,
            "action_text": _label(ACTION_TEXT, action),
            "state_text": _label(OBSERVATION_STATE_TEXT, observation.get("state")),
            "confirmation_text": _label(
                CONFIRMATION_TEXT, decision.get("confirmation_status")
            ),
            "reason": decision.get("reason") or observation.get("output") or "无摘要",
            "timestamp": entry.get("timestamp"),
            "timestamp_text": _fmt_ts(entry.get("timestamp")),
            "evidence_count": len(list(observation.get("evidence", []) or [])),
            "tone": _event_tone(action),
        }
        stream.append(item)
        if action in {"retry", "manual_review", "block"}:
            alerts.append(item)

    for task_id, items in groups.items():
        latest = items[-1]
        observation = _safe_get(latest, "w0", "observation", default={}) or {}
        task_spec = _safe_get(latest, "w0", "task_spec", default={}) or {}
        decision = _safe_get(latest, "w2", "decision", default={}) or {}
        latest_errors = list(observation.get("errors", []) or [])
        latest_evidence = list(observation.get("evidence", []) or [])
        violation_count = int(_safe_get(latest, "w1", "violation_count", default=0) or 0)
        action = decision.get("action")
        tasks.append(
            {
                "task_id": task_id,
                "task_name": task_spec.get("name") or task_id,
                "attempts": len(items),
                "latest_attempt": observation.get("attempt") or len(items),
                "decision": action,
                "decision_text": _label(ACTION_TEXT, action),
                "confirmation_status": decision.get("confirmation_status"),
                "confirmation_text": _label(CONFIRMATION_TEXT, decision.get("confirmation_status")),
                "owner": decision.get("owner"),
                "owner_text": _label(OWNER_TEXT, decision.get("owner")),
                "state": observation.get("state"),
                "state_text": _label(OBSERVATION_STATE_TEXT, observation.get("state")),
                "violation_count": violation_count,
                "evidence_count": len(latest_evidence),
                "updated_at": latest.get("timestamp"),
                "updated_at_text": _fmt_ts(latest.get("timestamp")),
                "duration_s": observation.get("duration_s"),
                "output": observation.get("output") or "",
                "errors": latest_errors,
                "next_step": decision.get("next_step") or "",
                "decision_priority": DECISION_PRIORITY.get(action, 99),
            }
        )
        recent.append(
            {
                "timestamp": latest.get("timestamp"),
                "timestamp_text": _fmt_ts(latest.get("timestamp")),
                "task_id": task_id,
                "decision_text": _label(ACTION_TEXT, action),
                "message": decision.get("reason") or observation.get("output") or "无摘要",
            }
        )

    tasks.sort(
        key=lambda item: (
            int(item.get("decision_priority", 99)),
            -float(item["updated_at"] or 0),
        )
    )
    recent.sort(key=lambda item: float(item["timestamp"] or 0), reverse=True)

    counts = {
        "tasks_total": len(tasks),
        "completed": sum(1 for item in tasks if item["decision"] == "complete"),
        "retrying": sum(1 for item in tasks if item["decision"] == "retry"),
        "manual_review": sum(1 for item in tasks if item["decision"] == "manual_review"),
        "blocked": sum(1 for item in tasks if item["decision"] == "block"),
        "with_evidence": sum(1 for item in tasks if item["evidence_count"] > 0),
    }
    return {
        "counts": counts,
        "tasks": tasks,
        "recent": recent[:20],
        "stream": stream[:40],
        "alerts": alerts[:12],
    }


def build_task_detail(entries, task_id):
    """返回单任务详情页数据。"""
    timeline = [entry for entry in entries if entry.get("task_id") == task_id]
    timeline.sort(key=lambda item: float(item.get("timestamp", 0)))
    if not timeline:
        return None

    latest = timeline[-1]
    spec = _safe_get(latest, "w0", "task_spec", default={}) or {}
    attempts = []
    for entry in timeline:
        observation = _safe_get(entry, "w0", "observation", default={}) or {}
        decision = _safe_get(entry, "w2", "decision", default={}) or {}
        violations = list(_safe_get(entry, "w1", "violations", default=[]) or [])
        evidence = list(observation.get("evidence", []) or [])
        attempts.append(
            {
                "attempt": observation.get("attempt"),
                "timestamp_text": _fmt_ts(entry.get("timestamp")),
                "state_text": _label(OBSERVATION_STATE_TEXT, observation.get("state")),
                "decision_text": _label(ACTION_TEXT, decision.get("action")),
                "confirmation_text": _label(CONFIRMATION_TEXT, decision.get("confirmation_status")),
                "owner_text": _label(OWNER_TEXT, decision.get("owner")),
                "reason": decision.get("reason") or "无",
                "next_step": decision.get("next_step") or "无",
                "duration_s": observation.get("duration_s"),
                "output": observation.get("output") or "",
                "errors": list(observation.get("errors", []) or []),
                "used_tools": list(observation.get("used_tools", []) or []),
                "evidence": evidence,
                "violations": violations,
            }
        )

    return {
        "task_id": task_id,
        "task_name": spec.get("name") or task_id,
        "required_evidence": list(spec.get("required_evidence", []) or []),
        "forbidden_tools": list(spec.get("forbidden_tools", []) or []),
        "max_attempts": spec.get("max_attempts"),
        "attempts": attempts,
        "latest_attempt": attempts[-1] if attempts else None,
    }


def read_artifact_preview(path_value, max_chars=12000):
    """读取 artifact 预览内容，只支持本地文本/JSON 文件。"""
    if not path_value:
        return {"error": "missing path"}
    path = Path(path_value).resolve()
    if not path.exists() or not path.is_file():
        return {"error": f"artifact not found: {path}"}
    if path.suffix.lower() not in PREVIEW_SUFFIXES:
        return {"error": f"artifact suffix not supported: {path.suffix}"}

    raw = path.read_text(encoding="utf-8", errors="replace")
    content_type = "text"
    if path.suffix.lower() == ".json":
        try:
            raw = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
            content_type = "json"
        except json.JSONDecodeError:
            content_type = "text"
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n...（预览已截断）"
    return {
        "path": str(path),
        "content_type": content_type,
        "content": raw,
    }


class MonitorHandler(BaseHTTPRequestHandler):
    """Supervisor 中文监控台 HTTP 处理器。"""

    target = None

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        if not _UI_PATH.exists():
            return self._send_json({"error": "monitor_ui.html missing"}, 404)
        body = _UI_PATH.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self._send_html()
        if path == "/api/config":
            log_path = resolve_log_path(self.target)
            return self._send_json(
                {
                    "log_path": str(log_path),
                    "title": "Agent 任务监督控制台",
                }
            )
        if path == "/api/summary":
            log_path = resolve_log_path(self.target)
            entries = read_jsonl(log_path)
            data = summarize_entries(entries)
            data["log_path"] = str(log_path)
            return self._send_json(data)
        if path == "/api/task":
            log_path = resolve_log_path(self.target)
            entries = read_jsonl(log_path)
            query = parse_qs(parsed.query or "")
            task_id = (query.get("task_id") or [None])[0]
            if not task_id:
                return self._send_json({"error": "missing task_id"}, 400)
            detail = build_task_detail(entries, task_id)
            if detail is None:
                return self._send_json({"error": f"task not found: {task_id}"}, 404)
            return self._send_json(detail)
        if path == "/api/artifact":
            query = parse_qs(parsed.query or "")
            artifact_path = (query.get("path") or [None])[0]
            preview = read_artifact_preview(artifact_path)
            if "error" in preview:
                return self._send_json(preview, 404)
            return self._send_json(preview)
        return self._send_json({"error": f"unknown path: {path}"}, 404)


def serve(target, host="127.0.0.1", port=DEFAULT_PORT):
    """启动监督控制台服务。"""
    MonitorHandler.target = target
    log_path = resolve_log_path(target)
    print(f"[supervisor-monitor] watching {log_path}")
    print(f"[supervisor-monitor] open http://{host}:{port}/  (Ctrl+C to stop)")
    httpd = ThreadingHTTPServer((host, port), MonitorHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="Agent 任务监督中文控制台")
    parser.add_argument("target", help="JSONL 日志文件或日志目录")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    serve(args.target, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
