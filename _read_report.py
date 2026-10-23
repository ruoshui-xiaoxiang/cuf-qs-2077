import json

d = json.load(open(r"e:\QODERCN\ai_agent_monitor\stress_test_report.json", encoding="utf-8"))

print("=== Stress Test Report Summary ===")
print(json.dumps(d["summary"], indent=2, ensure_ascii=False))
print()
print("=== Per-Phase Breakdown ===")
for k, v in d["phase_stats"].items():
    print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
print()

# Per-request token distribution by phase
print("=== Per-Request Token Distribution ===")
phases = {}
for r in d["per_request"]:
    label = r["label"]
    phase = label.split("_")[0] if "_" in label else "unknown"
    if phase not in phases:
        phases[phase] = []
    phases[phase].append(r["tokens"])

for phase in sorted(phases):
    toks = phases[phase]
    avg = sum(toks) / len(toks) if toks else 0
    print(f"  {phase:10s}: {len(toks):3d} reqs, total={sum(toks):6d}, avg={avg:.0f}, min={min(toks)}, max={max(toks)}")

print()
# Latency distribution
print("=== Per-Phase Latency ===")
lat_phases = {}
for r in d["per_request"]:
    label = r["label"]
    phase = label.split("_")[0] if "_" in label else "unknown"
    if phase not in lat_phases:
        lat_phases[phase] = []
    lat_phases[phase].append(r["latency"])

for phase in sorted(lat_phases):
    lats = lat_phases[phase]
    avg = sum(lats) / len(lats) if lats else 0
    print(f"  {phase:10s}: avg={avg:.2f}s, min={min(lats):.2f}s, max={max(lats):.2f}s, total={sum(lats):.1f}s")

# Failed requests
failed = [r for r in d["per_request"] if not r["ok"]]
print(f"\n=== Failed Requests: {len(failed)} ===")
for f in failed[:5]:
    print(f"  #{f['seq']} ({f['label']}): {f.get('error', 'unknown')}")
