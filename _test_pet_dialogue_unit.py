"""Tests for PetDialogue system — personality layer of the desktop pet.

Verifies:
- All event types produce output
- Human and cyber modes generate different styles
- Cooldown mechanism works
- Trust-based subject selection
- Escalation on repeated events
- Deduplication
- Stats tracking
- Unknown event handling
- Time-of-day modifiers
"""
import sys
import time
sys.path.insert(0, r"e:\QODERCN\ai_agent_monitor")

from src.ui.pet_dialogue import PetDialogue

RESULTS = []

def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"  [PASS] {name}")
    except AssertionError as e:
        RESULTS.append((name, False, str(e)))
        print(f"  [FAIL] {name}: {e}")
    except Exception as e:
        RESULTS.append((name, False, f"EXCEPTION: {e}"))
        print(f"  [FAIL] {name}: {e}")


print("=" * 60)
print("  PetDialogue Unit Tests")
print("=" * 60)

# ── 1. Basic instantiation ──
def test_init():
    pd = PetDialogue()
    assert pd._total_events == 0
    assert pd._personality == "snarky"
check("init_default", test_init)

# ── 2. All event types produce output ──
def test_all_events():
    pd = PetDialogue()
    events = ["startup", "trust_drop", "repetition", "low_diversity",
              "circuit_break", "idle", "trust_recover", "healthy_streak",
              "rate_limit"]
    for e in events:
        # Fresh instance each time to avoid cooldown
        pd2 = PetDialogue()
        line = pd2.on_event(e, mode="human", trust=0.8)
        assert line is not None, f"Event '{e}' returned None"
        assert len(line) > 0, f"Event '{e}' returned empty string"
check("all_events_human", test_all_events)

def test_all_events_cyber():
    events = ["startup", "trust_drop", "repetition", "low_diversity",
              "circuit_break", "idle", "trust_recover", "healthy_streak",
              "rate_limit"]
    for e in events:
        pd = PetDialogue()
        line = pd.on_event(e, mode="cyber", trust=0.8)
        assert line is not None, f"Cyber event '{e}' returned None"
        assert len(line) > 0
check("all_events_cyber", test_all_events_cyber)

# ── 3. Human vs Cyber produce different styles ──
def test_mode_difference():
    # Run multiple times to account for randomness
    diffs = 0
    for _ in range(5):
        pd_h = PetDialogue()
        pd_c = PetDialogue()
        h = pd_h.on_event("trust_drop", mode="human", trust=0.5)
        c = pd_c.on_event("trust_drop", mode="cyber", trust=0.5)
        if h != c:
            diffs += 1
    assert diffs >= 3, f"Human/cyber should differ most times, got {diffs}/5"
check("mode_difference", test_mode_difference)

# ── 4. Cooldown mechanism ──
def test_cooldown():
    pd = PetDialogue()
    first = pd.on_event("trust_drop", mode="human", trust=0.5)
    assert first is not None, "First call should succeed"
    # Immediate second call should be on cooldown
    second = pd.on_event("trust_drop", mode="human", trust=0.5)
    assert second is None, "Second immediate call should return None (cooldown)"
check("cooldown", test_cooldown)

def test_different_events_no_cooldown():
    pd = PetDialogue()
    r1 = pd.on_event("trust_drop", mode="human", trust=0.5)
    r2 = pd.on_event("repetition", mode="human", trust=0.5)
    assert r1 is not None
    assert r2 is not None, "Different event types should not share cooldown"
check("different_events_independent", test_different_events_no_cooldown)

# ── 5. Unknown event type ──
def test_unknown_event():
    pd = PetDialogue()
    result = pd.on_event("nonexistent_event", mode="human", trust=0.8)
    assert result is None, "Unknown event should return None"
check("unknown_event", test_unknown_event)

# ── 6. Stats tracking ──
def test_stats():
    pd = PetDialogue()
    pd.on_event("startup", mode="human", trust=0.8)
    pd.on_event("idle", mode="human", trust=0.8)
    s = pd.stats
    assert s["total_events"] == 2, f"Expected 2 events, got {s['total_events']}"
    assert "startup" in s["event_counts"]
    assert "idle" in s["event_counts"]
    assert s["unique_lines"] >= 1
    assert s["uptime_s"] >= 0
check("stats_tracking", test_stats)

# ── 7. Trust-based subject selection ──
def test_subject_low_trust():
    pd = PetDialogue()
    # With very low trust, subject should always be "我"
    subjects = set()
    for _ in range(20):
        pd2 = PetDialogue()
        line = pd2.on_event("trust_drop", mode="human", trust=0.1)
        if line:
            # Check which subject was used
            for s in ["我", "本宠", "系统"]:
                if s in line:
                    subjects.add(s)
    # Low trust → only "我"
    assert subjects == {"我"}, f"Low trust should use '我' only, got {subjects}"
check("subject_low_trust", test_subject_low_trust)

# ── 8. Escalation on repeated events ──
def test_escalation():
    pd = PetDialogue()
    # Bypass cooldown by manipulating internal state
    lines = []
    for i in range(6):
        pd._cooldowns["trust_drop"] = 0  # Reset cooldown
        line = pd.on_event("trust_drop", mode="human", trust=0.5)
        if line:
            lines.append(line)
    # After 5+ events, escalation prefix should appear
    escalated = [l for l in lines if "第" in l and "次" in l]
    assert len(escalated) >= 1, f"Expected escalation after 5+ events, got: {lines}"
check("escalation", test_escalation)

# ── 9. Deduplication ──
def test_dedup():
    pd = PetDialogue()
    lines = []
    for _ in range(10):
        pd._cooldowns["idle"] = 0
        line = pd.on_event("idle", mode="human", trust=0.8)
        if line:
            lines.append(line)
    # Should have some variety (not all identical)
    unique = set(lines)
    assert len(unique) > 1, f"Dedup failed: all lines identical: {lines[0]}"
check("deduplication", test_dedup)

# ── 10. Config loading ──
def test_config_load():
    pd = PetDialogue()
    assert pd._config.get("personality") == "snarky"
    assert "events" in pd._config
    assert "trust_drop" in pd._config["events"]
    assert pd._config["events"]["trust_drop"]["cooldown_s"] == 10
check("config_loading", test_config_load)

# ── 11. Event count accumulation ──
def test_event_counts():
    pd = PetDialogue()
    for _ in range(3):
        pd._cooldowns["repetition"] = 0
        pd.on_event("repetition", mode="human", trust=0.5)
    assert pd._event_counts.get("repetition", 0) == 3
    assert pd._total_events == 3
check("event_counts", test_event_counts)

# ── 12. History bounded ──
def test_history_bounded():
    pd = PetDialogue()
    for i in range(100):
        pd._cooldowns["idle"] = 0
        pd.on_event("idle", mode="cyber", trust=0.8)
    assert len(pd._history) <= 50, f"History should be bounded to 50, got {len(pd._history)}"
check("history_bounded", test_history_bounded)

# ── Summary ──
print()
print("=" * 60)
passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = sum(1 for _, ok, _ in RESULTS if not ok)
print(f"  Results: {passed} passed, {failed} failed, {len(RESULTS)} total")
if failed:
    print("  FAILED tests:")
    for name, ok, msg in RESULTS:
        if not ok:
            print(f"    - {name}: {msg}")
print("=" * 60)
