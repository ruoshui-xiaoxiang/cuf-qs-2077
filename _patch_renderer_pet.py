"""Patch renderer.html to add pet character CSS + HTML."""
import re

PATH = r"e:\QODERCN\ai_agent_monitor\hud_electron\renderer.html"

with open(PATH, "r", encoding="utf-8") as f:
    html = f.read()

# ══════════════════════════════════════════
# 1. Add pet character CSS before </style>
# ══════════════════════════════════════════

PET_CSS = r"""
/* ═══════════════════════════════════════════
   Pet Character — CSS Cyberpunk Robot Mascot
   ═══════════════════════════════════════════ */

#pet-container {
  position: fixed;
  bottom: 24px;
  left: 12px;
  z-index: 80;
  pointer-events: none;
  transition: opacity 0.5s ease;
}

#pet-character {
  width: 64px;
  height: 76px;
  position: relative;
  animation: petFloat 3s ease-in-out infinite;
}

@keyframes petFloat {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-4px); }
}

/* ── Antenna ── */
.pet-antenna {
  position: absolute;
  top: 0; left: 50%;
  transform: translateX(-50%);
  width: 2px; height: 10px;
  background: var(--hud-green-dim);
}
.pet-antenna-tip {
  position: absolute;
  top: -4px; left: -3px;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--hud-green);
  box-shadow: 0 0 6px var(--hud-green), 0 0 12px rgba(0,255,136,0.4);
  animation: antennaPulse 2s ease-in-out infinite;
}
@keyframes antennaPulse {
  0%, 100% { box-shadow: 0 0 6px var(--hud-green), 0 0 12px rgba(0,255,136,0.4); }
  50%      { box-shadow: 0 0 10px var(--hud-green), 0 0 20px rgba(0,255,136,0.6); }
}

/* ── Head ── */
.pet-head {
  position: absolute;
  top: 10px; left: 50%;
  transform: translateX(-50%);
  width: 40px; height: 30px;
  background: #1a1a2e;
  border: 2px solid var(--hud-green-dim);
  border-radius: 8px 8px 6px 6px;
  overflow: hidden;
}

/* ── Eyes ── */
.pet-eyes {
  display: flex;
  justify-content: center;
  gap: 8px;
  padding-top: 8px;
}
.pet-eye {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--hud-green);
  box-shadow: 0 0 4px var(--hud-green);
  transition: all 0.3s ease;
}

/* ── Mouth ── */
.pet-mouth {
  position: absolute;
  bottom: 4px; left: 50%;
  transform: translateX(-50%);
  width: 12px; height: 3px;
  background: var(--hud-green-dim);
  border-radius: 0 0 4px 4px;
  transition: all 0.3s ease;
}

/* ── Body ── */
.pet-body-box {
  position: absolute;
  top: 42px; left: 50%;
  transform: translateX(-50%);
  width: 30px; height: 22px;
  background: #1a1a2e;
  border: 2px solid var(--hud-green-dim);
  border-radius: 4px;
}
.pet-core {
  position: absolute;
  top: 5px; left: 50%;
  transform: translateX(-50%);
  width: 10px; height: 10px;
  border-radius: 50%;
  background: var(--hud-green-dark);
  border: 1px solid var(--hud-green-dim);
  animation: corePulse 1.5s ease-in-out infinite;
}
@keyframes corePulse {
  0%, 100% { background: var(--hud-green-dark); box-shadow: none; }
  50%      { background: var(--hud-green-dim); box-shadow: 0 0 6px var(--hud-green); }
}

/* ── Arms ── */
.pet-arm {
  position: absolute;
  top: 44px;
  width: 6px; height: 16px;
  background: var(--hud-green-dark);
  border: 1px solid var(--hud-green-dim);
  border-radius: 3px;
  transition: transform 0.3s ease;
}
.pet-arm.left  { left: 8px; transform-origin: top center; }
.pet-arm.right { right: 8px; transform-origin: top center; }

/* ── Dialogue bubble ── */
#pet-bubble {
  position: absolute;
  bottom: 80px; left: 50%;
  transform: translateX(-50%);
  background: rgba(0, 0, 0, 0.85);
  border: 1px solid var(--hud-green-dim);
  color: var(--hud-green);
  font-size: 10px;
  padding: 4px 8px;
  border-radius: 4px;
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: 0;
  transition: opacity 0.3s ease;
  pointer-events: none;
  text-shadow: 0 0 4px var(--hud-green);
}
#pet-bubble.visible { opacity: 1; }
#pet-bubble::after {
  content: '';
  position: absolute;
  bottom: -5px; left: 50%;
  transform: translateX(-50%);
  width: 0; height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 5px solid var(--hud-green-dim);
}

/* ═══ State: HAPPY (trust > 0.85) ═══ */
#pet-character.happy .pet-eye {
  height: 4px;
  border-radius: 4px 4px 0 0;
  background: var(--hud-green);
  box-shadow: 0 0 8px var(--hud-green);
}
#pet-character.happy .pet-mouth {
  width: 14px; height: 5px;
  border-radius: 0 0 6px 6px;
  background: var(--hud-green);
}
#pet-character.happy .pet-arm.left  { animation: armWaveL 0.8s ease-in-out infinite; }
#pet-character.happy .pet-arm.right { animation: armWaveR 0.8s ease-in-out infinite 0.2s; }
@keyframes armWaveL { 0%,100%{transform:rotate(0)} 50%{transform:rotate(-20deg)} }
@keyframes armWaveR { 0%,100%{transform:rotate(0)} 50%{transform:rotate(20deg)} }
#pet-character.happy { animation: petBounce 0.6s ease-in-out infinite; }
@keyframes petBounce {
  0%,100% { transform: translateY(0) scaleY(1); }
  50%     { transform: translateY(-6px) scaleY(1.05); }
}

/* ═══ State: IDLE (trust 0.6–0.85) ═══ */
#pet-character.idle .pet-eye {
  width: 8px; height: 8px;
  border-radius: 50%;
}
#pet-character.idle { animation: petFloat 3s ease-in-out infinite; }

/* ═══ State: WARNING (trust 0.4–0.6) ═══ */
#pet-character.warning .pet-eye {
  width: 10px; height: 6px;
  border-radius: 2px;
  background: var(--hud-amber);
  box-shadow: 0 0 6px var(--hud-amber);
}
#pet-character.warning .pet-mouth {
  width: 8px; height: 2px;
  border-radius: 0;
  background: var(--hud-amber);
}
#pet-character.warning .pet-antenna-tip {
  background: var(--hud-amber);
  box-shadow: 0 0 6px var(--hud-amber);
  animation: antennaWarn 0.5s ease-in-out infinite;
}
@keyframes antennaWarn {
  0%,100% { opacity: 1; } 50% { opacity: 0.3; }
}
#pet-character.warning .pet-core {
  background: var(--hud-amber);
  animation: none;
}

/* ═══ State: ANGRY (trust 0.2–0.4) ═══ */
#pet-character.angry .pet-eye {
  width: 8px; height: 8px;
  border-radius: 0;
  background: var(--hud-red);
  box-shadow: 0 0 8px var(--hud-red);
}
#pet-character.angry .pet-eye:first-child {
  clip-path: polygon(0 30%, 50% 50%, 0 70%, 100% 50%, 0 30%);
}
#pet-character.angry .pet-eye:last-child {
  clip-path: polygon(100% 30%, 50% 50%, 100% 70%, 0 50%, 100% 30%);
}
#pet-character.angry .pet-mouth {
  width: 14px; height: 3px;
  border-radius: 4px 4px 0 0;
  background: var(--hud-red);
}
#pet-character.angry { animation: petShake 0.15s linear infinite; }
@keyframes petShake {
  0%,100% { transform: translateX(0); }
  25%     { transform: translateX(-2px); }
  75%     { transform: translateX(2px); }
}
#pet-character.angry .pet-antenna-tip {
  background: var(--hud-red);
  box-shadow: 0 0 10px var(--hud-red);
}
#pet-character.angry .pet-core {
  background: var(--hud-red);
  box-shadow: 0 0 8px var(--hud-red);
  animation: none;
}

/* ═══ State: SLEEPING (trust < 0.2) ═══ */
#pet-character.sleeping .pet-eye {
  width: 8px; height: 2px;
  border-radius: 0;
  background: var(--hud-green-dark);
  box-shadow: none;
}
#pet-character.sleeping .pet-mouth {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: transparent;
  border: 1px solid var(--hud-green-dark);
}
#pet-character.sleeping { animation: petSleep 2s ease-in-out infinite; }
@keyframes petSleep {
  0%,100% { transform: translateY(0) rotate(0); }
  50%     { transform: translateY(2px) rotate(-3deg); }
}
#pet-character.sleeping .pet-antenna-tip {
  background: var(--hud-green-dark);
  box-shadow: none;
  animation: none;
}
#pet-character.sleeping .pet-core {
  animation: none;
  background: var(--hud-green-dark);
  box-shadow: none;
}
/* Zzz */
.pet-zzz {
  position: absolute;
  top: 2px; right: -8px;
  color: var(--hud-green-dark);
  font-size: 10px;
  opacity: 0;
}
#pet-character.sleeping .pet-zzz {
  animation: zzzFloat 2s ease-in-out infinite;
}
@keyframes zzzFloat {
  0%   { opacity: 0; transform: translate(0, 0) scale(0.8); }
  50%  { opacity: 1; transform: translate(4px, -8px) scale(1); }
  100% { opacity: 0; transform: translate(8px, -16px) scale(0.6); }
}
"""

html = html.replace("</style>", PET_CSS + "\n</style>", 1)

# ══════════════════════════════════════════
# 2. Add pet character HTML before status div
# ══════════════════════════════════════════

PET_HTML = """<!-- Pet Character -->
<div id="pet-container">
  <div id="pet-bubble"></div>
  <div id="pet-character" class="idle">
    <div class="pet-antenna">
      <div class="pet-antenna-tip"></div>
    </div>
    <div class="pet-head">
      <div class="pet-eyes">
        <div class="pet-eye"></div>
        <div class="pet-eye"></div>
      </div>
      <div class="pet-mouth"></div>
    </div>
    <div class="pet-arm left"></div>
    <div class="pet-arm right"></div>
    <div class="pet-body-box">
      <div class="pet-core"></div>
    </div>
    <div class="pet-zzz">z Z z</div>
  </div>
</div>

"""

html = html.replace(
    '<!-- Connection status -->\n<div id="status">connecting...</div>',
    PET_HTML + '<!-- Connection status -->\n<div id="status">connecting...</div>',
    1
)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(html)

print(f"renderer.html patched: +{PET_CSS.count(chr(10))} CSS lines, +{PET_HTML.count(chr(10))} HTML lines")
