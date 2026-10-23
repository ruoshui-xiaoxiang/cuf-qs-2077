"""Read stress test report and intervention log."""
import json, pathlib, sys

# Check report
report_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\stress_test_report.json")
if report_path.exists():
    d = json.loads(report_path.read_text(encoding="utf-8"))
    print("=== STRESS TEST REPORT ===")
    print(f"Total requests: {d.get('total_requests', '?')}")
    print(f"Successful: {d.get('successful', '?')}")
    print(f"Failed: {d.get('failed', '?')}")
    print(f"Total tokens: {d.get('total_tokens', '?')}")
    print(f"Duration: {d.get('total_duration_s', '?')}s")
    if 'results' in d:
        for i, r in enumerate(d['results']):
            status = "OK" if r.get('success') else "FAIL"
            tok = r.get('tokens', 0)
            phase = r.get('phase', '?')
            print(f"  [{i+1:02d}] {status} phase={phase} tokens={tok}")
else:
    print("No stress test report found")

# Check intervention log
ilog_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\logs\intervention_log.jsonl")
if ilog_path.exists():
    lines = ilog_path.read_text(encoding="utf-8").strip().split("\n")
    records = [json.loads(l) for l in lines if l.strip()]
    print(f"\n=== INTERVENTION LOG ({len(records)} records) ===")
    if records:
        # Summary stats
        trusts = [r.get('trust_score', 0) for r in records]
        levels = [r.get('intervention_level', 0) for r in records]
        actions = [r.get('action', '') for r in records]
        allowed = [r.get('allowed', True) for r in records]
        
        print(f"Trust range: {min(trusts):.3f} -> {max(trusts):.3f}")
        print(f"Final trust: {trusts[-1]:.3f}")
        print(f"Intervention levels: {set(levels)}")
        print(f"Actions: {dict((a, actions.count(a)) for a in set(actions))}")
        print(f"Allowed: {sum(allowed)}/{len(allowed)}")
        
        # Show first/last few
        print("\nFirst 5 records:")
        for r in records[:5]:
            print(f"  R={r.get('round_id','?')} trust={r.get('trust_score',0):.3f} "
                  f"lvl={r.get('intervention_level',0)} act={r.get('action','')} "
                  f"tok={r.get('tokens_this_round',0)} allowed={r.get('allowed','')}")
        
        print("\nLast 5 records:")
        for r in records[-5:]:
            print(f"  R={r.get('round_id','?')} trust={r.get('trust_score',0):.3f} "
                  f"lvl={r.get('intervention_level',0)} act={r.get('action','')} "
                  f"tok={r.get('tokens_this_round',0)} allowed={r.get('allowed','')}")
        
        # Check for trust changes
        trust_changes = []
        for i in range(1, len(records)):
            if abs(records[i]['trust_score'] - records[i-1]['trust_score']) > 0.001:
                trust_changes.append((records[i].get('round_id'), 
                                     records[i-1]['trust_score'],
                                     records[i]['trust_score']))
        print(f"\nTrust changes ({len(trust_changes)}):")
        for rid, old, new in trust_changes[:20]:
            delta = new - old
            print(f"  R={rid}: {old:.3f} -> {new:.3f} ({delta:+.3f})")
        if len(trust_changes) > 20:
            print(f"  ... and {len(trust_changes)-20} more")
else:
    print("\nNo intervention log found")
