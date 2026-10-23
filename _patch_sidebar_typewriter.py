"""Patch sidebar.py to add typewriter effect + dual-mode pet bar styling."""

PATH = r"e:\QODERCN\ai_agent_monitor\src\ui\sidebar.py"
with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# ══════════════════════════════════════════
# 1. Replace _show_pet_dialogue with typewriter + dual-mode
# ══════════════════════════════════════════

OLD_SHOW = """    def _show_pet_dialogue(self, text: str) -> None:
        \"\"\"Display a pet dialogue line with auto-fade.\"\"\"
        if self._pet_bar is None or not text:
            return
        self._pet_bar.configure(text=f"💬 {text}")
        # Cancel previous fade job
        if self._pet_fade_job:
            self._root.after_cancel(self._pet_fade_job)
        # Auto-fade after 6 seconds
        self._pet_fade_job = self._root.after(
            6000, self._fade_pet_dialogue)"""

NEW_SHOW = """    def _show_pet_dialogue(self, text: str) -> None:
        \"\"\"Display a pet dialogue line with typewriter effect + auto-fade.\"\"\"
        if self._pet_bar is None or not text:
            return
        # Cancel previous jobs
        if self._pet_fade_job:
            self._root.after_cancel(self._pet_fade_job)
        if hasattr(self, '_pet_type_job') and self._pet_type_job:
            self._root.after_cancel(self._pet_type_job)

        # Dual-mode styling
        if self._mode == "human":
            self._pet_bar.configure(
                fg="#ffffff", bg="#333333",
                font=("Microsoft YaHei UI", 9),
                text="", relief="solid", bd=1)
        else:
            self._pet_bar.configure(
                fg="#00ffcc", bg=_HUD_BG if self._hud else T.HEADER_BG,
                font=("Consolas", 12 if self._hud else 9, "bold"),
                text="", relief="flat", bd=0)

        # Typewriter effect: reveal characters one by one
        full_text = f"\\U0001f4ac {text}"
        self._pet_type_index = 0
        self._pet_type_text = full_text

        def _type_next():
            if self._pet_type_index >= len(full_text):
                # Typing complete — schedule fade
                self._pet_fade_job = self._root.after(
                    6000, self._fade_pet_dialogue)
                return
            self._pet_type_index += 1
            if self._pet_bar:
                self._pet_bar.configure(
                    text=full_text[:self._pet_type_index])
            # Speed: 30ms per char for cyber, 50ms for human
            delay = 30 if self._mode != "human" else 50
            self._pet_type_job = self._root.after(delay, _type_next)

        self._pet_type_job = self._root.after(100, _type_next)"""

code = code.replace(OLD_SHOW, NEW_SHOW, 1)

# ══════════════════════════════════════════
# 2. Add _pet_type_job initialization in __init__
# ══════════════════════════════════════════

OLD_INIT = """        self._pet_fade_job: Optional[str] = None"""
NEW_INIT = """        self._pet_fade_job: Optional[str] = None
        self._pet_type_job: Optional[str] = None"""
code = code.replace(OLD_INIT, NEW_INIT, 1)

# ══════════════════════════════════════════
# 3. Update _fade_pet_dialogue to also cancel type job
# ══════════════════════════════════════════

OLD_FADE = """    def _fade_pet_dialogue(self) -> None:
        \"\"\"Fade out the pet dialogue bar.\"\"\"
        if self._pet_bar:
            self._pet_bar.configure(text="")
        self._pet_fade_job = None"""

NEW_FADE = """    def _fade_pet_dialogue(self) -> None:
        \"\"\"Fade out the pet dialogue bar.\"\"\"
        if self._pet_bar:
            self._pet_bar.configure(text="")
        self._pet_fade_job = None
        if hasattr(self, '_pet_type_job') and self._pet_type_job:
            self._root.after_cancel(self._pet_type_job)
            self._pet_type_job = None"""

code = code.replace(OLD_FADE, NEW_FADE, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

print("sidebar.py patched: +typewriter effect, +dual-mode pet bar styling")
