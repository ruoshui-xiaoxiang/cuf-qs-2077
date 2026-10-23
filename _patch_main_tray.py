"""Patch main.js to add system tray + right-click menu + auto-start + update check."""

PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\main.js"

with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add Tray import
OLD_IMPORT = "const { app, BrowserWindow, screen, ipcMain } = require('electron');"
NEW_IMPORT = "const { app, BrowserWindow, screen, ipcMain, Tray, Menu, nativeImage } = require('electron');"
code = code.replace(OLD_IMPORT, NEW_IMPORT, 1)

# 2. Add tray + auto-start + update check before app.whenReady()
TRAY_CODE = r"""
// ── System Tray ──
let tray = null;

function createTray() {
  // Create a simple 16x16 tray icon (green circle on transparent)
  const icon = nativeImage.createFromBuffer(createTrayIconBuffer());
  icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.setToolTip('AI Agent Monitor');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show / Hide',
      click: () => {
        if (mainWindow.isVisible()) {
          mainWindow.hide();
        } else {
          mainWindow.show();
        }
      }
    },
    { type: 'separator' },
    {
      label: 'Settings',
      click: () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.show();
          mainWindow.webContents.send('open-settings');
        }
      }
    },
    {
      label: 'Reset Pet State',
      click: () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('pet-update', { trust: 0.8, state: 'idle' });
        }
      }
    },
    { type: 'separator' },
    {
      label: 'Start with Windows',
      type: 'checkbox',
      checked: app.getLoginItemSettings().openAtLogin,
      click: (menuItem) => {
        app.setLoginItemSettings({ openAtLogin: menuItem.checked });
      }
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        restoreProxy();
        stopPythonBackend();
        app.quit();
      }
    }
  ]);

  tray.setContextMenu(contextMenu);
  tray.on('double-click', () => {
    if (mainWindow) {
      mainWindow.isVisible() ? mainWindow.hide() : mainWindow.show();
    }
  });
}

// Generate a simple 16x16 green dot icon as PNG buffer
function createTrayIconBuffer() {
  // Minimal 16x16 RGBA raw pixel data (green circle)
  const size = 16;
  const buf = Buffer.alloc(size * size * 4, 0);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - 7.5, dy = y - 7.5;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const idx = (y * size + x) * 4;
      if (dist < 7) {
        buf[idx]     = 0;    // R
        buf[idx + 1] = 255;  // G
        buf[idx + 2] = 136;  // B
        buf[idx + 3] = 255;  // A
      } else if (dist < 8) {
        // Anti-aliased edge
        const alpha = Math.round((8 - dist) * 255);
        buf[idx]     = 0;
        buf[idx + 1] = 255;
        buf[idx + 2] = 136;
        buf[idx + 3] = alpha;
      }
    }
  }
  return nativeImage.createFromBuffer(buf, { width: size, height: size });
}

// ── Update check (lightweight) ──
function checkForUpdates() {
  // Placeholder: check a remote URL for newer version
  // For now, just log
  console.log('[Update] Current version:', app.getVersion());
  // In production, this would fetch a version.json from a release server
}

"""

code = code.replace("app.whenReady().then(async () => {", TRAY_CODE + "\napp.whenReady().then(async () => {", 1)

# 3. Add createTray() call after mainWindow creation
OLD_LOAD = "  mainWindow.loadFile(path.join(__dirname, 'renderer.html'));"
NEW_LOAD = """  mainWindow.loadFile(path.join(__dirname, 'renderer.html'));

  // ── System tray ──
  createTray();

  // ── Check for updates (non-blocking) ──
  setTimeout(checkForUpdates, 5000);"""
code = code.replace(OLD_LOAD, NEW_LOAD, 1)

# 4. Add open-settings IPC listener near other ipcMain handlers
SETTINGS_IPC = r"""
  // ── Open settings from tray ──
  ipcMain.on('open-settings', () => {
    // Handled in renderer via IPC
  });

"""
code = code.replace("  // Restore proxy on app quit", SETTINGS_IPC + "  // Restore proxy on app quit", 1)

# 5. Clean up tray on quit
OLD_QUIT = """app.on('window-all-closed', () => {
  stopPythonBackend();
  app.quit();
});"""
NEW_QUIT = """app.on('window-all-closed', () => {
  if (tray) tray.destroy();
  stopPythonBackend();
  app.quit();
});"""
code = code.replace(OLD_QUIT, NEW_QUIT, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

print("main.js patched: +system tray, +auto-start, +update check")
