"""Create pet dialogue unit tests."""
import os

TEST_CODE = r'''"""Tests for pet_dialogue.py — the personality layer.

Covers:
- Template coverage: every event type produces output
- Dual mode: human vs cyber produce different styles
- Context awareness: trust level affects subject selection
- Deduplication: same line not repeated within window
- Cooldown: rapid same-event calls are suppressed
- Escalation: repeated events get escalation prefix
- Stats: event counts and uptime tracked
- Custom templates: user templates merge into pool
- Unknown events: gracefully return None
"""

import json
import os
import sys
import tempfile
import time

# Ensure project root is on path
PROJECT = r"e:\QODERCN\ai_agent_monitor"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from src.ui.pet_dialogue import PetDialogue, _TEMPLATE_MAP, _SUBJECTS

# ── Helpers ──

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

def test_all_event_types_produce_output():
    """Every registered event type should produce a non-None line."""
    for evt in _TEMPLATE_MAP:
        pd = PetDialogue()
        line = pd.on_event(evt, mode="human", trust=0.5)
        assert line is not None, f"Event '{evt}' produced None"
        assert len(line) > 0, f"Event '{evt}' produced empty string"


def test_unknown_event_returns_none():
    """Unknown event types should return None."""
    pd = PetDialogue()
    result = pd.on_event("nonexistent_event", mode="human", trust=0.5)
    assert result is None, f"Expected None for unknown event, got: {result}"


def test_human_mode_output():
    """Human mode should produce plain Chinese text."""
    pd = PetDialogue()
    line = pd.on_event("trust_drop", mode="human", trust=0.5)
    assert line is not None
    assert isinstance(line, str)
    assert len(line) > 2


def test_cyber_mode_output():
    """Cyber mode should produce technical-style text."""
    pd = PetDialogue()
    line = pd.on_event("startup", mode="cyber", trust=0.8)
    assert line is not None
    assert isinstance(line, str)


def test_mode_difference():
    """Human and cyber modes should usually produce different output."""
    diffs = 0
    for _ in range(5):
        pd_h = PetDialogue()
        pd_c = PetDialogue()
        h = pd_h.on_event("trust_drop", mode="human", trust=0.5)
        c = pd_c.on_event("trust_drop", mode="cyber", trust=0.5)
        if h != c:
            diffs += 1
    assert diffs >= 3, f"Human/cyber should differ most times, got {diffs}/5"


def test_cooldown_suppresses_rapid_calls():
    """Same event type within cooldown should return None."""
    cfg = {
        "events": {
            "idle": {"weight": 0.3, "cooldown_s": 60}
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        cfg_path = f.name

    try:
        pd = PetDialogue(config_path=cfg_path)
        first = pd.on_event("idle", mode="human", trust=0.5)
        second = pd.on_event("idle", mode="human", trust=0.5)
        assert first is not None, "First call should succeed"
        assert second is None, "Second call within cooldown should be None"
    finally:
        os.unlink(cfg_path)


def test_deduplication():
    """Same line should not appear twice in recent history."""
    pd = PetDialogue()
    lines = []
    for i in range(20):
        pd._cooldowns.clear()
        line = pd.on_event("idle", mode="human", trust=0.5)
        if line:
            lines.append(line)
    for i in range(1, len(lines)):
        assert lines[i] != lines[i-1], f"Consecutive duplicate: {lines[i]}"


def test_escalation_after_repeats():
    """After 5+ same events, line should have escalation prefix."""
    pd = PetDialogue()
    line = None
    for i in range(6):
        pd._cooldowns.clear()
        line = pd.on_event("idle", mode="human", trust=0.5)
    assert line is not None
    assert "\u7b2c6\u6b21" in line, f"Expected escalation prefix, got: {line}"


def test_trust_affects_subject():
    """Low trust should prefer '\u6211' as subject."""
    pd = PetDialogue()
    subjects_seen = set()
    for _ in range(50):
        pd._cooldowns.clear()
        line = pd.on_event("trust_drop", mode="human", trust=0.1)
        if line:
            for subj in _SUBJECTS:
                if subj in line:
                    subjects_seen.add(subj)
    assert "\u6211" in subjects_seen, "Low trust should include '\u6211' as subject"


def test_stats_tracking():
    """Stats should track event counts and uptime."""
    pd = PetDialogue()
    pd.on_event("startup", mode="human", trust=0.8)
    pd._cooldowns.clear()
    pd.on_event("idle", mode="human", trust=0.5)
    pd._cooldowns.clear()
    pd.on_event("idle", mode="human", trust=0.5)

    stats = pd.stats
    assert stats["total_events"] == 3, f"Expected 3 events, got {stats['total_events']}"
    assert stats["event_counts"].get("idle") == 2, f"Expected 2 idle events"
    assert stats["event_counts"].get("startup") == 1, f"Expected 1 startup event"
    assert stats["uptime_s"] >= 0, "Uptime should be non-negative"
    assert isinstance(stats["unique_lines"], int)


def test_config_loading():
    """PetDialogue should load personality config."""
    cfg = {
        "personality": "gentle",
        "language": "zh",
        "events": {
            "startup": {"weight": 1.0, "cooldown_s": 0}
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        cfg_path = f.name

    try:
        pd = PetDialogue(config_path=cfg_path)
        assert pd._personality == "gentle"
    finally:
        os.unlink(cfg_path)


def test_missing_config_fallback():
    """Missing config file should not crash, uses defaults."""
    pd = PetDialogue(config_path="/nonexistent/path.json")
    assert pd._personality == "snarky"
    line = pd.on_event("startup", mode="human", trust=0.8)
    assert line is not None


def test_all_event_types_both_modes():
    """Every event type should work in both human and cyber modes."""
    for evt in _TEMPLATE_MAP:
        for mode in ("human", "cyber"):
            pd = PetDialogue()
            line = pd.on_event(evt, mode=mode, trust=0.5)
            assert line is not None, f"Event '{evt}' mode '{mode}' produced None"
            assert isinstance(line, str) and len(line) > 0


def test_template_map_completeness():
    """Template map should have all 9 event types."""
    expected = {
        "trust_drop", "trust_recover", "repetition", "low_diversity",
        "circuit_break", "rate_limit", "idle", "startup", "healthy_streak"
    }
    actual = set(_TEMPLATE_MAP.keys())
    assert expected == actual, f"Missing={expected - actual}, extra={actual - expected}"


def test_custom_template_loading():
    """Custom templates should merge into human template pool."""
    custom = {
        "trust_drop": ["{subj}\u81ea\u5b9a\u4e49\u53f0\u8bcd\u6d4b\u8bd5"],
        "idle": ["{subj}\u5728\u81ea\u5b9a\u4e49\u7a7a\u95f2\u72b6\u6001"]
    }
    custom_path = os.path.join(
        PROJECT, "config", "pet_custom_dialogues.json"
    )
    original = None
    if os.path.exists(custom_path):
        with open(custom_path, "r", encoding="utf-8") as f:
            original = f.read()

    try:
        with open(custom_path, "w", encoding="utf-8") as f:
            json.dump(custom, f, ensure_ascii=False)

        import importlib
        import src.ui.pet_dialogue as pd_mod
        importlib.reload(pd_mod)

        human_tpl, cyber_tpl = pd_mod._TEMPLATE_MAP["trust_drop"]
        has_custom = any("\u81ea\u5b9a\u4e49\u53f0\u8bcd\u6d4b\u8bd5" in t for t in human_tpl)
        assert has_custom, "Custom template not found in human pool"
    finally:
        if original:
            with open(custom_path, "w", encoding="utf-8") as f:
                f.write(original)
        else:
            default = {evt: [] for evt in [
                "trust_drop", "trust_recover", "repetition", "low_diversity",
                "circuit_break", "rate_limit", "idle", "startup", "healthy_streak"
            ]}
            with open(custom_path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False)
        importlib.reload(pd_mod)


# ══════════════════════════════════════
#  Run all tests
# ══════════════════════════════════════

if __name__ == "__main__":
    print("\n\u2550\u2550\u2550 Pet Dialogue Unit Tests \u2550\u2550\u2550\n")

    check("all_event_types_produce_output", test_all_event_types_produce_output)
    check("unknown_event_returns_none", test_unknown_event_returns_none)
    check("human_mode_output", test_human_mode_output)
    check("cyber_mode_output", test_cyber_mode_output)
    check("mode_difference", test_mode_difference)
    check("cooldown_suppresses_rapid_calls", test_cooldown_suppresses_rapid_calls)
    check("deduplication", test_deduplication)
    check("escalation_after_repeats", test_escalation_after_repeats)
    check("trust_affects_subject", test_trust_affects_subject)
    check("stats_tracking", test_stats_tracking)
    check("config_loading", test_config_loading)
    check("missing_config_fallback", test_missing_config_fallback)
    check("all_event_types_both_modes", test_all_event_types_both_modes)
    check("template_map_completeness", test_template_map_completeness)
    check("custom_template_loading", test_custom_template_loading)

    print(f"\n\u2550\u2550\u2550 Results: {PASSED} passed, {FAILED} failed \u2550\u2550\u2550\n")
    sys.exit(1 if FAILED else 0)
'''

path = r"e:\QODERCN\ai_agent_monitor\tests\test_pet_dialogue.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(TEST_CODE)
print(f"Created {path} ({len(TEST_CODE)} bytes)")
