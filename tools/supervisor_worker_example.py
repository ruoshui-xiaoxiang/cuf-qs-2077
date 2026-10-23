"""Example external worker for JsonSubprocessTaskAdapter.

This script simulates a real external task process:
- it runs in a separate process
- returns structured JSON to stdout
- can succeed, fail once, or complete without evidence
"""

import argparse
import json
import os
from pathlib import Path


def _artifact_payload(output_dir, attempt):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / f"artifact_attempt_{attempt}.txt"
    artifact.write_text(f"artifact created at attempt {attempt}\n", encoding="utf-8")
    return str(artifact)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["success", "flaky", "noevidence"], default="success")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    attempt = int(os.environ.get("SUPERVISOR_TASK_ATTEMPT", "1"))
    task_id = os.environ.get("SUPERVISOR_TASK_ID", "unknown")

    if args.mode == "flaky" and attempt == 1:
        print(json.dumps({
            "state": "failed",
            "errors": ["temporary upstream failure"],
            "metadata": {"task_id": task_id, "mode": args.mode, "attempt": attempt},
        }, ensure_ascii=False))
        return

    if args.mode == "noevidence":
        print(json.dumps({
            "state": "completed",
            "output": "task completed without durable proof",
            "used_tools": ["worker_process"],
            "metadata": {"task_id": task_id, "mode": args.mode, "attempt": attempt},
        }, ensure_ascii=False))
        return

    artifact_path = _artifact_payload(args.output_dir, attempt)
    print(json.dumps({
        "state": "completed",
        "output": f"worker finished for {task_id}",
        "used_tools": ["worker_process"],
        "evidence": [{
            "evidence_type": "artifact",
            "summary": f"artifact saved to {artifact_path}",
            "confirmed": True,
            "payload": {"path": artifact_path},
        }],
        "metadata": {"task_id": task_id, "mode": args.mode, "attempt": attempt},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
