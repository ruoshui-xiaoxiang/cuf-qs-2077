"""S24 LLM 接入实机验证（审计 A1/A3 真实模型路径）。

运行环境：必须 py -3.12（torch 2.9.1+cu128 GPU），本地 Qwen2.5-7B-Instruct
（E:\\HF_CACHE，NF4 量化）。模型首次加载约数分钟，显存 ≈4-5GB。

验证点：
1. ModelAdapter.generate_text 真实生成（贪心确定性）。
2. extract_hidden / make_model_state_fn：7B 隐状态对齐分有判别力
   （不同观测文本 → 不同对齐分，解决离线路恒 1.0 退化解，A3 核心）。
3. LLMAssistant live：attach 后 hypothesis_nl 由 7B 生成 + advisory 标记。
4. 闭环（mock 游戏）注入 LLM：轮末 advisory_notes 归档真实模型文本，
   且判定轨迹与离线一致（LLM 零决策回流）。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w2.model_adapter import ModelAdapter
from w2.llm_assistant import LLMAssistant
from w3.closed_loop_runner import (make_model_state_fn,
                                   make_model_embedding_fn)
from w3.log_recorder import LogRecorder
from w2.qs_kernel import QSKernel

adapter = ModelAdapter.from_config()
print("[1/4] loading Qwen2.5-7B-Instruct (NF4, local_only)...")
adapter.load()
assert adapter.loaded, "model must load"
print(f"[1/4] loaded, hidden_size={len(adapter.encode('x'))}")

# ── 1. generate_text 真实生成 ──────────────────────────────
print("[2/4] generate_text...")
t = adapter.generate_text(
    "请用一句话描述：反例驱动的公理降级机制。", max_new_tokens=48)
print(f"[2/4] GENERATED: {t!r}")
assert t and len(t) > 0, "generate_text must produce non-empty text"

# ── 2. 7B 隐状态对齐分判别力（A3 核心）────────────────────
print("[3/4] extract_hidden alignment discrimination...")
g_emb = adapter.compute_target_g("维持稳态：公理与观测保持一致")
s1 = adapter.extract_hidden("观测：僵尸 HP 为 0 且仍在扫描中")
s2 = adapter.extract_hidden("观测：正常 NPC 存活且 HP 满")
qs = QSKernel()
qs.compute_target_g(g_emb)
a1 = qs.compute_alignment_score(s1)
a2 = qs.compute_alignment_score(s2)
print(f"[3/4] g_dim={len(g_emb)} a(zombie)={a1:.4f} a(normal)={a2:.4f} "
      f"diff={abs(a1 - a2):.4f}")
assert abs(a1 - a2) > 1e-3, \
    "7B hidden-state alignment must discriminate observations (A3)"

# ── 3. LLMAssistant live 生成 ──────────────────────────────
llm = LLMAssistant(use_model=True)
llm.attach_model_adapter(adapter)
h = llm.hypothesis_nl({
    "axiom_id": "A_W2_5", "anchor": "P_W2_5",
    "predicate": "HP 归零的实体会死亡并消失"})
print(f"[3/4] hypothesis_nl(live): {h['text']!r}")
assert h["meta"]["mode"] == "live", h["meta"]
assert h["meta"]["model_generated"] is True, h["meta"]
assert h["advisory"] is True

# ── 4. 闭环（mock）注入 LLM：advisory 归档 + 判定隔离 ──────
print("[4/4] closed-loop with LLM (mock game, 3 rounds)...")
from s16_selftest import (setup_env as s16_setup,
                          make_runner as s16_make_runner, teardown)
with tempfile.TemporaryDirectory() as d:
    log_path = Path(d) / "loop.jsonl"
    env = s16_setup(20799, log_path, zombies=True)
    connector, mock = env[0], env[1]
    try:
        runner = s16_make_runner(env, rounds=3)
        runner.inject_axioms_from_w1()
        runner.llm_assistant = llm
        # 7B 双路：g 与 s_t 同空间（3584 维），替换离线 32 维退化解
        runner.embedding_fn = make_model_embedding_fn(adapter)
        runner.state_fn = make_model_state_fn(adapter)
        report = runner.run()
        entries = LogRecorder(log_path).load_all()
        notes = [n for e in entries
                 for n in (e["w3"].get("advisory_notes") or [])]
        gen = [n for n in notes
               if n.get("output", {}).get("meta", {}).get("model_generated")]
        print(f"[4/4] advisory_notes={len(notes)} "
              f"model_generated={len(gen)}")
        assert len(notes) >= 1, "advisory notes must be archived"
        assert len(gen) >= 1, "at least one note must be model-generated"
        # 判定隔离：3 轮 zombies 轨迹不受 LLM 影响
        assert report["axiom_final_status"]["A_W2_5"] == "deprecated", \
            report["axiom_final_status"]
        assert report["c_extend_series"] == [1, 3, 5], \
            report["c_extend_series"]
        print(f"[4/4] qs_history={report['qs_history']}")
    finally:
        teardown(connector, mock)

adapter.unload()
print("\nS24 LLM LIVE VERIFICATION PASSED")
