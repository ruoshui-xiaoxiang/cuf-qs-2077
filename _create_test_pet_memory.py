"""Create pet memory unit tests."""

TEST_CODE = r'''"""Tests for pet_memory.py — persistent state storage.

Covers:
- Initialization: fresh start and load from disk
- Request tracking: counts, agent tracking, favorite agent
- Trust history: recording, rolling window, peak/lowest
- Circuit break tracking
- Session management: start/end, long session achievement
- Achievements: automatic unlock based on conditions
- Persistence: save/load round-trip
- Stats property
- Edge cases: missing file, corrupt file, empty state
"""

import json
import os
import sys
import tempfile
import time

PROJECT = r"e:\QODERCN\ai_agent_monitor"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from src.common.pet_memory import PetMemory, ACHIEVEMENTS

PASSED = 0
FAILED = 0

def check(name, fn):
    global PASSED, FAILED
    try:
        fn()
        PASSED += 1
        print(f"  \u2713 {name}")
    except Exception as e:
        FAILED += 1
        print(f"  \u2717 {name}: {e}")


# ══════════════════════════════════════
#  Test functions
# ══════════════════════════════════════

def test_fresh_start():
    """New PetMemory with no file should start from zero."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        assert pm.total_requests == 0
        assert pm.total_errors == 0
        assert pm.total_sessions == 0
        assert pm.achievements == []
        assert pm.trust_history == []


def test_record_request():
    """Recording requests should increment counters."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        pm.record_request("agent_a", success=True)
        assert pm.total_requests == 1
        assert pm.total_errors == 0

        pm.record_request("agent_b", success=False)
        assert pm.total_requests == 2
        assert pm.total_errors == 1


def test_agent_tracking():
    """Should track per-agent counts and favorite agent."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        for _ in range(5):
            pm.record_request("agent_a")
        for _ in range(3):
            pm.record_request("agent_b")

        stats = pm.stats
        assert stats["favorite_agent"] == "agent_a"


def test_trust_history():
    """Trust history should record samples and track peak/lowest."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        pm.record_trust(0.8)
        pm.record_trust(0.3)
        pm.record_trust(0.9)

        history = pm.trust_history
        assert len(history) == 3
        assert history[0]["value"] == 0.8
        assert history[1]["value"] == 0.3
        assert history[2]["value"] == 0.9

        stats = pm.stats
        assert stats["peak_trust"] == 0.9
        assert stats["lowest_trust"] == 0.3


def test_trust_history_rolling_window():
    """Trust history should keep only last 200 entries."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        for i in range(250):
            pm.record_trust(0.5 + (i % 10) * 0.05)

        history = pm.trust_history
        assert len(history) == 200, f"Expected 200, got {len(history)}"


def test_circuit_break_tracking():
    """Circuit break should increment counter and check achievements."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        new_ach = pm.record_circuit_break()
        assert pm.stats["total_circuit_breaks"] == 1
        assert "first_circuit_break" in new_ach


def test_session_management():
    """Start/end session should track session count."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        pm.start_session()
        assert pm.total_sessions == 1
        pm.end_session()

        pm.start_session()
        assert pm.total_sessions == 2
        pm.end_session()


def test_achievement_first_request():
    """First request should unlock 'first_request' achievement."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        new_ach = pm.record_request("agent_a")
        assert "first_request" in new_ach
        assert "first_request" in pm.achievements


def test_achievement_ten_requests():
    """10 requests should unlock 'ten_requests' achievement."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        all_new = []
        for i in range(10):
            new = pm.record_request(f"agent_{i % 3}")
            all_new.extend(new)
        assert "ten_requests" in all_new


def test_achievement_hundred_requests():
    """100 requests should unlock 'hundred_requests'."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        all_new = []
        for i in range(100):
            new = pm.record_request(f"agent_{i % 5}")
            all_new.extend(new)
        assert "hundred_requests" in all_new


def test_achievement_hallucination():
    """First error should unlock 'first_hallucination'."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        pm.record_request("agent_a", success=True)
        new_ach = pm.record_request("agent_a", success=False)
        assert "first_hallucination" in new_ach


def test_persistence_roundtrip():
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
        assert "first_request" in pm2.achievements


def test_stats_property():
    """Stats should return complete summary dict."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        pm.record_request("agent_a")
        pm.record_trust(0.8)

        stats = pm.stats
        assert "total_requests" in stats
        assert "total_errors" in stats
        assert "total_sessions" in stats
        assert "total_circuit_breaks" in stats
        assert "peak_trust" in stats
        assert "lowest_trust" in stats
        assert "achievements" in stats
        assert "favorite_agent" in stats
        assert "created_at" in stats
        assert stats["total_requests"] == 1
        assert stats["peak_trust"] == 0.8


def test_achievements_dict():
    """ACHIEVEMENTS dict should have 12 entries."""
    assert len(ACHIEVEMENTS) == 12, f"Expected 12 achievements, got {len(ACHIEVEMENTS)}"
    for ach_id, info in ACHIEVEMENTS.items():
        assert "name" in info, f"Achievement '{ach_id}' missing 'name'"
        assert "desc" in info, f"Achievement '{ach_id}' missing 'desc'"


def test_corrupt_file_fallback():
    """Corrupt JSON file should not crash, falls back to defaults."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        with open(path, "w") as f:
            f.write("{invalid json!!!")

        pm = PetMemory(path=path)
        assert pm.total_requests == 0
        # Should still work
        pm.record_request("agent_a")
        assert pm.total_requests == 1


def test_no_duplicate_achievements():
    """Recording same achievement condition twice should not duplicate."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "pet_memory.json")
        pm = PetMemory(path=path)
        new1 = pm.record_request("agent_a")
        assert "first_request" in new1

        new2 = pm.record_request("agent_b")
        # first_request already unlocked, should not appear again
        assert "first_request" not in new2

        # Achievement list should have no duplicates
        ach_list = pm.achievements
        assert len(ach_list) == len(set(ach_list)), "Duplicate achievements found"


# ══════════════════════════════════════
#  Run all tests
# ══════════════════════════════════════

if __name__ == "__main__":
    print("\n\u2550\u2550\u2550 Pet Memory Unit Tests \u2550\u2550\u2550\n")

    check("fresh_start", test_fresh_start)
    check("record_request", test_record_request)
    check("agent_tracking", test_agent_tracking)
    check("trust_history", test_trust_history)
    check("trust_history_rolling_window", test_trust_history_rolling_window)
    check("circuit_break_tracking", test_circuit_break_tracking)
    check("session_management", test_session_management)
    check("achievement_first_request", test_achievement_first_request)
    check("achievement_ten_requests", test_achievement_ten_requests)
    check("achievement_hundred_requests", test_achievement_hundred_requests)
    check("achievement_hallucination", test_achievement_hallucination)
    check("persistence_roundtrip", test_persistence_roundtrip)
    check("stats_property", test_stats_property)
    check("achievements_dict", test_achievements_dict)
    check("corrupt_file_fallback", test_corrupt_file_fallback)
    check("no_duplicate_achievements", test_no_duplicate_achievements)

    print(f"\n\u2550\u2550\u2550 Results: {PASSED} passed, {FAILED} failed \u2550\u2550\u2550\n")
    sys.exit(1 if FAILED else 0)
'''

path = r"e:\QODERCN\ai_agent_monitor\tests\test_pet_memory.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(TEST_CODE)
print(f"Created {path} ({len(TEST_CODE)} bytes)")
