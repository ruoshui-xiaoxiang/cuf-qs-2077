"""Patch sidebar.py to integrate PetDialogue system."""
import re

SIDEBAR_PATH = r"e:\QODERCN\ai_agent_monitor\src\ui\sidebar.py"

with open(SIDEBAR_PATH, "r", encoding="utf-8") as f:
    code = f.read()

# ── 1. Add import ──
old_import = "from src.ui.cyber_header import CyberHeader"
new_import = """from src.ui.cyber_header import CyberHeader
from src.ui.pet_dialogue import PetDialogue"""
code = code.replace(old_import, new_import, 1)

# ── 2. Add event→pet mapping dict after _HUD_HEADER constant ──
pet_mapping = '''

# ── Event kind → pet dialogue event type mapping ──
_EVENT_TO_PET = {
    "api_error":        "trust_drop",
    "claim_mismatch":   "trust_drop",
    "hallucination":    "trust_drop",
    "metric_ok":        "healthy_streak",
    "tool_failure":     "trust_drop",
    "custom_rule_alert_warn":     "low_diversity",
    "custom_rule_alert_critical": "repetition",
    "custom_rule_rate_limit":     "rate_limit",
    "custom_rule_circuit_break":  "circuit_break",
    "discovery-complete":         "startup",
}
'''
old_after_header = 'class Sidebar:'
code = code.replace(old_after_header, pet_mapping + '\n\nclass Sidebar:', 1)

# ── 3. Add PetDialogue + PetDialogueBar to __init__ ──
old_init_end = '''        self._poll_interval = 100
        self._event_queue: Optional[queue.Queue] = None'''
new_init_end = '''        self._poll_interval = 100
        self._event_queue: Optional[queue.Queue] = None

        # ── Pet dialogue system ──
        self._pet = PetDialogue()
        self._pet_bar: Optional[tk.Label] = None
        self._pet_fade_job: Optional[str] = None'''
code = code.replace(old_init_end, new_init_end, 1)

# ── 4. Add pet bar creation at end of _setup_normal ──
old_normal_end = '''        root.bind("<Control-m>", self._toggle_mode)'''
new_normal_end = '''        root.bind("<Control-m>", self._toggle_mode)

        # ── Pet dialogue bar (bottom) ──
        self._create_pet_bar(root)'''
code = code.replace(old_normal_end, new_normal_end, 1)

# ── 5. Add pet bar creation at end of _setup_hud ──
old_hud_end = '''        # Close bindings
        root.bind("<Button-3>", lambda e: self._close_window())
        root.bind("<Escape>", lambda e: self._close_window())'''
new_hud_end = '''        # ── Pet dialogue bar (bottom of HUD) ──
        self._create_pet_bar(root)

        # Close bindings
        root.bind("<Button-3>", lambda e: self._close_window())
        root.bind("<Escape>", lambda e: self._close_window())'''
code = code.replace(old_hud_end, new_hud_end, 1)

# ── 6. Add pet bar methods + show_dialogue before _close_window ──
pet_methods = '''
    # ══════════════════════════════════════════════════
    #  Pet dialogue bar
    # ══════════════════════════════════════════════════

    def _create_pet_bar(self, root) -> None:
        """Create the pet dialogue display bar at the bottom."""
        if self._hud:
            bg, fg = _HUD_BG, "#00ffcc"
            font = ("Consolas", 12, "bold")
        else:
            bg, fg = T.HEADER_BG, "#88ccaa"
            font = ("Consolas", 9)

        self._pet_bar = tk.Label(
            root, text="", anchor="w",
            bg=bg, fg=fg, font=font,
            padx=10, pady=4,
            wraplength=440, justify="left",
        )
        self._pet_bar.pack(fill="x", side="bottom", padx=0, pady=0)

    def _show_pet_dialogue(self, text: str) -> None:
        """Display a pet dialogue line with auto-fade."""
        if self._pet_bar is None or not text:
            return
        self._pet_bar.configure(text=f"💬 {text}")
        # Cancel previous fade job
        if self._pet_fade_job:
            self._root.after_cancel(self._pet_fade_job)
        # Auto-fade after 6 seconds
        self._pet_fade_job = self._root.after(
            6000, self._fade_pet_dialogue)

    def _fade_pet_dialogue(self) -> None:
        """Fade out the pet dialogue bar."""
        if self._pet_bar:
            self._pet_bar.configure(text="")
        self._pet_fade_job = None

    def _process_pet_dialogue(self, event: Dict[str, Any]) -> None:
        """Map a monitoring event to a pet dialogue line."""
        kind = event.get("kind", "")
        pet_event = _EVENT_TO_PET.get(kind)
        if pet_event is None:
            return

        # Extract trust from context if available
        ctx = event.get("context", {})
        trust = 0.8  # default
        # Try to get trust from the trust scorer via collector
        if hasattr(self, '_trust_ref') and self._trust_ref:
            agent_id = event.get("agent_id", "default_agent")
            trust = self._trust_ref.get_score(agent_id)

        line = self._pet.on_event(
            pet_event, mode=self._mode, trust=trust)
        if line:
            self._show_pet_dialogue(line)

'''

old_close = '    # ── Close ──'
code = code.replace(old_close, pet_methods + '    # ── Close ──', 1)

# ── 7. Hook add_item to trigger pet dialogue ──
old_add_item_end = '''        self._text.see("end")

        if self._counter > 200:'''
new_add_item_end = '''        self._text.see("end")

        # ── Trigger pet dialogue ──
        self._process_pet_dialogue(event)

        if self._counter > 200:'''
code = code.replace(old_add_item_end, new_add_item_end, 1)

# ── 8. Add startup dialogue after setup ──
# In _setup_normal, after pet bar creation
old_toggle = '        root.bind("<Control-m>", self._toggle_mode)\n\n        # ── Pet dialogue bar (bottom) ──\n        self._create_pet_bar(root)'
new_toggle = '''        root.bind("<Control-m>", self._toggle_mode)

        # ── Pet dialogue bar (bottom) ──
        self._create_pet_bar(root)
        # Show startup dialogue
        self._root.after(1000, self._pet_startup_greeting)'''
code = code.replace(old_toggle, new_toggle, 1)

# Add the startup greeting method
old_pet_methods_end = '    def _process_pet_dialogue(self, event: Dict[str, Any]) -> None:'
new_pet_methods_end = '''    def _pet_startup_greeting(self) -> None:
        """Show startup greeting after a short delay."""
        line = self._pet.on_event("startup", mode=self._mode, trust=0.8)
        if line:
            self._show_pet_dialogue(line)

    def _process_pet_dialogue(self, event: Dict[str, Any]) -> None:'''
code = code.replace(old_pet_methods_end, new_pet_methods_end, 1)

# ── Write ──
with open(SIDEBAR_PATH, "w", encoding="utf-8") as f:
    f.write(code)

print(f"[OK] Patched {SIDEBAR_PATH}")
print(f"  Total lines: {len(code.splitlines())}")
