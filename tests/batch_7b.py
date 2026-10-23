"""7B 双路 S21 正式批次（P_W2_5 对比数据）——py -3.12 + GPU 专用。

流程：
1. 加载 Qwen2.5-7B（NF4），构造 7B 双路（make_model_embedding_fn +
   make_model_state_fn，g 与 s_t 同空间 3584 维）。
2. 复用 S21 三公理聚焦剧本（A_M1/A_W1/A_W2_5 × 种子 × 10 轮）
   + 7B 双路注入 → run_formal_batch（logs/formal_batch_7b_v4）。
3. S22 BatchVisualizer 处理 → stats_summary.json → P_W2_5 锚点判定。
4. 输出 P_W2_5 verdict + 重排 p 值 + 实验组/对照组均值。

对比口径（S18 契约不变）：对照组 = 频率拟合回放同批观测映射分；
实验组 = 7B 判别分。τ=0.6（config 已重标定）。

v4（2026-08-26）：S29.14 方案 1 修复后实机重验——场景含 3 背景平民、
对照组观测含 hp 镜像、A_M1 走 R_M1_2 真反例（warn r3/downgrade r5）。
验证规模 3 种子（机制时序确定性，统计功效已由 v2/v3 9-36 种子锁定）。

v5（2026-08-26）：S29.15-S29.16 修复体制全量重验——证据归属裁决、
证据化边界补全、对照组轮级快照、候选池 15 条、τ 单一事实源
（0.6，visualizer 口径修复后 convergence_round 不再恒 None）。
双路通道：驻留服务在线时走远程 encode（推理锁保护，数值与本地
逐位一致已验证），否则回退本地加载。

v6（2026-08-26）：S29.17 P2 群修复后终验——轮内重复降级杜绝、
消失实体感知豁免、计分步 F3 保护、窗口卫生、默认编排防漂移等
~20 条修复生效后的整体行为确认（预期与 v5 逐位一致）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w2.model_adapter import ModelAdapter, RemoteModelAdapter
from w3.closed_loop_runner import (make_model_embedding_fn,
                                   make_model_state_fn)
from w3.formal_batch import run_formal_batch
from w3.visualizer import BatchVisualizer
from s21_selftest import scenario_factory

SEEDS = [7, 42, 2077]
OUT_DIR = Path(__file__).resolve().parents[1] / "logs" / "formal_batch_7b_v6"
CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "experiment_config.json")
BASE_PORT = 20950


def main():
    if RemoteModelAdapter.ping():
        # S29.14 encode 扩展 + S29.15 推理锁：直接复用驻留模型
        print("[7B] model_server 在线 → 远程双路（推理锁保护）")
        adapter = RemoteModelAdapter()
    else:
        adapter = ModelAdapter.from_config()
        print("[7B] loading Qwen2.5-7B-Instruct (NF4)...")
        adapter.load()
    print(f"[7B] hidden_size={len(adapter.encode('x'))}")
    embedding_fn = make_model_embedding_fn(adapter)
    state_fn = make_model_state_fn(adapter)

    conditions = [
        {"axiom": "A_M1", "seeds": SEEDS,
         "mock_factory": scenario_factory("A_M1", 10)},
        {"axiom": "A_W1", "seeds": SEEDS,
         "mock_factory": scenario_factory("A_W1", 10)},
        {"axiom": "A_W2_5", "seeds": SEEDS,
         "mock_factory": scenario_factory("A_W2_5", 10)},
    ]
    summary = run_formal_batch(
        conditions, rounds=10, out_dir=str(OUT_DIR), config_path=CONFIG,
        base_port=BASE_PORT, wait_timeout_s=5.0,
        embedding_fn=embedding_fn, state_fn=state_fn)
    if hasattr(adapter, "unload"):
        adapter.unload()

    print("[7B] axiom_coverage:", summary["axiom_coverage"])
    print("[7B] 各运行 completed:", [
        r.get("virtual_lifespan") for c in summary["conditions"]
        for r in c["runs"]])

    # 统计层 → P_W2_5 判定
    viz = BatchVisualizer(str(OUT_DIR))
    stats = viz.render_all()
    anchors = stats["anchors"]
    st = stats["statistical_tests"]
    p = st["pooled"]["permutation"]
    print("=" * 60)
    print("P_W2_5 verdict:", anchors["P_W2_5"]["verdict"])
    print("P_W2_5 evidence:", json.dumps(anchors["P_W2_5"]["evidence"],
                                         ensure_ascii=False))
    print("pooled permutation:", json.dumps({
        "n": p["n"], "observed_mean_diff": round(p["observed_mean_diff"], 4),
        "p_value": round(p["p_value"], 6), "enumerated": p["enumerated"]}))
    print("pooled exp_mean:", [round(x, 4) for x in st["pooled"]["exp_means"]],
          "| baseline_mean:",
          [round(x, 4) for x in st["pooled"]["baseline_means"]])
    for cond in summary["conditions"]:
        entry = st["per_condition"][cond["axiom"]]
        print(f"  {cond['axiom']}: exp={[round(x,3) for x in entry['exp_means']]} "
              f"base={[round(x,3) for x in entry['baseline_means']]}")
    print("=" * 60)
    print("stats_summary.json:", OUT_DIR / "stats" / "stats_summary.json")


if __name__ == "__main__":
    main()
