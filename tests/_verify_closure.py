"""验证修复后完整闭环：struct 超预算触发 Γ脆 判据③（刹车生效）。

场景：ActionAwareMock 产生 entity_appeared 事件 → 触发候选激活 → struct
膨胀 → struct 超预算（struct_budget 临时设 5）→ over_budget 解体。

用法：py -3.12 tests/_verify_closure.py（无需游戏/GPU）
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from planner_batch import ActionAwareMock
from w3.auto_experiment import run_auto_experiment

BASE_CONFIG = Path(__file__).resolve().parents[1] / "config" / "experiment_config.json"
PORT = 21500
OUT = Path(__file__).resolve().parents[1] / "logs" / "_verify_closure"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # 临时 config：struct_budget 设 5（小预算，候选激活后 struct 必超），
    # activation 启用（候选激活）。
    cfg = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    cfg["c_h"]["struct_budget"] = 5
    cfg["activation"]["enabled"] = True
    fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix="verify_closure_")
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    mock = ActionAwareMock(port=PORT, seed=7)
    t = threading.Thread(target=mock.connect_with_retry,
                         kwargs={"interval_s": 0.1}, daemon=True)
    t.start()
    try:
        report = run_auto_experiment(
            rounds=10, config_path=cfg_path, port=PORT,
            log_path=str(OUT / "rounds.jsonl"),
            report_path=str(OUT / "report.json"),
            wait_timeout_s=30.0, seed=7)
    finally:
        mock.close()

    print("=== 修复后完整闭环验证 ===")
    print("completed:", report.get("completed"))
    print("criterion:", report.get("criterion"),
          " (期望 over_budget = struct 超预算触发判据③)")
    print("virtual_lifespan:", report.get("virtual_lifespan"))
    act = report.get("activation") or {}
    print("激活候选数:", len(act.get("activated", [])))

    lines = (OUT / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    ch = last["c_h"]
    print("最终 struct:", ch["struct"], "/ budget:", ch["budgets"]["struct_budget"],
          " struct_overload:", ch["overloads"]["struct_overload"])
    print("within_budget:", ch["within_budget"],
          " (期望 False = 刹车生效)")


if __name__ == "__main__":
    main()
