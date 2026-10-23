"""Deep debug persistence_roundtrip."""
import sys
import tempfile
import os
import json

sys.path.insert(0, r"e:\QODERCN\ai_agent_monitor")
from src.common.pet_memory import PetMemory

# Simulate exact test conditions
td = tempfile.mkdtemp()
path = os.path.join(td, "pet_memory.json")
print(f"Test path: {path}")
print(f"Path exists before: {os.path.exists(path)}")

pm1 = PetMemory(path=path)
print(f"After pm1 init: total_requests={pm1.total_requests}, history_len={len(pm1.trust_history)}")

pm1.record_request("agent_a")
print(f"After record_request(a): total_requests={pm1.total_requests}")

pm1.record_request("agent_b", success=False)
print(f"After record_request(b,False): total_requests={pm1.total_requests}, errors={pm1.total_errors}")

pm1.record_trust(0.75)
print(f"After record_trust(0.75): history_len={len(pm1.trust_history)}")

# Check file content
with open(path, "r", encoding="utf-8") as f:
    raw = json.load(f)
print(f"File trust_history len: {len(raw.get('trust_history', []))}")

pm1.start_session()
pm1.end_session()

# Check file content again
with open(path, "r", encoding="utf-8") as f:
    raw2 = json.load(f)
print(f"File after sessions - trust_history len: {len(raw2.get('trust_history', []))}")
print(f"File after sessions - total_requests: {raw2.get('total_requests')}")

# Reload
pm2 = PetMemory(path=path)
print(f"\npm2 loaded:")
print(f"  total_requests: {pm2.total_requests}")
print(f"  total_errors: {pm2.total_errors}")
print(f"  trust_history len: {len(pm2.trust_history)}")
print(f"  total_sessions: {pm2.total_sessions}")
print(f"  achievements: {pm2.achievements}")

# Cleanup
import shutil
shutil.rmtree(td, ignore_errors=True)
print("\nDone!")
