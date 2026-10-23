"""Real local audit worker for the task supervisor.

This worker scans a target directory, writes a structured inventory report,
and returns the report path as durable evidence for supervisor review.
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path


DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    ".qoder",
    "__pycache__",
    "build",
    "dist",
    "logs",
    "node_modules",
    "venv",
}


def _should_skip_dir(name: str, extra_excludes):
    return name in DEFAULT_EXCLUDES or name in extra_excludes


def _inventory(target_dir: Path, extra_excludes):
    extension_counts = Counter()
    top_level_counts = Counter()
    total_files = 0
    total_dirs = 0

    for root, dirs, files in os.walk(target_dir):
        root_path = Path(root)
        dirs[:] = [item for item in dirs if not _should_skip_dir(item, extra_excludes)]
        total_dirs += len(dirs)

        try:
            relative_root = root_path.relative_to(target_dir)
        except ValueError:
            relative_root = Path(".")
        top_level = relative_root.parts[0] if relative_root.parts else "."

        for filename in files:
            file_path = root_path / filename
            suffix = file_path.suffix.lower() or "<no_ext>"
            extension_counts[suffix] += 1
            top_level_counts[top_level] += 1
            total_files += 1

    return {
        "target_dir": str(target_dir),
        "total_files": total_files,
        "total_dirs": total_dirs,
        "extensions": dict(extension_counts.most_common()),
        "top_level_entries": dict(top_level_counts.most_common()),
    }


def _write_report(output_dir: Path, task_id: str, attempt: int, report: dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"repo_inventory_{task_id}_attempt_{attempt}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--exclude-dir", action="append", default=[])
    args = parser.parse_args()

    attempt = int(os.environ.get("SUPERVISOR_TASK_ATTEMPT", "1"))
    task_id = os.environ.get("SUPERVISOR_TASK_ID", "unknown")
    target_dir = Path(args.target_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    try:
        if not target_dir.exists() or not target_dir.is_dir():
            raise FileNotFoundError(f"target dir not found: {target_dir}")

        report = _inventory(target_dir, set(args.exclude_dir))
        if report["total_files"] <= 0:
            raise RuntimeError("inventory result is empty")

        report["task_id"] = task_id
        report["attempt"] = attempt
        report_path = _write_report(output_dir, task_id, attempt, report)
        payload = {
            "state": "completed",
            "output": (
                f"inventory completed: {report['total_files']} files under {report['target_dir']}"
            ),
            "used_tools": ["filesystem_scan", "report_write"],
            "evidence": [
                {
                    "evidence_type": "artifact",
                    "summary": f"inventory report saved to {report_path}",
                    "confirmed": True,
                    "payload": {
                        "path": str(report_path),
                        "target_dir": report["target_dir"],
                        "total_files": report["total_files"],
                        "total_dirs": report["total_dirs"],
                    },
                }
            ],
            "metadata": {
                "worker": "repo_inventory",
                "task_id": task_id,
                "attempt": attempt,
            },
        }
    except Exception as exc:
        payload = {
            "state": "failed",
            "errors": [f"{type(exc).__name__}: {exc}"],
            "metadata": {
                "worker": "repo_inventory",
                "task_id": task_id,
                "attempt": attempt,
            },
        }

    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
