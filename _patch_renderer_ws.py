"""Patch renderer.js ws.onmessage to handle _pet_update messages."""

PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\renderer.js"

with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# Replace the ws.onmessage handler to detect pet_update messages
OLD_HANDLER = """  ws.onmessage = (e) => {
    try {
      const events = JSON.parse(e.data);
      const batch = Array.isArray(events) ? events : [events];
      for (const event of batch) {
        addEvent(event);
      }
    } catch (err) {
      console.warn('Bad event:', err);
    }
  };"""

NEW_HANDLER = """  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      // Handle pet-update messages (single object with _pet_update flag)
      if (data && data._pet_update) {
        if (data.trust !== undefined) updatePetFromTrust(data.trust);
        if (data.dialogue) showPetDialogue(data.dialogue, data.duration || 5000);
        if (data.state) setPetState(data.state);
        return;
      }
      const batch = Array.isArray(data) ? data : [data];
      for (const event of batch) {
        addEvent(event);
      }
    } catch (err) {
      console.warn('Bad event:', err);
    }
  };"""

code = code.replace(OLD_HANDLER, NEW_HANDLER, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

print("renderer.js patched: ws.onmessage handles _pet_update")
