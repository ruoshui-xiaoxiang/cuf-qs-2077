"""Self-test for supervised task execution."""

import json
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from supervisor import (  # noqa: E402
    FunctionTaskAdapter,
    JsonSubprocessTaskAdapter,
    SupervisedTaskRunner,
    TaskSpec,
    build_default_supervisor,
)
from supervisor.cli import main as cli_main  # noqa: E402


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def test_runner_retries_then_completes():
    def flaky(spec=None, attempt=1, context=None):
        if attempt == 1:
            return {"state": "failed", "errors": ["temporary"]}
        return {
            "state": "completed",
            "output": "ok",
            "evidence": [{"evidence_type": "artifact", "summary": "done", "confirmed": True}],
        }

    with tempfile.TemporaryDirectory() as tmp:
        supervisor = build_default_supervisor(Path(tmp) / "audit.jsonl")
        runner = SupervisedTaskRunner(supervisor, FunctionTaskAdapter(flaky))
        spec = TaskSpec(task_id="r1", name="retry task", max_attempts=2, required_evidence=["artifact"])
        result = runner.run(spec)
        _assert(result["attempts"] == 2, "expected two attempts")
        _assert(result["final_decision"]["action"] == "complete", "expected completion")


def test_runner_blocks_forbidden_tool():
    def forbidden(spec=None, attempt=1, context=None):
        return {
            "state": "completed",
            "output": "done",
            "used_tools": ["shell_exec"],
        }

    with tempfile.TemporaryDirectory() as tmp:
        supervisor = build_default_supervisor(Path(tmp) / "audit.jsonl")
        runner = SupervisedTaskRunner(supervisor, FunctionTaskAdapter(forbidden))
        spec = TaskSpec(
            task_id="r2",
            name="forbidden task",
            max_attempts=2,
            forbidden_tools=["shell_exec"],
        )
        result = runner.run(spec)
        _assert(result["attempts"] == 1, "expected one attempt")
        _assert(result["final_decision"]["action"] == "block", "expected block")


def test_runner_escalates_after_retry_budget():
    def always_fail(spec=None, attempt=1, context=None):
        return {"state": "failed", "errors": ["downstream failed"]}

    with tempfile.TemporaryDirectory() as tmp:
        supervisor = build_default_supervisor(Path(tmp) / "audit.jsonl")
        runner = SupervisedTaskRunner(supervisor, FunctionTaskAdapter(always_fail))
        spec = TaskSpec(task_id="r3", name="always fail", max_attempts=2)
        result = runner.run(spec)
        _assert(result["attempts"] == 2, "expected two attempts")
        _assert(
            result["final_decision"]["action"] == "manual_review",
            "expected manual review after retry budget",
        )


def test_subprocess_runner_retries_then_completes():
    worker = Path(__file__).resolve().parents[1] / "tools" / "supervisor_worker_example.py"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        supervisor = build_default_supervisor(tmp_path / "audit.jsonl")
        adapter = JsonSubprocessTaskAdapter(
            command=[
                sys.executable,
                str(worker),
                "--mode",
                "flaky",
                "--output-dir",
                str(tmp_path / "artifacts"),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        runner = SupervisedTaskRunner(supervisor, adapter)
        spec = TaskSpec(task_id="sp1", name="subprocess flaky", max_attempts=2, required_evidence=["artifact"])
        result = runner.run(spec)
        _assert(result["attempts"] == 2, "expected subprocess retry")
        _assert(result["final_decision"]["action"] == "complete", "expected subprocess complete")
        artifact = tmp_path / "artifacts" / "artifact_attempt_2.txt"
        _assert(artifact.exists(), "expected subprocess artifact")


def test_subprocess_runner_manual_review_without_evidence():
    worker = Path(__file__).resolve().parents[1] / "tools" / "supervisor_worker_example.py"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        supervisor = build_default_supervisor(tmp_path / "audit.jsonl")
        adapter = JsonSubprocessTaskAdapter(
            command=[
                sys.executable,
                str(worker),
                "--mode",
                "noevidence",
                "--output-dir",
                str(tmp_path / "artifacts"),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        runner = SupervisedTaskRunner(supervisor, adapter)
        spec = TaskSpec(task_id="sp2", name="subprocess no evidence", max_attempts=2, required_evidence=["artifact"])
        result = runner.run(spec)
        _assert(result["attempts"] == 1, "expected single subprocess attempt")
        _assert(result["final_decision"]["action"] == "manual_review", "expected manual review")


def test_cli_demo_writes_result():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "cli_audit.jsonl"
        output_path = Path(tmp) / "stdout.txt"
        stdout = sys.stdout
        try:
            with output_path.open("w", encoding="utf-8") as fh:
                sys.stdout = fh
                cli_main(["--demo", "success", "--log", str(log_path)])
        finally:
            sys.stdout = stdout
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        _assert(payload["final_decision"]["action"] == "complete", "cli expected complete")
        _assert(log_path.exists(), "expected cli audit log")


def test_cli_subprocess_demo_writes_result():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log_path = tmp_path / "cli_subprocess_audit.jsonl"
        output_path = tmp_path / "stdout.txt"
        stdout = sys.stdout
        try:
            with output_path.open("w", encoding="utf-8") as fh:
                sys.stdout = fh
                cli_main(
                    [
                        "--executor",
                        "subprocess",
                        "--demo",
                        "success",
                        "--log",
                        str(log_path),
                        "--output-dir",
                        str(tmp_path / "worker_out"),
                    ]
                )
        finally:
            sys.stdout = stdout
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        _assert(payload["final_decision"]["action"] == "complete", "cli subprocess expected complete")
        _assert((tmp_path / "worker_out" / "artifact_attempt_1.txt").exists(), "expected worker artifact")


def test_cli_repo_inventory_writes_report():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target_dir = tmp_path / "scan_target"
        nested = target_dir / "pkg"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "module.py").write_text("print('ok')\n", encoding="utf-8")
        (target_dir / "README.md").write_text("# sample\n", encoding="utf-8")

        log_path = tmp_path / "cli_inventory_audit.jsonl"
        output_path = tmp_path / "stdout.txt"
        stdout = sys.stdout
        try:
            with output_path.open("w", encoding="utf-8") as fh:
                sys.stdout = fh
                cli_main(
                    [
                        "--executor",
                        "subprocess",
                        "--demo",
                        "repo_inventory",
                        "--log",
                        str(log_path),
                        "--output-dir",
                        str(tmp_path / "inventory_out"),
                        "--target-dir",
                        str(target_dir),
                    ]
                )
        finally:
            sys.stdout = stdout
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        _assert(payload["final_decision"]["action"] == "complete", "repo inventory expected complete")
        report = tmp_path / "inventory_out" / "repo_inventory_subprocess_repo_inventory_attempt_1.json"
        _assert(report.exists(), "expected repo inventory report")
        report_payload = json.loads(report.read_text(encoding="utf-8"))
        _assert(report_payload["total_files"] == 2, "expected two inventoried files")
        _assert(report_payload["extensions"][".py"] == 1, "expected python file count")


def main():
    tests = [
        test_runner_retries_then_completes,
        test_runner_blocks_forbidden_tool,
        test_runner_escalates_after_retry_budget,
        test_subprocess_runner_retries_then_completes,
        test_subprocess_runner_manual_review_without_evidence,
        test_cli_demo_writes_result,
        test_cli_subprocess_demo_writes_result,
        test_cli_repo_inventory_writes_report,
    ]
    passed = 0
    for func in tests:
        func()
        passed += 1
    print(f"{passed}/{len(tests)} passed")


if __name__ == "__main__":
    main()
