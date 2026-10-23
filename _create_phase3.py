"""Create Phase 3 features: themes, custom dialogues, screenshot, achievements UI."""
import json
import os

BASE = r"e:\QODERCN\ai_agent_monitor"

# ══════════════════════════════════════════
# 1. config/pet_themes.json — skin/theme system
# ══════════════════════════════════════════
themes = {
    "default": {
        "name": "赛博绿",
        "primary": "#00ff88",
        "primary_dim": "#00aa55",
        "primary_dark": "#004422",
        "accent": "#ffcc00",
        "danger": "#ff4466",
        "bg_solid": "#0a0a0a",
        "pet_body": "#1a1a2e",
        "pet_border": "#00aa55"
    },
    "amber": {
        "name": "琥珀金",
        "primary": "#ffaa00",
        "primary_dim": "#cc8800",
        "primary_dark": "#664400",
        "accent": "#ff6600",
        "danger": "#ff2200",
        "bg_solid": "#0a0800",
        "pet_body": "#2e2a1a",
        "pet_border": "#cc8800"
    },
    "ice": {
        "name": "冰蓝",
        "primary": "#00ccff",
        "primary_dim": "#0088cc",
        "primary_dark": "#004466",
        "accent": "#00ffcc",
        "danger": "#ff4466",
        "bg_solid": "#000a0f",
        "pet_body": "#1a2a3e",
        "pet_border": "#0088cc"
    },
    "pink": {
        "name": "樱花粉",
        "primary": "#ff88cc",
        "primary_dim": "#cc5599",
        "primary_dark": "#662244",
        "accent": "#ffcc00",
        "danger": "#ff4466",
        "bg_solid": "#0a0008",
        "pet_body": "#2e1a2a",
        "pet_border": "#cc5599"
    },
    "mono": {
        "name": "纯白",
        "primary": "#cccccc",
        "primary_dim": "#888888",
        "primary_dark": "#444444",
        "accent": "#ffffff",
        "danger": "#ff4466",
        "bg_solid": "#0a0a0a",
        "pet_body": "#1a1a1a",
        "pet_border": "#888888"
    }
}

themes_path = os.path.join(BASE, "config", "pet_themes.json")
with open(themes_path, "w", encoding="utf-8") as f:
    json.dump(themes, f, indent=2, ensure_ascii=False)
print(f"Created pet_themes.json ({len(themes)} themes)")

# ══════════════════════════════════════════
# 2. config/pet_custom_dialogues.json — user dialogue templates
# ══════════════════════════════════════════
custom_tpl = {
    "_comment": "在此添加自定义台词。每个事件类型是一个列表，格式同 pet_dialogue.py 模板。{subj} 会被替换为主语。",
    "trust_drop": [],
    "trust_recover": [],
    "repetition": [],
    "low_diversity": [],
    "circuit_break": [],
    "rate_limit": [],
    "idle": [],
    "startup": [],
    "healthy_streak": []
}

custom_path = os.path.join(BASE, "config", "pet_custom_dialogues.json")
with open(custom_path, "w", encoding="utf-8") as f:
    json.dump(custom_tpl, f, indent=2, ensure_ascii=False)
print("Created pet_custom_dialogues.json")

# ══════════════════════════════════════════
# 3. Patch pet_dialogue.py — load custom templates
# ══════════════════════════════════════════
DIALOGUE_PATH = os.path.join(BASE, "src", "ui", "pet_dialogue.py")
with open(DIALOGUE_PATH, "r", encoding="utf-8") as f:
    dcode = f.read()

# Add custom template loading after _TEMPLATE_MAP definition
CUSTOM_LOAD = r"""

# ── Load user custom dialogue templates ──
def _load_custom_templates():
    \"\"\"Load user-defined custom dialogue templates from config.\"\"\"
    custom_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "config", "pet_custom_dialogues.json"
    )
    try:
        with open(custom_path, "r", encoding="utf-8") as f:
            custom = json.load(f)
        for event_type, lines in custom.items():
            if event_type in _TEMPLATE_MAP and isinstance(lines, list) and lines:
                human_tpl, cyber_tpl = _TEMPLATE_MAP[event_type]
                # Custom lines go into the human template pool
                _TEMPLATE_MAP[event_type] = (
                    list(human_tpl) + lines, cyber_tpl
                )
        log.info("Loaded custom dialogue templates")
    except Exception:
        pass  # No custom templates, use defaults

_load_custom_templates()
"""

dcode = dcode.replace(
    "\nclass PetDialogue:",
    CUSTOM_LOAD + "\nclass PetDialogue:",
    1
)

with open(DIALOGUE_PATH, "w", encoding="utf-8") as f:
    f.write(dcode)
print("pet_dialogue.py patched: +custom template loading")

print("\nPhase 3 core files created.")
