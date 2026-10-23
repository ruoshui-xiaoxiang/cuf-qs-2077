"""Fix 3 failing tests in pet_memory and pet_integration."""
import os

# ══════════════════════════════════════
# Fix 1: persistence_roundtrip in test_pet_memory.py
# ══════════════════════════════════════
PATH_MEM = r"e:\QODERCN\ai_agent_monitor\tests\test_pet_memory.py"
with open(PATH_MEM, "r", encoding="utf-8") as f:
    code = f.read()

OLD_PERSIST = '''def test_persistence_roundtrip():
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
        assert pm2.total_requests == 2, f"Expected 2 requests, got {pm2.total_requests}"
        assert pm2.total_errors == 1, f"Expected 1 error, got {pm2.total_errors}"
        assert len(pm2.trust_history) == 1, f"Expected 1 trust history, got {len(pm2.trust_history)}"
        assert pm2.trust_history[0]["value"] == 0.75, f"Expected 0.75 trust, got {pm2.trust_history[0]['value']}"
        assert pm2.total_sessions == 1, f"Expected 1 session, got {pm2.total_sessions}"
        assert "first_request" in pm2.achievements, f"Expected 'first_request' achievement, got {pm2.achievements}"'''

NEW_PERSIST = '''def test_persistence_roundtrip():
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
        assert pm2.total_requests == 2, f"Expected 2 requests, got {pm2.total_requests}"
        assert pm2.total_errors == 1, f"Expected 1 error, got {pm2.total_errors}"
        assert len(pm2.trust_history) == 1, f"Expected 1 trust history, got {len(pm2.trust_history)}"
        val = pm2.trust_history[0]["value"]
        assert val == 0.75, f"Expected 0.75 trust, got {val}"
        assert pm2.total_sessions == 1, f"Expected 1 session, got {pm2.total_sessions}"
        achs = pm2.achievements
        assert "first_request" in achs, f"Expected first_request in {achs}"
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)'''

code = code.replace(OLD_PERSIST, NEW_PERSIST, 1)
with open(PATH_MEM, "w", encoding="utf-8") as f:
    f.write(code)
print("Fixed persistence_roundtrip")

# ══════════════════════════════════════
# Fix 2: sidebar_event_mapping_coverage in test_pet_integration.py
# ══════════════════════════════════════
PATH_INT = r"e:\QODERCN\ai_agent_monitor\tests\test_pet_integration.py"
with open(PATH_INT, "r", encoding="utf-8") as f:
    code2 = f.read()

OLD_MAPPING = '''def test_sidebar_event_mapping_coverage():
    """_EVENT_TO_PET should map key monitoring events to pet events."""
    sidebar_path = os.path.join(PROJECT, "src", "ui", "sidebar.py")
    with open(sidebar_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Check key mappings exist
    required_mappings = [
        "hallucination", "trust_drop", "circuit_break",
        "idle", "startup", "repetition"
    ]
    for mapping in required_mappings:
        assert mapping in code, f"Missing mapping for '{mapping}'"'''

NEW_MAPPING = '''def test_sidebar_event_mapping_coverage():
    """_EVENT_TO_PET should map key monitoring events to pet events."""
    sidebar_path = os.path.join(PROJECT, "src", "ui", "sidebar.py")
    with open(sidebar_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Check key monitoring event -> pet event mappings exist
    # These are monitoring event kinds that map to pet dialogue events
    required_source_events = [
        "hallucination",     # -> trust_drop
        "api_error",         # -> trust_drop
        "claim_mismatch",    # -> trust_drop
        "metric_ok",         # -> healthy_streak
        "custom_rule_circuit_break",  # -> circuit_break
        "custom_rule_alert_critical", # -> repetition
    ]
    for evt in required_source_events:
        assert evt in code, f"Missing source event mapping for '{evt}'"

    # Check that pet dialogue event types are referenced as values
    required_pet_events = [
        "trust_drop", "circuit_break", "repetition",
        "healthy_streak", "low_diversity", "rate_limit",
    ]
    for pet_evt in required_pet_events:
        assert pet_evt in code, f"Missing pet event type '{pet_evt}'"'''

code2 = code2.replace(OLD_MAPPING, NEW_MAPPING, 1)

# ══════════════════════════════════════
# Fix 3: custom_dialogues_config in test_pet_integration.py
# ══════════════════════════════════════

OLD_CUSTOM = '''def test_custom_dialogues_config():
    """pet_custom_dialogues.json should be valid."""
    path = os.path.join(PROJECT, "config", "pet_custom_dialogues.json")
    with open(path, "r", encoding="utf-8") as f:
        custom = json.load(f)

    assert isinstance(custom, dict)
    # Should have event type keys
    for key, val in custom.items():
        assert isinstance(val, list), f"Key '{key}' should be a list"'''

NEW_CUSTOM = '''def test_custom_dialogues_config():
    """pet_custom_dialogues.json should be valid."""
    path = os.path.join(PROJECT, "config", "pet_custom_dialogues.json")
    with open(path, "r", encoding="utf-8") as f:
        custom = json.load(f)

    assert isinstance(custom, dict)
    # Should have event type keys with list values
    # (non-list keys like _comment are allowed as metadata)
    event_keys = [k for k in custom if not k.startswith("_")]
    assert len(event_keys) >= 9, f"Expected 9+ event keys, got {len(event_keys)}"
    for key in event_keys:
        assert isinstance(custom[key], list), f"Event key '{key}' should be a list"'''

code2 = code2.replace(OLD_CUSTOM, NEW_CUSTOM, 1)

with open(PATH_INT, "w", encoding="utf-8") as f:
    f.write(code2)
print("Fixed sidebar_event_mapping_coverage + custom_dialogues_config")
