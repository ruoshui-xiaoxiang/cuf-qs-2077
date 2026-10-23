"""Patch renderer.js to add pet character state management + dialogue bubble."""

PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\renderer.js"

with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# ══════════════════════════════════════════
# Add pet state management before "// ── Start ──"
# ══════════════════════════════════════════

PET_CODE = r"""
// ── Pet Character State Management ──

const petCharacter = document.getElementById('pet-character');
const petBubble = document.getElementById('pet-bubble');
let petState = 'idle';
let petBubbleTimer = null;
let currentTrust = 0.8;

/**
 * Map trust score to pet state.
 * > 0.85  → happy
 * 0.6–0.85 → idle
 * 0.4–0.6  → warning
 * 0.2–0.4  → angry
 * < 0.2    → sleeping
 */
function trustToState(trust) {
  if (trust > 0.85) return 'happy';
  if (trust > 0.6)  return 'idle';
  if (trust > 0.4)  return 'warning';
  if (trust > 0.2)  return 'angry';
  return 'sleeping';
}

function setPetState(newState) {
  if (newState === petState) return;
  petCharacter.classList.remove(petState);
  petCharacter.classList.add(newState);
  petState = newState;
}

function updatePetFromTrust(trust) {
  currentTrust = trust;
  const newState = trustToState(trust);
  setPetState(newState);
}

function showPetDialogue(text, durationMs) {
  if (!petBubble || !text) return;
  durationMs = durationMs || 5000;
  petBubble.textContent = text;
  petBubble.classList.add('visible');
  if (petBubbleTimer) clearTimeout(petBubbleTimer);
  petBubbleTimer = setTimeout(() => {
    petBubble.classList.remove('visible');
    petBubbleTimer = null;
  }, durationMs);
}

// IPC: receive trust updates from main process
ipcRenderer.on('pet-update', (event, data) => {
  if (data.trust !== undefined) {
    updatePetFromTrust(data.trust);
  }
  if (data.dialogue) {
    showPetDialogue(data.dialogue, data.duration || 5000);
  }
  if (data.state) {
    setPetState(data.state);
  }
});

// Also extract trust from WS events for immediate feedback
function extractTrustFromEvent(event) {
  // If event contains trust info, update pet immediately
  if (event.trust !== undefined) {
    updatePetFromTrust(event.trust);
  }
  // Map event kinds to pet dialogue
  const kindToDialogue = {
    'trust_drop':    { text: '...不太对劲', state: 'warning' },
    'metric_ok':     { text: '这次还行', state: 'happy' },
    'circuit_break': { text: '受够了！', state: 'angry' },
  };
  const mapping = kindToDialogue[event.kind];
  if (mapping) {
    showPetDialogue(mapping.text);
  }
}

"""

# Insert before "// ── Start ──"
code = code.replace("// ── Start ──", PET_CODE + "\n// ── Start ──", 1)

# Also patch addEvent to extract trust data
old_add_event_line = "  // Random holographic flicker"
new_add_event_line = "  // Update pet from event data\n  extractTrustFromEvent(event);\n\n  // Random holographic flicker"
code = code.replace(old_add_event_line, new_add_event_line, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

print(f"renderer.js patched: +{PET_CODE.count(chr(10))} lines")
