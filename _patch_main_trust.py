"""Patch main.py to wire trust_scorer to ws_bridge + add pet dialogue integration."""

PATH = r"e:\QODERCN\ai_agent_monitor\main.py"

with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# 1. After ws_bridge = WebSocketBridge(bus) / ws_bridge.start()
#    Add: ws_bridge.set_trust_scorer(runner._trust)
# There are two occurrences (electron mode and normal mode)

OLD_ELECTRON = """        ws_bridge = WebSocketBridge(bus)
        ws_bridge.start()
        log.info("Electron HUD mode: ws://127.0.0.1:20781")"""

NEW_ELECTRON = """        ws_bridge = WebSocketBridge(bus)
        ws_bridge.set_trust_scorer(runner._trust)
        ws_bridge.start()
        log.info("Electron HUD mode: ws://127.0.0.1:20781")"""

code = code.replace(OLD_ELECTRON, NEW_ELECTRON, 1)

# 2. Also patch the second occurrence (non-electron HUD mode)
OLD_NORMAL = """    ws_bridge = WebSocketBridge(bus)
    ws_bridge.start()"""

NEW_NORMAL = """    ws_bridge = WebSocketBridge(bus)
    ws_bridge.set_trust_scorer(runner._trust)
    ws_bridge.start()"""

code = code.replace(OLD_NORMAL, NEW_NORMAL, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

print("main.py patched: ws_bridge.set_trust_scorer wired up")
