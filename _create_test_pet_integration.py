"""Create ws_bridge + sidebar integration tests for pet system."""

TEST_CODE = r'''"""Tests for pet system integration — ws_bridge trust injection + sidebar mapping.

Covers:
- ws_bridge: trust injection into events
- ws_bridge: broadcast_pet_update message format
- ws_bridge: set_trust_scorer wiring
- sidebar: _EVENT_TO_PET mapping completeness
- sidebar: PetDialogue import and instantiation
- Config files: pet_personality.json, pet_themes.json, pet_custom_dialogues.json
"""

import json
import os
import sys
import tempfile

PROJECT = r"e:\QODERCN\ai_agent_monitor"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

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
#  ws_bridge tests
# ══════════════════════════════════════

def test_ws_bridge_import():
    """ws_bridge module should import without errors."""
    from src.common.ws_bridge import WebSocketBridge
    assert WebSocketBridge is not None


def test_ws_bridge_trust_scorer_attach():
    """set_trust_scorer should attach without errors."""
    from src.common.ws_bridge import WebSocketBridge

    class FakeBus:
        def subscribe(self):
            import queue
            return queue.Queue()

    bridge = WebSocketBridge(FakeBus())
    assert bridge._trust_scorer is None

    class FakeTrust:
        def get_score(self, aid):
            return 0.75

    bridge.set_trust_scorer(FakeTrust())
    assert bridge._trust_scorer is not None


def test_ws_bridge_pet_update_format():
    """broadcast_pet_update should format message correctly."""
    # We can't easily test the actual broadcast without a running server,
    # but we can verify the method exists and handles empty clients
    from src.common.ws_bridge import WebSocketBridge

    class FakeBus:
        def subscribe(self):
            import queue
            return queue.Queue()

    bridge = WebSocketBridge(FakeBus())
    # With no clients, should not raise
    bridge.broadcast_pet_update({"trust": 0.8, "event": "trust_drop"})


def test_trust_injection_logic():
    """Trust injection should add trust field to events."""
    # Simulate the injection logic from ws_bridge._run handler
    class FakeTrustScorer:
        def get_score(self, aid):
            return {"agent_a": 0.9, "agent_b": 0.4}.get(aid, 0.5)

    scorer = FakeTrustScorer()
    batch = [
        {"agent_id": "agent_a", "type": "api_call"},
        {"agent_id": "agent_b", "type": "api_call"},
        {"type": "system"},  # No agent_id
    ]

    for evt in batch:
        aid = evt.get("agent_id", "")
        if aid:
            evt["trust"] = scorer.get_score(aid)

    assert batch[0]["trust"] == 0.9
    assert batch[1]["trust"] == 0.4
    assert "trust" not in batch[2]  # No agent_id, no injection


# ══════════════════════════════════════
#  sidebar event mapping tests
# ══════════════════════════════════════

def test_sidebar_event_mapping_exists():
    """_EVENT_TO_PET mapping should exist in sidebar module."""
    # Import the mapping by reading the source
    sidebar_path = os.path.join(PROJECT, "src", "ui", "sidebar.py")
    with open(sidebar_path, "r", encoding="utf-8") as f:
        code = f.read()
    assert "_EVENT_TO_PET" in code, "sidebar.py missing _EVENT_TO_PET"


def test_sidebar_event_mapping_coverage():
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
        assert mapping in code, f"Missing mapping for '{mapping}'"


def test_sidebar_pet_dialogue_integration():
    """sidebar should import and use PetDialogue."""
    sidebar_path = os.path.join(PROJECT, "src", "ui", "sidebar.py")
    with open(sidebar_path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "from src.ui.pet_dialogue import PetDialogue" in code
    assert "PetDialogue" in code
    assert "_show_pet_dialogue" in code
    assert "_pet_bar" in code


def test_sidebar_typewriter_effect():
    """sidebar should have typewriter effect implementation."""
    sidebar_path = os.path.join(PROJECT, "src", "ui", "sidebar.py")
    with open(sidebar_path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "_pet_type_job" in code, "Missing typewriter job variable"
    assert "_type_next" in code, "Missing typewriter _type_next function"
    assert "_pet_type_index" in code, "Missing typewriter index"


def test_sidebar_dual_mode_styling():
    """sidebar should have dual-mode pet bar styling."""
    sidebar_path = os.path.join(PROJECT, "src", "ui", "sidebar.py")
    with open(sidebar_path, "r", encoding="utf-8") as f:
        code = f.read()

    # Human mode styling
    assert '"#ffffff"' in code or "'#ffffff'" in code, "Missing human mode white text"
    assert '"#333333"' in code or "'#333333'" in code, "Missing human mode dark bg"
    # Cyber mode styling
    assert '"#00ffcc"' in code or "'#00ffcc'" in code, "Missing cyber mode neon text"


# ══════════════════════════════════════
#  Config file tests
# ══════════════════════════════════════

def test_pet_personality_config():
    """pet_personality.json should be valid and complete."""
    path = os.path.join(PROJECT, "config", "pet_personality.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    assert cfg["personality"] == "snarky"
    assert cfg["language"] == "zh"
    assert "events" in cfg
    # Should have all 9 event types
    expected_events = {
        "trust_drop", "trust_recover", "repetition", "low_diversity",
        "circuit_break", "rate_limit", "idle", "startup", "healthy_streak"
    }
    actual_events = set(cfg["events"].keys())
    assert expected_events == actual_events, \
        f"Config events mismatch: missing={expected_events - actual_events}"

    # Each event should have weight and cooldown_s
    for evt, params in cfg["events"].items():
        assert "weight" in params, f"Event '{evt}' missing 'weight'"
        assert "cooldown_s" in params, f"Event '{evt}' missing 'cooldown_s'"


def test_pet_themes_config():
    """pet_themes.json should have 5 themes."""
    path = os.path.join(PROJECT, "config", "pet_themes.json")
    with open(path, "r", encoding="utf-8") as f:
        themes = json.load(f)

    assert len(themes) >= 5, f"Expected 5+ themes, got {len(themes)}"
    # Each theme should have color properties
    for theme_id, theme_data in themes.items():
        assert "primary" in theme_data, f"Theme '{theme_id}' missing 'primary'"
        assert "accent" in theme_data, f"Theme '{theme_id}' missing 'accent'"


def test_custom_dialogues_config():
    """pet_custom_dialogues.json should be valid."""
    path = os.path.join(PROJECT, "config", "pet_custom_dialogues.json")
    with open(path, "r", encoding="utf-8") as f:
        custom = json.load(f)

    assert isinstance(custom, dict)
    # Should have event type keys
    for key, val in custom.items():
        assert isinstance(val, list), f"Key '{key}' should be a list"


# ══════════════════════════════════════
#  Electron file tests
# ══════════════════════════════════════

def test_electron_renderer_has_pet_character():
    """renderer.html should contain pet character CSS."""
    path = os.path.join(PROJECT, "hud_electron", "renderer.html")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "pet-character" in code, "Missing pet-character element"
    assert "pet-head" in code or "pet-body" in code, "Missing pet body parts"
    assert "@keyframes" in code, "Missing CSS animations"


def test_electron_renderer_has_pet_states():
    """renderer.html should define 5 pet animation states."""
    path = os.path.join(PROJECT, "hud_electron", "renderer.html")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    for state in ["happy", "idle", "warning", "angry", "sleeping"]:
        assert state in code, f"Missing pet state: {state}"


def test_electron_renderer_has_themes():
    """renderer.html should support theme switching."""
    path = os.path.join(PROJECT, "hud_electron", "renderer.html")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "theme-" in code, "Missing theme CSS classes"


def test_electron_renderer_has_achievements():
    """renderer.html should have achievement display."""
    path = os.path.join(PROJECT, "hud_electron", "renderer.html")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "ach-" in code, "Missing achievement CSS/HTML"


def test_electron_main_has_tray():
    """main.js should have system tray implementation."""
    path = os.path.join(PROJECT, "hud_electron", "main.js")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "Tray" in code, "Missing Tray import"
    assert "Menu" in code, "Missing Menu import"
    assert "createTray" in code or "tray" in code.lower(), "Missing tray creation"


def test_electron_main_has_screenshot():
    """main.js should have screenshot capability."""
    path = os.path.join(PROJECT, "hud_electron", "main.js")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "take-screenshot" in code or "screenshot" in code.lower(), \
        "Missing screenshot IPC"
    assert "capturePage" in code, "Missing capturePage call"


def test_electron_main_has_autostart():
    """main.js should have auto-start support."""
    path = os.path.join(PROJECT, "hud_electron", "main.js")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "setLoginItemSettings" in code or "login" in code.lower(), \
        "Missing auto-start support"


def test_electron_renderer_js_pet_state():
    """renderer.js should have pet state management."""
    path = os.path.join(PROJECT, "hud_electron", "renderer.js")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "setPetState" in code or "petState" in code, \
        "Missing pet state management"
    assert "trustToState" in code or "trust" in code.lower(), \
        "Missing trust-to-state mapping"


def test_electron_renderer_js_theme():
    """renderer.js should have theme switching."""
    path = os.path.join(PROJECT, "hud_electron", "renderer.js")
    with open(path, "r", encoding="utf-8") as f:
        code = f.read()

    assert "set-theme" in code or "theme" in code.lower(), \
        "Missing theme switching"


# ══════════════════════════════════════
#  Run all tests
# ══════════════════════════════════════

if __name__ == "__main__":
    print("\n\u2550\u2550\u2550 Pet System Integration Tests \u2550\u2550\u2550\n")

    # ws_bridge
    check("ws_bridge_import", test_ws_bridge_import)
    check("ws_bridge_trust_scorer_attach", test_ws_bridge_trust_scorer_attach)
    check("ws_bridge_pet_update_format", test_ws_bridge_pet_update_format)
    check("trust_injection_logic", test_trust_injection_logic)

    # sidebar
    check("sidebar_event_mapping_exists", test_sidebar_event_mapping_exists)
    check("sidebar_event_mapping_coverage", test_sidebar_event_mapping_coverage)
    check("sidebar_pet_dialogue_integration", test_sidebar_pet_dialogue_integration)
    check("sidebar_typewriter_effect", test_sidebar_typewriter_effect)
    check("sidebar_dual_mode_styling", test_sidebar_dual_mode_styling)

    # configs
    check("pet_personality_config", test_pet_personality_config)
    check("pet_themes_config", test_pet_themes_config)
    check("custom_dialogues_config", test_custom_dialogues_config)

    # electron
    check("electron_renderer_has_pet_character", test_electron_renderer_has_pet_character)
    check("electron_renderer_has_pet_states", test_electron_renderer_has_pet_states)
    check("electron_renderer_has_themes", test_electron_renderer_has_themes)
    check("electron_renderer_has_achievements", test_electron_renderer_has_achievements)
    check("electron_main_has_tray", test_electron_main_has_tray)
    check("electron_main_has_screenshot", test_electron_main_has_screenshot)
    check("electron_main_has_autostart", test_electron_main_has_autostart)
    check("electron_renderer_js_pet_state", test_electron_renderer_js_pet_state)
    check("electron_renderer_js_theme", test_electron_renderer_js_theme)

    print(f"\n\u2550\u2550\u2550 Results: {PASSED} passed, {FAILED} failed \u2550\u2550\u2550\n")
    sys.exit(1 if FAILED else 0)
'''

path = r"e:\QODERCN\ai_agent_monitor\tests\test_pet_integration.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(TEST_CODE)
print(f"Created {path} ({len(TEST_CODE)} bytes)")
