"""Patch main.py to integrate PetMemory + patch renderer.js for open-settings."""
import os

# ══════════════════════════════════════════
# 1. Patch main.py — integrate PetMemory
# ══════════════════════════════════════════

MAIN_PATH = r"e:\QODERCN\ai_agent_monitor\main.py"
with open(MAIN_PATH, "r", encoding="utf-8") as f:
    code = f.read()

# Add import
OLD_IMPORT = "from src.common.event_bus import EventBus"
NEW_IMPORT = """from src.common.event_bus import EventBus
from src.common.pet_memory import PetMemory"""
code = code.replace(OLD_IMPORT, NEW_IMPORT, 1)

# After runner creation, create PetMemory and start session
OLD_RUNNER = """    # ── Electron HUD mode ──
    if hud_electron:"""
NEW_RUNNER = """    # ── Pet memory (persistent state) ──
    pet_mem = PetMemory()
    pet_mem.start_session()

    # ── Electron HUD mode ──
    if hud_electron:"""
code = code.replace(OLD_RUNNER, NEW_RUNNER, 1)

# Also add end_session on cleanup
OLD_CLEANUP = "        restoreProxy()\n        stopPythonBackend()"
NEW_CLEANUP = "        pet_mem.end_session()\n        restoreProxy()\n        stopPythonBackend()"
code = code.replace(OLD_CLEANUP, NEW_CLEANUP, 1)

with open(MAIN_PATH, "w", encoding="utf-8") as f:
    f.write(code)
print("main.py patched: +PetMemory integration")

# ══════════════════════════════════════════
# 2. Patch renderer.js — add open-settings IPC handler
# ══════════════════════════════════════════

REN_PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\renderer.js"
with open(REN_PATH, "r", encoding="utf-8") as f:
    js = f.read()

SETTINGS_HANDLER = r"""
// IPC: open settings panel from tray menu
ipcRenderer.on('open-settings', () => {
  if (typeof toggleSettings === 'function') {
    toggleSettings();
  }
});

"""

# Insert before "// ── Start ──"
js = js.replace("// ── Start ──", SETTINGS_HANDLER + "// ── Start ──", 1)

with open(REN_PATH, "w", encoding="utf-8") as f:
    f.write(js)
print("renderer.js patched: +open-settings IPC handler")
