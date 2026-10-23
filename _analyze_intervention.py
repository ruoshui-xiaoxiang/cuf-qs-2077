"""Analyze intervention log from stress test."""
import json
import statistics
from collections import Counter
from datetime import datetime

LOG = r"e:\QODERCN\ai_agent_monitor\logs\intervention_log.jsonl"

lines = open(LOG, encoding="utf-8").readlines()
records = [json.loads(l) for l in lines]

print("=" * 70)
print("  AI Agent Monitor - Intervention Layer Analysis Report")
print("=" * 70)
print()

# ── Overview ──
print(f"[Overview]")
print(f"  Total intervention records : {len(records)}")
print(f"  Round range                : {records[0]['round_id']} ~ {records[-1]['round_id']}")
print(f"  Time range                 : {records[0]['ts']} ~ {records[-1]['ts']}")
t0 = datetime.fromisoformat(records[0]['ts'])
t1 = datetime.fromisoformat(records[-1]['ts'])
print(f"  Duration                   : {(t1 - t0).total_seconds():.1f}s")
print()

# ── Trust Score Trajectory ──
trusts = [r['trust_score'] for r in records]
print(f"[Trust Score Trajectory]")
print(f"  Initial   : {trusts[0]:.3f}")
print(f"  Minimum   : {min(trusts):.3f}")
print(f"  Final     : {trusts[-1]:.3f}")
print(f"  Mean      : {statistics.mean(trusts):.3f}")
print(f"  Stddev    : {statistics.stdev(trusts):.3f}" if len(trusts) > 1 else "")

# Show trust at key milestones
milestones = [0, len(records)//4, len(records)//2, 3*len(records)//4, len(records)-1]
print(f"  Milestones:")
for idx in milestones:
    r = records[idx]
    print(f"    Round {r['round_id']:3d} | trust={r['trust_score']:.3f} | level={r['intervention_level']} | {r['trust_level']}")
print()

# ── Trust Level Transitions ──
trust_levels_seq = [r['trust_level'] for r in records]
transitions = []
prev = None
for i, tl in enumerate(trust_levels_seq):
    if tl != prev:
        transitions.append((records[i]['round_id'], tl))
    prev = tl

print(f"[Trust Level Transitions]")
print(f"  Total transitions: {len(transitions)}")
for rnd, tl in transitions:
    print(f"    Round {rnd:3d} -> {tl}")
print()

# ── Intervention Level Distribution ──
levels = Counter(r['intervention_level'] for r in records)
print(f"[Intervention Level Distribution]")
level_names = {0: "none (monitoring only)", 1: "inject (evidence correction)", 2: "rate_limit (throttle)", 3: "circuit_break (block)"}
for lv in sorted(levels):
    pct = levels[lv] / len(records) * 100
    name = level_names.get(lv, "unknown")
    print(f"  Level {lv} ({name}): {levels[lv]:3d} times ({pct:.1f}%)")
print()

# ── Action Distribution ──
actions = Counter(r['action'] for r in records)
print(f"[Action Distribution]")
for a, c in actions.most_common():
    print(f"  {a:30s}: {c:3d} times")
print()

# ── Allowed vs Denied ──
allowed_true = sum(1 for r in records if r['allowed'])
allowed_false = len(records) - allowed_true
print(f"[Request Allow/Deny]")
print(f"  Allowed : {allowed_true:3d} ({allowed_true/len(records)*100:.1f}%)")
print(f"  Denied  : {allowed_false:3d} ({allowed_false/len(records)*100:.1f}%)")
print()

# ── Token Analysis ──
tokens_list = [r['tokens_this_round'] for r in records]
cum_tokens = [r['cumulative_tokens'] for r in records]
print(f"[Token Consumption]")
print(f"  Per-round tokens:")
print(f"    Min     : {min(tokens_list):6d}")
print(f"    Max     : {max(tokens_list):6d}")
print(f"    Mean    : {statistics.mean(tokens_list):8.1f}")
print(f"    Median  : {statistics.median(tokens_list):8.1f}")
print(f"    Stdev   : {statistics.stdev(tokens_list):8.1f}")
print(f"    Total   : {sum(tokens_list):8d}")
print(f"  Final cumulative: {max(cum_tokens)}")
print()

# Token consumption by intervention level
print(f"[Token Consumption by Intervention Level]")
for lv in sorted(levels):
    lv_tokens = [r['tokens_this_round'] for r in records if r['intervention_level'] == lv]
    if lv_tokens:
        name = level_names.get(lv, "unknown")
        print(f"  Level {lv} ({name}):")
        print(f"    Count: {len(lv_tokens):3d} | Total: {sum(lv_tokens):6d} | Mean: {statistics.mean(lv_tokens):.1f} | Max: {max(lv_tokens)}")
print()

# ── Delay Analysis ──
delays = [r['delay_s'] for r in records]
non_zero_delays = [d for d in delays if d > 0]
print(f"[Delay Analysis (Rate Limiting)]")
print(f"  Total rounds with delay: {len(non_zero_delays)}/{len(delays)}")
if non_zero_delays:
    print(f"  Delay range: {min(non_zero_delays):.3f}s ~ {max(non_zero_delays):.3f}s")
    print(f"  Total delay injected: {sum(non_zero_delays):.3f}s")
    print(f"  Mean delay (when active): {statistics.mean(non_zero_delays):.3f}s")
else:
    print(f"  No delays were applied")
print()

# ── Rule Transitions ──
rule_trans = [r for r in records if r.get('rule_transitions')]
print(f"[Rule Engine Transitions]")
print(f"  Total rule transition events: {len(rule_trans)}")
for rt in rule_trans[:10]:
    print(f"    Round {rt['round_id']:3d}: {rt['rule_transitions']}")
if len(rule_trans) > 10:
    print(f"    ... and {len(rule_trans) - 10} more")
print()

# ── Token Savings Estimation ──
print(f"[Token Savings Estimation]")
# Baseline: all tokens without any intervention
total_actual = sum(tokens_list)
# If circuit_break had blocked all requests at level 3, how many tokens saved
level3_tokens = sum(r['tokens_this_round'] for r in records if r['intervention_level'] == 3)
level2_tokens = sum(r['tokens_this_round'] for r in records if r['intervention_level'] == 2)
# If rate_limit had reduced token usage by ~30% (estimated)
print(f"  Total actual tokens: {total_actual}")
print(f"  Tokens during circuit_break (level 3): {level3_tokens}")
print(f"  Tokens during rate_limit (level 2): {level2_tokens}")
print(f"  Tokens during inject (level 1): {sum(r['tokens_this_round'] for r in records if r['intervention_level'] == 1)}")
print(f"  Tokens during none (level 0): {sum(r['tokens_this_round'] for r in records if r['intervention_level'] == 0)}")
print()

# ── Phase Analysis ──
print(f"[Phase-by-Phase Analysis]")
# Group by trust level phases
phase_data = {}
for r in records:
    tl = r['trust_level']
    if tl not in phase_data:
        phase_data[tl] = {'count': 0, 'tokens': 0, 'rounds': []}
    phase_data[tl]['count'] += 1
    phase_data[tl]['tokens'] += r['tokens_this_round']
    phase_data[tl]['rounds'].append(r['round_id'])

phase_order = ['high', 'medium', 'low', 'critical']
for phase in phase_order:
    if phase in phase_data:
        pd = phase_data[phase]
        print(f"  Phase '{phase}':")
        print(f"    Rounds: {pd['count']:3d} (R{min(pd['rounds'])}-R{max(pd['rounds'])})")
        print(f"    Tokens: {pd['tokens']:6d} (avg {pd['tokens']/pd['count']:.1f}/round)")
print()

# ── Detailed Timeline (first 10 + last 10 records) ──
print(f"[Detailed Timeline - First 10 Records]")
print(f"  {'Round':>5} | {'Trust':>6} | {'Level':>5} | {'Action':>25} | {'Tokens':>7} | {'Allowed':>7} | {'Delay':>6}")
print(f"  {'-'*5}-+-{'-'*6}-+-{'-'*5}-+-{'-'*25}-+-{'-'*7}-+-{'-'*7}-+-{'-'*6}")
for r in records[:10]:
    print(f"  {r['round_id']:5d} | {r['trust_score']:6.3f} | {r['intervention_level']:5d} | {r['action']:>25} | {r['tokens_this_round']:7d} | {str(r['allowed']):>7} | {r['delay_s']:6.3f}")

print()
print(f"[Detailed Timeline - Last 10 Records]")
print(f"  {'Round':>5} | {'Trust':>6} | {'Level':>5} | {'Action':>25} | {'Tokens':>7} | {'Allowed':>7} | {'Delay':>6}")
print(f"  {'-'*5}-+-{'-'*6}-+-{'-'*5}-+-{'-'*25}-+-{'-'*7}-+-{'-'*7}-+-{'-'*6}")
for r in records[-10:]:
    print(f"  {r['round_id']:5d} | {r['trust_score']:6.3f} | {r['intervention_level']:5d} | {r['action']:>25} | {r['tokens_this_round']:7d} | {str(r['allowed']):>7} | {r['delay_s']:6.3f}")

print()
print("=" * 70)
print("  End of Report")
print("=" * 70)
