"""18/36 种子 × 3 组 7B 批次（用户要求，py -3.12 + GPU）。

每组使用**不同的种子集**（18 种子：1-18/19-36/37-54；36 种子：
1-36/37-72/73-108），测种子采样的稳定性；mock 剧本确定性保证
组内轨迹可复现。输出 logs\\formal_batch_7b_v3\\seed{N}_run{i}。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w2.model_adapter import ModelAdapter
from w3.closed_loop_runner import (make_model_embedding_fn,
                                   make_model_state_fn)
from w3.formal_batch import run_formal_batch
from w3.visualizer import BatchVisualizer
from s21_selftest import scenario_factory

CONFIG = str(Path(__file__).resolve().parents[1] / "config" / "experiment_config.json")
BASE = Path(__file__).resolve().parents[1] / "logs" / "formal_batch_7b_v3"


def run_once(adapter, seeds, out_dir, base_port):
    emb = make_model_embedding_fn(adapter)
    st = make_model_state_fn(adapter)
    conds = [
        {"axiom": "A_M1", "seeds": seeds,
         "mock_factory": scenario_factory("A_M1", 10)},
        {"axiom": "A_W1", "seeds": seeds,
         "mock_factory": scenario_factory("A_W1", 10)},
        {"axiom": "A_W2_5", "seeds": seeds,
         "mock_factory": scenario_factory("A_W2_5", 10)},
    ]
    run_formal_batch(conds, rounds=10, out_dir=out_dir, config_path=CONFIG,
                     base_port=base_port, wait_timeout_s=5.0,
                     embedding_fn=emb, state_fn=st)
    stats = BatchVisualizer(out_dir).render_all()
    return stats


def main():
    adapter = ModelAdapter.from_config()
    print("[7B] loading...")
    adapter.load()
    print(f"[7B] loaded hidden_size={len(adapter.encode('x'))}")

    # 18 种子 × 3 组
    for i in range(3):
        seeds = list(range(1 + i * 18, 1 + (i + 1) * 18))
        out = str(BASE / f"seed18_run{i + 1}")
        stats = run_once(adapter, seeds, out, 21000 + i * 60)
        p = stats["statistical_tests"]["pooled"]["permutation"]
        print(f"[seed18 run{i+1}] seeds={seeds[0]}-{seeds[-1]} "
              f"p={p['p_value']:.4f} diff={p['observed_mean_diff']:.4f} "
              f"verdict={stats['anchors']['P_W2_5']['verdict']}")

    # 36 种子 × 3 组
    for i in range(3):
        seeds = list(range(1 + i * 36, 1 + (i + 1) * 36))
        out = str(BASE / f"seed36_run{i + 1}")
        stats = run_once(adapter, seeds, out, 21300 + i * 120)
        p = stats["statistical_tests"]["pooled"]["permutation"]
        print(f"[seed36 run{i+1}] seeds={seeds[0]}-{seeds[-1]} "
              f"p={p['p_value']:.4f} diff={p['observed_mean_diff']:.4f} "
              f"verdict={stats['anchors']['P_W2_5']['verdict']}")

    adapter.unload()
    print("[7B] all 6 groups done ->", BASE)


if __name__ == "__main__":
    main()
