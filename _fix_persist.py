"""Fix persistence_roundtrip test - direct replacement."""

PATH = r"e:\QODERCN\ai_agent_monitor\tests\test_pet_memory.py"
with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

OLD = '''def test_persistence_roundtrip():
    """Data should survive save/load cycle."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")

        # Create and populate
        pm1 = PetMemory(path=path)
        pm1.record_request("agent_a")
        pm1.record_request("agent_b", success=False)
        pm1.record_trust(0.75)
        pm1.start_session()
        pm1.end_session()

        # Reload
        pm2 = PetMemory(path=path)
        assert pm2.total_requests == 2
        assert pm2.total_errors == 1
        assert len(pm2.trust_history) == 1
        assert pm2.trust_history[0]["value"] == 0.75
        assert pm2.total_sessions == 1
        assert "first_request" in pm2.achievements'''

NEW = '''def test_persistence_roundtrip():
    """Data should survive save/load cycle."""
    td = tempfile.mkdtemp()
    try:
        path = os.path.join(td, "pet_memory.json")

        # Create and populate
        pm1 = PetMemory(path=path)
        pm1.record_request("agent_a")
        pm1.record_request("agent_b", success=False)
        pm1.record_trust(0.75)
        pm1.start_session()
        pm1.end_session()

        # Reload
        pm2 = PetMemory(path=path)
        assert pm2.total_requests == 2, f"requests={pm2.total_requests}"
        assert pm2.total_errors == 1, f"errors={pm2.total_errors}"
        assert len(pm2.trust_history) == 1, f"history_len={len(pm2.trust_history)}"
        val = pm2.trust_history[0]["value"]
        assert val == 0.75, f"trust_val={val}"
        assert pm2.total_sessions == 1, f"sessions={pm2.total_sessions}"
        achs = pm2.achievements
        assert "first_request" in achs, f"achievements={achs}"
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)'''

if OLD in code:
    code = code.replace(OLD, NEW, 1)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(code)
    print("SUCCESS: persistence_roundtrip fixed")
else:
    print("ERROR: OLD text not found. Showing actual content around line 185:")
    lines = code.split("\n")
    for i in range(184, min(206, len(lines))):
        print(f"  {i+1}: {repr(lines[i])}")
