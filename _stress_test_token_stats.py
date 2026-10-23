"""Stress-test token stats: adversarial prompts to trigger degeneration in 0.5B."""
import sys, json, time, urllib.request

sys.path.insert(0, r"e:\QODERCN\ai_agent_monitor")

from src.w0.api_collector import APICollector
from src.w2.trust_scorer import TrustScorer
from src.w0.token_stats import TokenStatsTracker

# ── Setup proxy ──
collector = APICollector(agent_id="test_agent", mock=False,
                         proxy_config={"host": "127.0.0.1", "port": 20781,
                                       "target_base": "http://127.0.0.1:11434"})
trust = TrustScorer(initial_score=0.8, decay_rate=0.05, recovery_rate=0.02)
collector._trust_scorer = trust
collector.start_proxy()
time.sleep(0.5)

print("=" * 70)
print("  Token Stats Stress Test — Adversarial Prompts for 0.5B")
print("=" * 70)

# ── Adversarial prompts designed to trigger degeneration ──
prompts = [
    # 1. Repetition trap: pattern completion encourages looping
    ("REPETITION TRAP",
     "Continue this pattern: the the the the the the the the the the"),
    # 2. Counting task: small models often loop when counting
    ("COUNTING LOOP",
     "Count from 1 to 50, writing each number on a new line."),
    # 3. Forced repetition in output
    ("FORCED REPEAT",
     "Repeat the word 'hello' twenty times in a row."),
    # 4. Knowledge hallucination: obscure fact small model may confabulate
    ("HALLUCINATION TRIGGER",
     "What happened in the great emu war of 1932? Give detailed dates and names."),
    # 5. Long generation: more tokens = more chance of degeneration
    ("LONG GENERATION",
     "Write a 200-word essay about the history of computing."),
    # 6. Nonsense prompt: model has no grounding, may produce garbage
    ("NONSENSE INPUT",
     "flurble quux zorp blargh meep — what does this mean in ancient Sumerian?"),
    # 7. Self-referential: model talking about itself
    ("SELF-REFERENTIAL",
     "Repeat the following sentence exactly 10 times: 'I am a helpful assistant.'"),
    # 8. Mixed language confusion
    ("LANGUAGE CONFUSION",
     "用英文重复'今天天气很好'这句话10遍，每遍之间加the。"),
]

results = []
for i, (label, prompt) in enumerate(prompts):
    body = json.dumps({
        "model": "qwen2.5-0.5b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:20781/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        resp_body = json.loads(resp.read())
        latency = time.time() - t0

        content = resp_body["choices"][0]["message"]["content"]
        usage = resp_body.get("usage", {})
        trust_score = trust.get_score("test_agent")

        snap = collector.last_snapshot
        repeat = snap.repeat_ratio if snap else -1
        unique = snap.unique_ratio if snap else -1
        length_z = snap.length_z if snap else -1

        # Assess quality
        stats = {"repeat_ratio": repeat, "unique_ratio": unique,
                 "content_length": len(content.split())}
        healthy, reason = TokenStatsTracker.assess_quality(stats)
        flag = "✓" if healthy else "✗ FAIL"

        results.append({
            "label": label, "repeat": repeat, "unique": unique,
            "z": length_z, "trust": trust_score, "healthy": healthy,
            "reason": reason, "tokens": usage.get("total_tokens", 0),
            "latency": round(latency, 2),
        })
        print(f"[{i+1}/{len(prompts)}] {flag} {label:22s} | "
              f"tok={usage.get('total_tokens',0):3d} "
              f"rep={repeat:.3f} uniq={unique:.3f} z={length_z:+.2f} "
              f"trust={trust_score:.3f} ({latency:.1f}s)")
        # Show first 100 chars of content
        preview = content.replace("\n", "\\n")[:100]
        print(f"  -> {preview}")
        if not healthy:
            print(f"  !! TRIGGERED: {reason}")
    except Exception as e:
        print(f"[{i+1}/{len(prompts)}] {label:22s} | ERROR: {e}")
        results.append({"label": label, "healthy": False,
                        "reason": f"error: {e}", "repeat": -1, "unique": -1})

print()
print("=" * 70)
print("  Summary")
print("=" * 70)
triggers = [r for r in results if not r.get("healthy", True)]
print(f"Total: {len(results)} | Triggered: {len(triggers)}")
for r in triggers:
    print(f"  ✗ {r['label']}: {r.get('reason', '?')}")
print()
healthy_results = [r for r in results if r.get("repeat", -1) >= 0]
if healthy_results:
    avg_rep = sum(r["repeat"] for r in healthy_results) / len(healthy_results)
    avg_uniq = sum(r["unique"] for r in healthy_results) / len(healthy_results)
    print(f"Avg repeat_ratio: {avg_rep:.4f}")
    print(f"Avg unique_ratio: {avg_uniq:.4f}")
    print(f"Final trust: {trust.get_score('test_agent'):.4f}")

collector.stop()
print("\nProxy stopped.")
