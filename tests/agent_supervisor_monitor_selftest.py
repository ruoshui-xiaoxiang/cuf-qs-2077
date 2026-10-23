"""Self-test for the supervisor monitor data aggregation."""

import json
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from supervisor.monitor import (  # noqa: E402
    build_task_detail,
    read_jsonl,
    read_artifact_preview,
    resolve_log_path,
    summarize_entries,
)


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _entry(task_id, attempt, timestamp, state, action, evidence_count=0):
    evidence = []
    for index in range(evidence_count):
        evidence.append(
            {
                "evidence_type": "artifact",
                "summary": f"artifact {index + 1}",
                "confirmed": True,
                "payload": {"path": f"E:\\tmp\\artifact_{index + 1}.json"},
            }
        )
    return {
        "task_id": task_id,
        "timestamp": timestamp,
        "w0": {
            "task_spec": {
                "task_id": task_id,
                "name": f"{task_id} 名称",
                "max_attempts": 2,
                "required_evidence": ["artifact"],
                "forbidden_tools": ["shell_exec"],
            },
            "observation": {
                "task_id": task_id,
                "attempt": attempt,
                "state": state,
                "output": f"{task_id} 输出",
                "errors": [] if state != "failed" else ["temporary error"],
                "used_tools": ["subprocess"],
                "duration_s": 0.12,
                "evidence": evidence,
            },
        },
        "w1": {
            "violation_count": 1 if action == "manual_review" else 0,
            "violations": (
                [{"rule_id": "missing_evidence", "message": "缺少证据"}]
                if action == "manual_review"
                else []
            ),
        },
        "w2": {
            "decision": {
                "action": action,
                "status": action,
                "reason": f"{task_id} 裁决原因",
                "confirmation_status": "confirmed" if action == "complete" else "unconfirmed",
                "next_step": "进入下一步" if action == "complete" else "等待处理",
                "owner": "supervisor" if action != "manual_review" else "human",
            }
        },
        "w3": {
            "control_action": action,
            "status": action,
            "confirmation_status": "confirmed" if action == "complete" else "unconfirmed",
            "next_step": "进入下一步" if action == "complete" else "等待处理",
            "owner": "supervisor" if action != "manual_review" else "human",
        },
    }


def test_summary_and_detail():
    entries = [
        _entry("task_a", 1, 100.0, "failed", "retry", evidence_count=0),
        _entry("task_a", 2, 101.0, "completed", "complete", evidence_count=1),
        _entry("task_b", 1, 102.0, "completed", "manual_review", evidence_count=0),
        _entry("task_c", 1, 90.0, "completed", "block", evidence_count=0),
    ]
    summary = summarize_entries(entries)
    _assert(summary["counts"]["tasks_total"] == 3, "expected three tasks")
    _assert(summary["counts"]["completed"] == 1, "expected one completed task")
    _assert(summary["counts"]["manual_review"] == 1, "expected one review task")
    _assert(summary["counts"]["blocked"] == 1, "expected one blocked task")
    _assert(summary["tasks"][0]["task_id"] == "task_c", "blocked task should sort first")
    _assert(summary["stream"][0]["task_id"] == "task_b", "expected newest event in stream")
    _assert(summary["stream"][0]["tone"] == "danger", "review event should be marked as danger")
    _assert(summary["alerts"][0]["task_id"] == "task_b", "expected newest alert first")

    detail = build_task_detail(entries, "task_a")
    _assert(detail is not None, "expected task detail")
    _assert(len(detail["attempts"]) == 2, "expected two attempts")
    _assert(detail["latest_attempt"]["decision_text"] == "已完成", "expected chinese decision label")


def test_log_resolution_and_loading():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        logs = tmp_path / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        old_log = logs / "old.jsonl"
        new_log = logs / "new.jsonl"
        old_log.write_text(json.dumps(_entry("task_old", 1, 1.0, "completed", "complete")) + "\n", encoding="utf-8")
        new_log.write_text(json.dumps(_entry("task_new", 1, 2.0, "completed", "complete")) + "\n", encoding="utf-8")
        resolved = resolve_log_path(logs)
        _assert(resolved == new_log, "expected newest jsonl to be selected")
        loaded = read_jsonl(new_log)
        _assert(len(loaded) == 1, "expected one entry from log file")


def test_artifact_preview():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        artifact = tmp_path / "artifact.json"
        artifact.write_text(json.dumps({"ok": True, "count": 2}, ensure_ascii=False), encoding="utf-8")
        preview = read_artifact_preview(artifact)
        _assert(preview["content_type"] == "json", "expected json preview")
        _assert('"count": 2' in preview["content"], "expected preview content")


def main():
    tests = [
        test_summary_and_detail,
        test_log_resolution_and_loading,
        test_artifact_preview,
    ]
    passed = 0
    for func in tests:
        func()
        passed += 1
    print(f"{passed}/{len(tests)} passed")


if __name__ == "__main__":
    main()
