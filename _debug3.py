"""Run persistence test in the same process as other tests to reproduce interference."""
import sys
import tempfile
import os
import json

sys.path.insert(0, r"e:\QODERCN\ai_agent_monitor")
from src.common.pet_memory import PetMemory

# Run trust_history_rolling_window first (the suspect)
print("=== Running trust_history_rolling_window ===")
td1 = tempfile.mkdtemp()
try:
    path1 = os.path.join(td1, "pet_memory.json")
    pm = PetMemory(path=path1)
    for i in range(250):
        pm.record_trust(0.5 + (i % 10) * 0.05)
    history = pm.trust_history
    assert len(history) == 200, f"Expected 200, got {len(history)}"
    print(f"  PASS: rolling window history len = {len(history)}")
finally:
    import shutil
    shutil.rmtree(td1, ignore_errors=True)

# Now run persistence_roundtrip
print("\n=== Running persistence_roundtrip ===")
td2 = tempfile.mkdtemp()
try:
    path2 = os.path.join(td2, "pet_memory.json")
    print(f"  Path: {path2}")
    print(f"  Path exists: {os.path.exists(path2)}")

    pm1 = PetMemory(path=path2)
    print(f"  pm1 init: requests={pm1.total_requests}, history={len(pm1.trust_history)}")

    pm1.record_request("agent_a")
    pm1.record_request("agent_b", success=False)
    pm1.record_trust(0.75)
    print(f"  pm1 after ops: requests={pm1.total_requests}, history={len(pm1.trust_history)}")

    pm1.start_session()
    pm1.end_session()

    # Check raw file
    with open(path2, "r", encoding="utf-8") as f:
        raw = json.load(f)
    print(f"  Raw file: requests={raw.get('total_requests')}, history_len={len(raw.get('trust_history', []))}")

    pm2 = PetMemory(path=path2)
    print(f"  pm2 loaded: requests={pm2.total_requests}, history={len(pm2.trust_history)}")

    assert len(pm2.trust_history) == 1, f"Expected 1, got {len(pm2.trust_history)}"
    print("  PASS!")
finally:
    shutil.rmtree(td2, ignore_errors=True)
