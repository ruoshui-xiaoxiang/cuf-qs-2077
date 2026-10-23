"""实机 7B 双路闭环（S26-7B）——真实游戏 + 7B 隐状态/嵌入获取真实判别分。

运行环境：必须 `py -3.12`（torch 2.9.1+cu128 GPU），本地 Qwen2.5-7B。
游戏须运行并进入固定区域；日志落 logs\\live_7b（S25 面板可指向）。

7B 双路（S24 契约）：
- embedding_fn = make_model_embedding_fn(adapter)：g = compute_target_g(公理集文本)
- state_fn     = make_model_state_fn(adapter)：s_t = extract_hidden(观测文本)
- g 与 s_t 同空间（3584 维）→ 对齐分为真实余弦判别分（非恒 1.0）
- F3 双层防御：模型失败轮对齐分 None + model_state_unavailable 标记
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from w2.model_adapter import ModelAdapter, RemoteModelAdapter
from w3.auto_experiment import run_auto_experiment
from w3.closed_loop_runner import (make_model_embedding_fn,
                                   make_model_state_fn)

ROUNDS = 20
PORT = 20770
# v3（2026-08-26）：S29.14-S29.17 修复体制实机闭环验证（不覆盖历史
# live_7b/live_7b_v2 归档）。双路通道：驻留服务在线时走远程 encode
# （推理锁保护），否则回退本地加载。
LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "live_7b_v3" / "rounds.jsonl"
REPORT_PATH = LOG_PATH.parent / "report.json"


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if RemoteModelAdapter.ping():
        print("[7B] model_server 在线 → 远程双路（推理锁保护）")
        adapter = RemoteModelAdapter()
    else:
        adapter = ModelAdapter.from_config()
        print("[7B] loading Qwen2.5-7B-Instruct (NF4)...")
        adapter.load()
    print(f"[7B] hidden_size={len(adapter.encode('x'))}")

    report = run_auto_experiment(
        rounds=ROUNDS, port=PORT,
        log_path=str(LOG_PATH), report_path=str(REPORT_PATH),
        wait_timeout_s=60.0,
        embedding_fn=make_model_embedding_fn(adapter),
        state_fn=make_model_state_fn(adapter),
    )
    if hasattr(adapter, "unload"):
        adapter.unload()

    print(f"[7B] completed={report['completed']} "
          f"converged={report['converged_final']} "
          f"criterion={report['criterion']}")
    print(f"[7B] qs_history={[round(s, 4) for s in report['qs_history']]}")
    scores = [rd["alignment_score"] for rd in report["round_details"]]
    print(f"[7B] alignment_series={[round(s, 4) if s is not None else None for s in scores]}")
    distinct = {round(s, 4) for s in scores if s is not None}
    print(f"[7B] distinct_alignment_values={len(distinct)} (判别力: >1 即非退化解)")
    print(f"[7B] report={REPORT_PATH}")


if __name__ == "__main__":
    main()
