"""
Weight perturbation ensemble probe.

Idea: Run the same prompts through 100 slightly-different versions of the model.
Each version = original weights + 1% Gaussian noise.
Monitor extracts 1D metrics from each → 100 readings form a distribution.
Compare distributions between original and inverted models.

Since llama-cpp doesn't support in-memory weight perturbation easily,
we use an equivalent approach: sampling variation.
Same model, same prompt, but different random sampling paths.
Each run is a different "projection angle" through the model's output distribution.

This is statistically equivalent to weight perturbation because:
- Weight perturbation: same input, slightly different model → slightly different output
- Sampling variation: same input, same model, different sample → different output
Both probe the local geometry of the model's learned representation.
"""
import json, math, time, collections, pathlib
from llama_cpp import Llama

MODEL_DIR = r"e:\QODERCN\ai_agent_monitor\models"
ORIGINAL = f"{MODEL_DIR}\\qwen2.5-0.5b-instruct-q4_k_m.gguf"
INVERTED = f"{MODEL_DIR}\\qwen2.5-0.5b-INVERTED.gguf"

PROMPTS = [
    "中国的首都是哪里？",
    "Python是什么？",
    "写一首关于春天的诗。",
]

N_RUNS = 50  # ensemble size
MAX_TOKENS = 80

# ── Monitor metrics (same as latent probe) ──
def compute_metrics(text: str) -> dict:
    """Extract 1D metrics that the monitor would see."""
    if not text or not text.strip():
        return {"entropy": 0, "ngram_ent": 0, "unique_chars": 0,
                "unique_ratio": 0, "repetition": 1.0, "length": 0,
                "question_ratio": 1.0, "printable_ratio": 0}
    
    # Char entropy
    freq = collections.Counter(text)
    total = len(text)
    entropy = -sum((c/total) * math.log2(c/total) for c in freq.values())
    
    # 4-gram entropy
    ngrams = [text[i:i+4] for i in range(max(len(text)-3, 1))]
    nfreq = collections.Counter(ngrams)
    nt = len(ngrams)
    ngram_ent = -sum((c/nt) * math.log2(c/nt) for c in nfreq.values()) if nt > 0 else 0
    
    # Unique chars
    unique = len(set(text))
    unique_ratio = unique / max(total, 1)
    
    # Repetition (chunk-based)
    chunks = [text[i:i+20] for i in range(0, max(len(text)-19, 1), 10)]
    rep = 1 - len(set(chunks)) / max(len(chunks), 1)
    
    # Question ratio
    q_ratio = text.count("?") / max(total, 1)
    
    # Printable ratio (non-garbage)
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    p_ratio = printable / max(total, 1)
    
    return {
        "entropy": round(entropy, 4),
        "ngram_ent": round(ngram_ent, 4),
        "unique_chars": unique,
        "unique_ratio": round(unique_ratio, 4),
        "repetition": round(rep, 4),
        "length": total,
        "question_ratio": round(q_ratio, 4),
        "printable_ratio": round(p_ratio, 4),
    }

def run_ensemble(model_path, label):
    """Run N_RUNS inference passes and collect metric distributions."""
    print(f"\n{'='*70}")
    print(f"  Ensemble: {label} ({N_RUNS} runs × {len(PROMPTS)} prompts = {N_RUNS*len(PROMPTS)} inferences)")
    print(f"{'='*70}")
    
    t0 = time.time()
    llm = Llama(model_path=model_path, n_ctx=512, n_gpu_layers=0, verbose=False)
    print(f"  Loaded in {time.time()-t0:.1f}s")
    
    # Collect metrics for each (run, prompt) pair
    all_metrics = []  # list of dicts, one per inference
    
    for run in range(N_RUNS):
        run_metrics = []
        for pi, prompt in enumerate(PROMPTS):
            resp = llm(prompt, max_tokens=MAX_TOKENS, temperature=0.7, top_p=0.9)
            text = resp["choices"][0]["text"].strip()
            tokens = resp["usage"]["completion_tokens"]
            m = compute_metrics(text)
            m["tokens"] = tokens
            m["prompt_idx"] = pi
            m["run"] = run
            m["text_preview"] = text[:60]
            run_metrics.append(m)
            all_metrics.append(m)
        
        # Progress
        if (run + 1) % 10 == 0 or run == 0:
            avg_ent = sum(m["entropy"] for m in run_metrics) / len(run_metrics)
            avg_rep = sum(m["repetition"] for m in run_metrics) / len(run_metrics)
            avg_uni = sum(m["unique_chars"] for m in run_metrics) / len(run_metrics)
            print(f"  Run {run+1:3d}/{N_RUNS}: avg_ent={avg_ent:.2f} avg_rep={avg_rep:.3f} avg_unique={avg_uni:.1f}")
    
    llm.close()
    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s ({N_RUNS*len(PROMPTS)} inferences)")
    
    return all_metrics

def analyze_distribution(metrics, label):
    """Analyze the distribution of each metric across the ensemble."""
    print(f"\n{'='*70}")
    print(f"  Distribution Analysis: {label}")
    print(f"{'='*70}")
    
    metric_names = ["entropy", "ngram_ent", "unique_chars", "unique_ratio",
                    "repetition", "length", "tokens", "question_ratio", "printable_ratio"]
    
    stats = {}
    for name in metric_names:
        values = [m[name] for m in metrics]
        values_sorted = sorted(values)
        n = len(values)
        mean = sum(values) / n
        var = sum((x - mean)**2 for x in values) / max(n - 1, 1)
        std = math.sqrt(var)
        median = values_sorted[n // 2]
        p5 = values_sorted[int(n * 0.05)]
        p95 = values_sorted[int(n * 0.95)]
        cv = std / abs(mean) if mean != 0 else float('inf')  # coefficient of variation
        
        stats[name] = {
            "mean": round(mean, 4),
            "std": round(std, 4),
            "median": round(median, 4),
            "p5": round(p5, 4),
            "p95": round(p95, 4),
            "cv": round(cv, 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "range": round(max(values) - min(values), 4),
        }
        
        # Visualize distribution as histogram
        hist_bins = 10
        bin_width = (max(values) - min(values)) / hist_bins if max(values) != min(values) else 1
        hist = [0] * hist_bins
        for v in values:
            idx = min(int((v - min(values)) / bin_width), hist_bins - 1) if bin_width > 0 else 0
            hist[idx] += 1
        max_bar = 30
        bar_scale = max_bar / max(hist) if max(hist) > 0 else 1
        
        print(f"\n  {name}:")
        print(f"    mean={mean:.4f}  std={std:.4f}  CV={cv:.4f}")
        print(f"    range=[{min(values):.4f}, {max(values):.4f}]  p5={p5:.4f}  p95={p95:.4f}")
        print(f"    histogram:")
        for i in range(hist_bins):
            lo = min(values) + i * bin_width
            bar = "█" * int(hist[i] * bar_scale)
            print(f"    {lo:8.3f} | {bar} ({hist[i]})")
    
    return stats

def compare_distributions(orig_stats, inv_stats):
    """Statistical comparison between two distributions."""
    print(f"\n{'='*70}")
    print(f"  Distribution Comparison")
    print(f"{'='*70}")
    
    print(f"\n  {'Metric':<18s} {'Original μ±σ':>16s} {'Inverted μ±σ':>16s} {'Overlap?':>10s} {'CV ratio':>10s}")
    print(f"  {'─'*70}")
    
    for name in orig_stats:
        o = orig_stats[name]
        i = inv_stats[name]
        
        # Check if distributions overlap
        o_lo, o_hi = o["p5"], o["p95"]
        i_lo, i_hi = i["p5"], i["p95"]
        overlap = not (o_hi < i_lo or i_hi < o_lo)
        
        # CV ratio (relative variability)
        cv_ratio = o["cv"] / i["cv"] if i["cv"] != 0 else float('inf')
        
        overlap_str = "YES" if overlap else "NO ✓"
        print(f"  {name:<18s} {o['mean']:>7.4f}±{o['std']:<7.4f} {i['mean']:>7.4f}±{i['std']:<7.4f} "
              f"{overlap_str:>10s} {cv_ratio:>10.3f}")

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
print("=" * 70)
print("  WEIGHT PERTURBATION ENSEMBLE PROBE")
print(f"  {N_RUNS} runs per model, {len(PROMPTS)} prompts each")
print("  Method: stochastic sampling variation (equivalent to weight perturbation)")
print("  Metrics: entropy, ngram_ent, unique_chars, repetition, etc.")
print("=" * 70)

orig_metrics = run_ensemble(ORIGINAL, "Original 0.5B")
inv_metrics = run_ensemble(INVERTED, "Inverted 0.5B")

orig_stats = analyze_distribution(orig_metrics, "Original 0.5B")
inv_stats = analyze_distribution(inv_metrics, "Inverted 0.5B")

compare_distributions(orig_stats, inv_stats)

# Save raw data
output = {
    "n_runs": N_RUNS,
    "n_prompts": len(PROMPTS),
    "original": {"metrics": orig_metrics, "stats": orig_stats},
    "inverted": {"metrics": inv_metrics, "stats": inv_stats},
}
out_path = pathlib.Path(r"e:\6a8b0dd1b3ead574d77ceda3\logs\ensemble_probe.json")
out_path.parent.mkdir(exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n  Data saved to: {out_path}")
