"""One-click launcher for the supervisor web console."""

import argparse
import json
import sys
import webbrowser
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from supervisor.cli import run_task  # noqa: E402
from supervisor.monitor import serve  # noqa: E402


DEFAULT_CONFIG = _ROOT / "config" / "supervisor_console.json"


def load_config(config_path):
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value):
    path = Path(value)
    return path if path.is_absolute() else (_ROOT / path)


def should_seed(log_path, seed_cfg):
    if not seed_cfg.get("enabled", True):
        return False
    mode = seed_cfg.get("mode", "if_missing")
    if mode == "always":
        return True
    if mode == "never":
        return False
    return (not log_path.exists()) or log_path.stat().st_size == 0


def seed_default_task(log_path, output_dir, seed_cfg):
    target_dir = resolve_path(seed_cfg.get("target_dir", "src/supervisor"))
    result = run_task(
        executor=seed_cfg.get("executor", "subprocess"),
        demo=seed_cfg.get("demo", "repo_inventory"),
        log_path=log_path,
        output_dir=output_dir,
        target_dir=target_dir,
    )
    return result


def launch(config_path, open_browser_override=None, skip_seed=False):
    config = load_config(config_path)
    monitor_cfg = dict(config.get("monitor", {}))
    runtime_cfg = dict(config.get("runtime", {}))
    seed_cfg = dict(config.get("seed_task", {}))

    host = monitor_cfg.get("host", "127.0.0.1")
    port = int(monitor_cfg.get("port", 8091))
    open_browser = monitor_cfg.get("open_browser", True)
    if open_browser_override is not None:
        open_browser = open_browser_override

    log_path = resolve_path(runtime_cfg.get("log_path", "logs/runtime_supervisor.jsonl"))
    output_dir = resolve_path(runtime_cfg.get("output_dir", "logs/runtime_worker_out"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not skip_seed and should_seed(log_path, seed_cfg):
        result = seed_default_task(log_path, output_dir, seed_cfg)
        print("[launcher] seeded default task")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print("[launcher] skipped seed task")

    url = f"http://{host}:{port}/"
    if open_browser:
        webbrowser.open(url)
    serve(log_path, host=host, port=port)


def main():
    parser = argparse.ArgumentParser(description="一键启动 Agent 任务监督控制台")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="运行配置文件路径",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动时不自动打开浏览器",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="跳过首次默认任务生成",
    )
    args = parser.parse_args()
    launch(
        config_path=args.config,
        open_browser_override=(False if args.no_browser else None),
        skip_seed=args.skip_seed,
    )


if __name__ == "__main__":
    main()
