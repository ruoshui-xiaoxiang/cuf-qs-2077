"""Analyze real proxy request log from 14B stress test."""
import json, pathlib

log_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\logs\proxy_request_log.jsonl")
if not log_path.exists():
    print("No proxy_request_log.jsonl found!")
    exit(1)

lines = log_path.read_text(encoding="utf-8").strip().split("\n")
records = [json.loads(l) for l in lines if l.strip()]
print(f"=== PROXY REQUEST LOG: {len(records)} records ===\n")

# Trust trajectory
print("--- Trust Trajectory (every 5th request) ---")
print(f"{'Seq':>4} {'TrustBefore':>12} {'TrustAfter':>12} {'Delta':>8} {'Level':>6} {'Action':>14} {'Tokens':>7} {'VerifyOK':>9} {'Injected':>9}")
print("-" * 100)
for r in records:
    seq = r['seq']
    if seq <= 20 or seq % 5 == 0 or seq == len(records):
        delta = r['trust_after'] - r['trust_before']
        inj = "YES" if r.get('injected_failure') else ""
        print(f"{seq:4d} {r['trust_before']:12.4f} {r['trust_after']:12.4f} {delta:+8.4f} "
              f"{r['intervention_level']:6d} {r['action']:>14} {r['tokens']:7d} "
              f"{'OK' if r['verify_ok'] else 'FAIL':>9} {inj:>9}")

# Summary stats
trusts_before = [r['trust_before'] for r in records]
trusts_after = [r['trust_after'] for r in records]
actions = {}
for r in records:
    a = r['action']
    actions[a] = actions.get(a, 0) + 1

verify_ok_count = sum(1 for r in records if r['verify_ok'])
verify_fail_count = sum(1 for r in records if not r['verify_ok'])
injected_count = sum(1 for r in records if r.get('injected_failure'))
total_tokens = sum(r['tokens'] for r in records)

print(f"\n--- Summary ---")
print(f"Total requests logged: {len(records)}")
print(f"Trust range: {min(trusts_before):.4f} -> {min(trusts_after):.4f}")
print(f"Final trust: {trusts_after[-1]:.4f}")
print(f"Verify OK: {verify_ok_count}, Verify FAIL: {verify_fail_count}")
print(f"Injected failures: {injected_count}")
print(f"Total tokens: {total_tokens}")
print(f"Action distribution: {actions}")

# Intervention level transitions
print(f"\n--- Intervention Level Timeline ---")
prev_level = -1
for r in records:
    lvl = r['intervention_level']
    if lvl != prev_level:
        print(f"  Seq {r['seq']:3d}: level {prev_level} -> {lvl} (trust={r['trust_before']:.4f}, action={r['action']})")
        prev_level = lvl

# Phase analysis
print(f"\n--- Phase Analysis ---")
phase_boundaries = [(1, 15, "P1 Warm-up"), (16, 30, "P2 Hard"),
                    (31, 54, "P3 Multi-turn"), (55, 69, "P4 Code"),
                    (70, 99, "P5 Rapid")]
for start, end, name in phase_boundaries:
    phase_recs = [r for r in records if start <= r['seq'] <= end]
    if not phase_recs:
        continue
    ok = sum(1 for r in phase_recs if r['verify_ok'])
    fail = len(phase_recs) - ok
    tok = sum(r['tokens'] for r in phase_recs)
    t_start = phase_recs[0]['trust_before']
    t_end = phase_recs[-1]['trust_after']
    levels = set(r['intervention_level'] for r in phase_recs)
    print(f"  {name:15s}: {len(phase_recs):3d} reqs, {ok:2d} ok, {fail:2d} fail, "
          f"{tok:6d} tok, trust {t_start:.3f}->{t_end:.3f}, levels={levels}")
