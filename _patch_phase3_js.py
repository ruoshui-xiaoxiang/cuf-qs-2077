"""Patch renderer.js + main.js for theme switching, screenshot, achievements."""

# ══════════════════════════════════════════
# 1. Patch renderer.js — theme + screenshot + achievements
# ══════════════════════════════════════════

REN_PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\renderer.js"
with open(REN_PATH, "r", encoding="utf-8") as f:
    js = f.read()

PHASE3_JS = r"""
// ── Theme Switcher ──
const themeSelect = document.getElementById('theme-select');
if (themeSelect) {
  themeSelect.addEventListener('change', (e) => {
    const theme = e.target.value;
    document.body.className = 'mode-' + currentMode;
    if (theme !== 'default') {
      document.body.classList.add('theme-' + theme);
    }
    ipcRenderer.send('set-theme', theme);
  });
}

// Restore saved theme
ipcRenderer.on('theme-restored', (e, theme) => {
  if (theme && theme !== 'default') {
    document.body.classList.add('theme-' + theme);
    if (themeSelect) themeSelect.value = theme;
  }
});

// ── Screenshot ──
const screenshotBtn = document.getElementById('screenshot-btn');
if (screenshotBtn) {
  screenshotBtn.addEventListener('click', async () => {
    const dataUrl = await ipcRenderer.invoke('take-screenshot');
    if (dataUrl) {
      showPetDialogue('📷 截图已保存', 3000);
    }
  });
}

// ── Achievement Display ──
const ACHIEVEMENT_DEFS = {
  first_request:      '🎯 初次接触',
  ten_requests:       '🔟 十面埋伏',
  hundred_requests:   '💯 百折不挠',
  thousand_requests:  '🏅 千锤百炼',
  first_hallucination:'👁 抓到了',
  ten_hallucinations: '🔍 惯犯',
  first_circuit_break:'⚡ 忍无可忍',
  perfect_streak:     '✨ 完美主义',
  night_owl:          '🦉 夜猫子',
  early_bird:         '🐦 早起鸟',
  trust_recovery:     '🤝 重归于好',
  long_session:       '🏃 马拉松',
};

function refreshAchievements(unlocked) {
  const grid = document.getElementById('ach-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const unlockedSet = new Set(unlocked || []);
  for (const [id, label] of Object.entries(ACHIEVEMENT_DEFS)) {
    const badge = document.createElement('div');
    badge.className = 'ach-badge' + (unlockedSet.has(id) ? '' : ' locked');
    badge.textContent = unlockedSet.has(id) ? label : '???';
    badge.title = unlockedSet.has(id) ? label : '未解锁';
    grid.appendChild(badge);
  }
}

// Request achievements when settings panel opens
const origToggle = typeof toggleSettings === 'function' ? toggleSettings : null;
if (origToggle) {
  toggleSettings = function() {
    origToggle();
    if (settingsOverlay && settingsOverlay.classList.contains('open')) {
      ipcRenderer.invoke('get-pet-stats').then(stats => {
        if (stats && stats.achievements) {
          refreshAchievements(stats.achievements);
        }
      });
    }
  };
}

"""

js = js.replace("// ── Start ──", PHASE3_JS + "\n// ── Start ──", 1)

with open(REN_PATH, "w", encoding="utf-8") as f:
    f.write(js)
print("renderer.js patched: +theme switcher, +screenshot, +achievements")

# ══════════════════════════════════════════
# 2. Patch main.js — screenshot IPC + theme persistence
# ══════════════════════════════════════════

MAIN_PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\main.js"
with open(MAIN_PATH, "r", encoding="utf-8") as f:
    mjs = f.read()

# Add screenshot + theme IPC before "// Restore proxy on app quit"
PHASE3_IPC = r"""
  // ── Screenshot ──
  ipcMain.handle('take-screenshot', async () => {
    if (!mainWindow || mainWindow.isDestroyed()) return null;
    try {
      const image = await mainWindow.capturePage();
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const desktopPath = require('path').join(
        require('os').homedir(), 'Desktop',
        `agent-monitor-${timestamp}.png`
      );
      require('fs').writeFileSync(desktopPath, image.toPNG());
      console.log('[Screenshot] Saved to:', desktopPath);
      return desktopPath;
    } catch (e) {
      console.error('[Screenshot] Failed:', e.message);
      return null;
    }
  });

  // ── Theme persistence ──
  let currentTheme = 'default';
  const themePath = require('path').join(getProjectRoot(), 'config', 'current_theme.json');

  ipcMain.on('set-theme', (event, theme) => {
    currentTheme = theme;
    try {
      require('fs').writeFileSync(themePath, JSON.stringify({ theme }), 'utf8');
    } catch (e) {}
  });

  ipcMain.handle('get-pet-stats', () => {
    // Return placeholder stats (would connect to Python backend in production)
    return {
      achievements: [],
      total_requests: 0,
    };
  });

  // Restore theme on startup
  try {
    if (require('fs').existsSync(themePath)) {
      const saved = JSON.parse(require('fs').readFileSync(themePath, 'utf8'));
      currentTheme = saved.theme || 'default';
      // Will be sent to renderer after it loads
    }
  } catch (e) {}

"""

mjs = mjs.replace("  // Restore proxy on app quit", PHASE3_IPC + "  // Restore proxy on app quit", 1)

# Send restored theme after window loads
OLD_LOAD_LINE = "  // ── System tray ──"
NEW_LOAD_LINE = """  // ── Restore theme ──
  mainWindow.webContents.once('did-finish-load', () => {
    if (currentTheme && currentTheme !== 'default') {
      mainWindow.webContents.send('theme-restored', currentTheme);
    }
  });

  // ── System tray ──"""
mjs = mjs.replace(OLD_LOAD_LINE, NEW_LOAD_LINE, 1)

with open(MAIN_PATH, "w", encoding="utf-8") as f:
    f.write(mjs)
print("main.js patched: +screenshot IPC, +theme persistence, +pet-stats")
