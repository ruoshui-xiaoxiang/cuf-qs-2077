"""Test: Compare original vs inverted 0.5B model outputs."""
from llama_cpp import Llama
import time

MODEL_DIR = r"e:\QODERCN\ai_agent_monitor\models"
ORIGINAL = f"{MODEL_DIR}\\qwen2.5-0.5b-instruct-q4_k_m.gguf"
INVERTED = f"{MODEL_DIR}\\qwen2.5-0.5b-INVERTED.gguf"

PROMPTS = [
    "你好，请介绍一下自己。",
    "Python是什么？",
    "1+1等于几？",
    "天空是什么颜色的？",
    "写一首关于月亮的诗。",
]

def test_model(path, label, n_ctx=512, max_tokens=100):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {path.split(chr(92))[-1]}")
    print(f"{'='*60}")
    try:
        t0 = time.time()
        llm = Llama(model_path=path, n_ctx=n_ctx, n_gpu_layers=0, verbose=False)
        load_time = time.time() - t0
        print(f"  Loaded in {load_time:.1f}s\n")
        
        results = []
        for i, prompt in enumerate(PROMPTS):
            t0 = time.time()
            resp = llm(prompt, max_tokens=max_tokens, temperature=0.7, top_p=0.9)
            elapsed = time.time() - t0
            text = resp["choices"][0]["text"].strip()
            tokens = resp["usage"]["completion_tokens"]
            results.append({"prompt": prompt, "text": text, "tokens": tokens, "time": elapsed})
            print(f"  Q{i+1}: {prompt}")
            print(f"  A: {text[:200]}{'...' if len(text)>200 else ''}")
            print(f"  [{tokens} tokens, {elapsed:.1f}s]\n")
        
        llm.close()
        return results
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

print("=" * 60)
print("  Experiment: Original vs Inverted 0.5B Model")
print("  Inverted = every bit in tensor data flipped (XOR 0xFF)")
print("=" * 60)

orig_results = test_model(ORIGINAL, "ORIGINAL MODEL")
inv_results = test_model(INVERTED, "INVERTED MODEL (all weights XOR 0xFF)")

if orig_results and inv_results:
    print(f"\n{'='*60}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*60}")
    for i, (o, inv) in enumerate(zip(orig_results, inv_results)):
        print(f"\n  Q{i+1}: {o['prompt']}")
        print(f"  Original:  {o['text'][:120]}...")
        print(f"  Inverted:  {inv['text'][:120]}...")
        print(f"  Orig tokens: {o['tokens']}, Inv tokens: {inv['tokens']}")
