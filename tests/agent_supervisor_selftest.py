"""Self-test for the agent task supervisor MVP."""

import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from supervisor import (  # noqa: E402
    JsonlAuditStore,
    TaskEvidence,
    TaskObservation,
    TaskSpec,
    TaskSupervisor,
)


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def test_completed_with_confirmed_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonlAuditStore(Path(tmp) / "audit.jsonl")
        supervisor = TaskSupervisor(store=store)
        spec = TaskSpec(
            task_id="t1",
            name="collect report",
            required_evidence=["artifact"],
        )
        obs = TaskObservation(
            task_id="t1",
            state="completed",
            output="report ready",
            evidence=[TaskEvidence("artifact", "uploaded report", confirmed=True)],
        )
        entry = supervisor.evaluate(spec, obs)
        _assert(entry["w2"]["decision"]["action"] == "complete", "expected complete")
        _assert(
            entry["w2"]["decision"]["confirmation_status"] == "confirmed",
            "expected confirmed",
        )
        _assert(len(store.load_all()) == 1, "expected one audit record")


def test_completed_without_required_evidence():
    supervisor = TaskSupervisor()
    spec = TaskSpec(
        task_id="t2",
        name="ship summary",
        required_evidence=["ticket"],
    )
    obs = TaskObservation(
        task_id="t2",
        state="completed",
        output="done",
        evidence=[],
    )
    entry = supervisor.evaluate(spec, obs)
    _assert(
        entry["w2"]["decision"]["action"] == "manual_review",
        "expected manual review",
    )
    _assert(entry["w1"]["violation_count"] == 1, "expected one violation")


def test_failed_inside_retry_budget():
    supervisor = TaskSupervisor()
    spec = TaskSpec(task_id="t3", name="call tool", max_attempts=3)
    obs = TaskObservation(
        task_id="t3",
        state="failed",
        attempt=1,
        errors=["temporary upstream error"],
    )
    entry = supervisor.evaluate(spec, obs)
    _assert(entry["w2"]["decision"]["action"] == "retry", "expected retry")
    _assert(
        entry["w2"]["decision"]["status"] == "retry_scheduled",
        "expected retry_scheduled",
    )


def test_forbidden_tool_blocks_task():
    supervisor = TaskSupervisor()
    spec = TaskSpec(
        task_id="t4",
        name="unsafe tool use",
        forbidden_tools=["shell_exec"],
    )
    obs = TaskObservation(
        task_id="t4",
        state="completed",
        used_tools=["shell_exec"],
        output="done",
    )
    entry = supervisor.evaluate(spec, obs)
    _assert(entry["w2"]["decision"]["action"] == "block", "expected block")
    _assert(entry["w2"]["decision"]["status"] == "blocked", "expected blocked")


def test_timeout_triggers_manual_review():
    supervisor = TaskSupervisor()
    spec = TaskSpec(task_id="t5", name="slow task", timeout_s=3)
    obs = TaskObservation(
        task_id="t5",
        state="completed",
        duration_s=9,
        output="partial result",
    )
    entry = supervisor.evaluate(spec, obs)
    _assert(
        entry["w2"]["decision"]["action"] == "manual_review",
        "expected manual review for timeout",
    )


def main():
    tests = [
        test_completed_with_confirmed_evidence,
        test_completed_without_required_evidence,
        test_failed_inside_retry_budget,
        test_forbidden_tool_blocks_task,
        test_timeout_triggers_manual_review,
    ]
    passed = 0
    for func in tests:
        func()
        passed += 1
    print(f"{passed}/{len(tests)} passed")


if __name__ == "__main__":
    main()
