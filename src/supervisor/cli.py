"""Minimal CLI demo for the task supervisor."""

import argparse
import json
import sys
from pathlib import Path

from supervisor.adapters import FunctionTaskAdapter, JsonSubprocessTaskAdapter
from supervisor.models import TaskSpec
from supervisor.runner import SupervisedTaskRunner
from supervisor.supervisor import build_default_supervisor


def _demo_success(spec=None, attempt=1, context=None):
    return {
        "state": "completed",
        "output": "report generated",
        "used_tools": ["read_repo"],
        "evidence": [
            {
                "evidence_type": "artifact",
                "summary": "saved output artifact",
                "confirmed": True,
            }
        ],
    }


def _demo_flaky(spec=None, attempt=1, context=None):
    if attempt == 1:
        return {
            "state": "failed",
            "errors": ["temporary upstream error"],
        }
    return {
        "state": "completed",
        "output": "retry succeeded",
        "used_tools": ["retry_task"],
        "evidence": [
            {
                "evidence_type": "artifact",
                "summary": "retry output",
                "confirmed": True,
            }
        ],
    }


def _demo_forbidden(spec=None, attempt=1, context=None):
    return {
        "state": "completed",
        "output": "unsafe operation done",
        "used_tools": ["shell_exec"],
    }


FUNCTION_DEMOS = {
    "success": _demo_success,
    "flaky": _demo_flaky,
    "forbidden": _demo_forbidden,
}

SUBPROCESS_DEMOS = {"success", "flaky", "noevidence", "repo_inventory"}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _build_subprocess_command(demo: str, output_dir: Path, target_dir: Path | None):
    root = _project_root()
    if demo == "repo_inventory":
        worker = root / "tools" / "supervisor_repo_inventory_worker.py"
        inventory_target = target_dir or (root / "src")
        return [
            sys.executable,
            str(worker),
            "--target-dir",
            str(inventory_target),
            "--output-dir",
            str(output_dir),
        ]

    worker = root / "tools" / "supervisor_worker_example.py"
    return [
        sys.executable,
        str(worker),
        "--mode",
        demo,
        "--output-dir",
        str(output_dir),
    ]


def _build_spec(executor: str, demo: str) -> TaskSpec:
    spec = TaskSpec(
        task_id=f"{executor}_{demo}",
        name=f"{executor} task {demo}",
        max_attempts=2,
        required_evidence=["artifact"],
        forbidden_tools=["shell_exec"],
    )
    if executor == "function" and demo == "forbidden":
        spec.required_evidence = []
    return spec


def _build_runner(
    executor: str,
    demo: str,
    output_dir: Path,
    log_path: Path,
    target_dir: Path | None,
) -> SupervisedTaskRunner:
    supervisor = build_default_supervisor(log_path)
    if executor == "function":
        return SupervisedTaskRunner(supervisor, FunctionTaskAdapter(FUNCTION_DEMOS[demo]))

    command = _build_subprocess_command(demo, output_dir, target_dir)
    adapter = JsonSubprocessTaskAdapter(command=command, cwd=str(_project_root()))
    return SupervisedTaskRunner(supervisor, adapter)


def run_task(
    executor="function",
    demo="success",
    log_path=None,
    output_dir=None,
    target_dir=None,
):
    """Run one supervised task and return the structured result."""
    if executor == "function" and demo not in FUNCTION_DEMOS:
        raise ValueError(f"demo '{demo}' is not available for executor 'function'")
    if executor == "subprocess" and demo not in SUBPROCESS_DEMOS:
        raise ValueError(f"demo '{demo}' is not available for executor 'subprocess'")

    log_path = Path(log_path) if log_path is not None else (Path("logs") / "agent_supervisor_demo.jsonl")
    default_output_dir = log_path.parent / f"worker_{demo}"
    output_dir = Path(output_dir) if output_dir is not None else default_output_dir
    target_dir = Path(target_dir) if target_dir is not None else None

    spec = _build_spec(executor, demo)
    runner = _build_runner(executor, demo, output_dir, log_path, target_dir)
    return runner.run(spec)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a supervised task demo.")
    parser.add_argument(
        "--executor",
        choices=["function", "subprocess"],
        default="function",
        help="execution backend",
    )
    parser.add_argument(
        "--demo",
        choices=sorted(set(FUNCTION_DEMOS) | set(SUBPROCESS_DEMOS)),
        default="success",
        help="demo scenario name",
    )
    parser.add_argument(
        "--log",
        default=str(Path("logs") / "agent_supervisor_demo.jsonl"),
        help="audit log path",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="worker artifact output directory for subprocess demos",
    )
    parser.add_argument(
        "--target-dir",
        default=None,
        help="target directory for repo_inventory subprocess scenario",
    )
    args = parser.parse_args(argv)

    try:
        result = run_task(
            executor=args.executor,
            demo=args.demo,
            log_path=args.log,
            output_dir=args.output_dir,
            target_dir=args.target_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
