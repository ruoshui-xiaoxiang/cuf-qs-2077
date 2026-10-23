"""Self-test for one-click supervisor launcher helpers."""

import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_TOOLS = _ROOT / "tools"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from start_supervisor_console import (  # noqa: E402
    load_config,
    resolve_path,
    seed_default_task,
    should_seed,
)


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def test_should_seed_logic():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        log_path = tmp_path / "runtime.jsonl"
        _assert(should_seed(log_path, {"enabled": True, "mode": "if_missing"}), "missing log should seed")
        log_path.write_text("x\n", encoding="utf-8")
        _assert(not should_seed(log_path, {"enabled": True, "mode": "if_missing"}), "existing log should not seed")
        _assert(should_seed(log_path, {"enabled": True, "mode": "always"}), "always mode should seed")
        _assert(not should_seed(log_path, {"enabled": False, "mode": "always"}), "disabled should skip seed")


def test_seed_default_task_creates_log():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target_dir = tmp_path / "sample"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "a.py").write_text("print('ok')\n", encoding="utf-8")
        log_path = tmp_path / "runtime.jsonl"
        output_dir = tmp_path / "worker_out"
        result = seed_default_task(
            log_path,
            output_dir,
            {
                "executor": "subprocess",
                "demo": "repo_inventory",
                "target_dir": str(target_dir),
            },
        )
        _assert(result["final_decision"]["action"] == "complete", "seed task should complete")
        _assert(log_path.exists(), "seed task should create log")


def test_load_config_and_resolve_path():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "console.json"
        config_path.write_text(
            json.dumps({"runtime": {"log_path": "logs/a.jsonl"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        config = load_config(config_path)
        _assert(config["runtime"]["log_path"] == "logs/a.jsonl", "expected config load")
        resolved = resolve_path("logs/a.jsonl")
        _assert(resolved.is_absolute(), "resolved path should be absolute")


def main():
    tests = [
        test_should_seed_logic,
        test_seed_default_task_creates_log,
        test_load_config_and_resolve_path,
    ]
    passed = 0
    for func in tests:
        func()
        passed += 1
    print(f"{passed}/{len(tests)} passed")


if __name__ == "__main__":
    main()
