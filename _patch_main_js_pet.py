"""Patch main.js to add pet-update IPC forwarding."""

PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\main.js"

with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# Add pet-update IPC handler after the proxy-status handler
PET_IPC = r"""
  // ── Pet state forwarding ──
  ipcMain.on('pet-update', (event, data) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('pet-update', data);
    }
  });

"""

# Insert after proxy-status handler block
marker = "  // Restore proxy on app quit"
code = code.replace(marker, PET_IPC + marker, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

print("main.js patched: +pet-update IPC handler")
