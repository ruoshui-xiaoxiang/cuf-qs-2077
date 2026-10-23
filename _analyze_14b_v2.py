"""Analyze real intervention log from 14B stress test."""
import json, pathlib

log_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\logs\intervention_log.jsonl")
if not log_path.exists():
    print("No intervention_log.jsonl found!")
    exit(1)

lines = log_path.read_text(encoding="utf-8").strip().split("\n")
records = [json.loads(l) for l in lines if l.strip()]
print(f"=== INTERVENTION LOG: {len(records)} records ===\n")

# Trust trajectory
trusts = [r['trust_score'] for r in records]
levels = [r['intervention_level'] for r in records]
actions = [r.get('action', '') for r in records]
tokens_list = [r.get('tokens_this_round', 0) for r in records]
cum_tokens = [r.get('cumulative_tokens', 0) for r in records]

print("--- Trust Trajectory (sampled) ---")
print(f"{'Idx':>4} {'Round':>6} {'Trust':>8} {'Level':>6} {'Action':>20} {'Tokens':>8} {'CumTok':>8}")
print("-" * 70)
step = max(1, len(records) // 40)
for i in range(0, len(records), step):
    r = records[i]
    print(f"{i:4d} {r.get('round_id','?'):>6} {r['trust_score']:8.4f} "
          f"{r['intervention_level']:6d} {r.get('action',''):>20} "
          f"{r.get('tokens_this_round',0):8d} {r.get('cumulative_tokens',0):8d}")
# Always show last
r = records[-1]
print(f"{len(records)-1:4d} {r.get('round_id','?'):>6} {r['trust_score']:8.4f} "
      f"{r['intervention_level']:6d} {r.get('action',''):>20} "
      f"{r.get('tokens_this_round',0):8d} {r.get('cumulative_tokens',0):8d}")

# Summary
print(f"\n--- Summary ---")
print(f"Total records: {len(records)}")
print(f"Trust: {trusts[0]:.4f} -> {trusts[-1]:.4f}")
print(f"Min trust: {min(trusts):.4f}")
print(f"Max trust: {max(trusts):.4f}")
print(f"Total tokens (cumulative): {cum_tokens[-1] if cum_tokens else 0}")

# Action distribution
action_dist = {}
for a in actions:
    action_dist[a] = action_dist.get(a, 0) + 1
print(f"Action distribution: {action_dist}")

# Level distribution
level_dist = {}
for l in levels:
    level_dist[l] = level_dist.get(l, 0) + 1
print(f"Level distribution: {level_dist}")

# Level transitions
print(f"\n--- Intervention Level Transitions ---")
prev_level = -1
for i, r in enumerate(records):
    lvl = r['intervention_level']
    if lvl != prev_level:
        print(f"  Record {i:3d} (R={r.get('round_id','?')}): "
              f"level {prev_level} -> {lvl}, trust={r['trust_score']:.4f}, "
              f"action={r.get('action','')}")
        prev_level = lvl

# Token analysis per level
print(f"\n--- Token Consumption by Intervention Level ---")
for lvl in sorted(level_dist.keys()):
    lvl_records = [r for r in records if r['intervention_level'] == lvl]
    lvl_tokens = [r.get('tokens_this_round', 0) for r in lvl_records]
    avg_tok = sum(lvl_tokens) / len(lvl_tokens) if lvl_tokens else 0
    max_tok = max(lvl_tokens) if lvl_tokens else 0
    min_tok = min(lvl_tokens) if lvl_tokens else 0
    total_tok = sum(lvl_tokens)
    print(f"  Level {lvl}: {len(lvl_records):3d} records, "
          f"avg={avg_tok:.0f}tok, min={min_tok}, max={max_tok}, total={total_tok}")

# Rule transitions
transitions = [r for r in records if r.get('rule_transitions')]
if transitions:
    print(f"\n--- Rule Transitions ({len(transitions)}) ---")
    for r in transitions[:10]:
        print(f"  R={r.get('round_id')}: {r.get('rule_transitions')}")

# Trust level names
trust_levels = {}
for r in records:
    tl = r.get('trust_level', '')
    if tl:
        trust_levels[tl] = trust_levels.get(tl, 0) + 1
print(f"\n--- Trust Level Distribution ---")
for tl, count in sorted(trust_levels.items()):
    print(f"  {tl}: {count}")
