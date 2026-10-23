"""Patch renderer.html to add theme switcher + screenshot button + achievement display."""

PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\renderer.html"

with open(PATH, "r", encoding="utf-8") as f:
    html = f.read()

# ══════════════════════════════════════════
# 1. Add theme switcher CSS (CSS custom properties)
# ══════════════════════════════════════════
THEME_CSS = r"""
/* ═══════════════════════════════════════════
   Theme System — CSS custom properties
   ═══════════════════════════════════════════ */
body.theme-amber {
  --hud-green: #ffaa00;
  --hud-green-dim: #cc8800;
  --hud-green-dark: #664400;
  --hud-amber: #ff6600;
}
body.theme-ice {
  --hud-green: #00ccff;
  --hud-green-dim: #0088cc;
  --hud-green-dark: #004466;
  --hud-amber: #00ffcc;
}
body.theme-pink {
  --hud-green: #ff88cc;
  --hud-green-dim: #cc5599;
  --hud-green-dark: #662244;
  --hud-amber: #ffcc00;
}
body.theme-mono {
  --hud-green: #cccccc;
  --hud-green-dim: #888888;
  --hud-green-dark: #444444;
  --hud-amber: #ffffff;
}

/* ── Achievement badges ── */
.ach-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.ach-badge {
  background: rgba(0,255,136,0.08);
  border: 1px solid var(--hud-green-dark);
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 10px;
  color: var(--hud-green);
  text-shadow: 0 0 4px var(--hud-green);
}
.ach-badge.locked {
  opacity: 0.3;
  color: var(--hud-green-dark);
  border-color: var(--hud-green-dark);
  text-shadow: none;
}

/* ── Screenshot button ── */
#screenshot-btn {
  -webkit-app-region: no-drag;
  color: var(--hud-green-dim);
  font-size: 12px;
  cursor: pointer;
  padding: 2px 6px;
  opacity: 0.6;
  transition: opacity 0.2s;
  margin-left: 4px;
}
#screenshot-btn:hover {
  opacity: 1;
  text-shadow: 0 0 6px var(--hud-green);
}
"""

html = html.replace(
    ".pet-zzz {\n  position: absolute;",
    THEME_CSS + "\n.pet-zzz {\n  position: absolute;",
    1
)

# ══════════════════════════════════════════
# 2. Add screenshot button next to close button
# ══════════════════════════════════════════
html = html.replace(
    '<span id="close-btn">✕</span>',
    '<span id="close-btn">✕</span>\n  <span id="screenshot-btn" title="Screenshot">📷</span>',
    1
)

# ══════════════════════════════════════════
# 3. Add theme selector + achievement display in settings panel
# ══════════════════════════════════════════
SETTINGS_ADDITIONS = r"""
    <div class="setting-row">
      <span class="setting-label">Theme</span>
      <select class="setting-input" id="theme-select" style="width:100px">
        <option value="default">赛博绿</option>
        <option value="amber">琥珀金</option>
        <option value="ice">冰蓝</option>
        <option value="pink">樱花粉</option>
        <option value="mono">纯白</option>
      </select>
    </div>

    <div id="achievements-section" style="margin-top:12px">
      <h2 style="font-size:12px; margin-bottom:6px">🏆 ACHIEVEMENTS</h2>
      <div class="ach-grid" id="ach-grid"></div>
    </div>
"""

html = html.replace(
    '    <div id="settings-close-btn">[ CLOSE ]</div>',
    SETTINGS_ADDITIONS + '\n    <div id="settings-close-btn">[ CLOSE ]</div>',
    1
)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(html)

print("renderer.html patched: +theme system, +screenshot btn, +achievements UI")
