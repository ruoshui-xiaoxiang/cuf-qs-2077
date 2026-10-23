"""Debug persistence_roundtrip test failure."""
import sys
import tempfile
import os
import traceback

sys.path.insert(0, r"e:\QODERCN\ai_agent_monitor")
from src.common.pet_memory import PetMemory

td = tempfile.mkdtemp()
path = os.path.join(td, "pet_memory.json")

try:
    pm1 = PetMemory(path=path)
    pm1.record_request("agent_a")
    pm1.record_request("agent_b", success=False)
    pm1.record_trust(0.75)
    pm1.start_session()
    pm1.end_session()

    pm2 = PetMemory(path=path)
    checks = [
        ("total_requests == 2", pm2.total_requests == 2, pm2.total_requests),
        ("total_errors == 1", pm2.total_errors == 1, pm2.total_errors),
        ("len(trust_history) == 1", len(pm2.trust_history) == 1, len(pm2.trust_history)),
        ("trust_history[0].value == 0.75",
         pm2.trust_history[0]["value"] == 0.75 if pm2.trust_history else False,
         pm2.trust_history[0]["value"] if pm2.trust_history else "EMPTY"),
        ("total_sessions == 1", pm2.total_sessions == 1, pm2.total_sessions),
        ("first_request in achievements",
         "first_request" in pm2.achievements, pm2.achievements),
    ]

    all_pass = True
    for name, passed, actual in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name} (actual={actual})")

    if all_pass:
        print("\nALL CHECKS PASSED")
    else:
        print("\nSOME CHECKS FAILED")

except Exception:
    traceback.print_exc()
finally:
    import shutil
    shutil.rmtree(td, ignore_errors=True)
