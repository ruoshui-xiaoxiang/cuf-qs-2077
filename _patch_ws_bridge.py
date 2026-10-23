"""Patch ws_bridge.py to add trust injection + pet update broadcasting."""

PATH = r"e:\QODERCN\ai_agent_monitor\src\common\ws_bridge.py"

with open(PATH, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add trust_scorer parameter and pet_update broadcasting
# Insert after __init__ method's last line (self._event_queue)

OLD_INIT_END = "        self._event_queue: Optional[queue.Queue] = None"
NEW_INIT_END = """        self._event_queue: Optional[queue.Queue] = None
        self._trust_scorer = None  # Set via set_trust_scorer()
        self._last_pet_trust = {}  # agent_id → last broadcast trust"""

code = code.replace(OLD_INIT_END, NEW_INIT_END, 1)

# 2. Add set_trust_scorer + broadcast_pet_update methods before start()
OLD_START = "    def start(self) -> None:"
NEW_METHODS = """    def set_trust_scorer(self, trust_scorer) -> None:
        \"\"\"Attach trust scorer for automatic trust injection into events.\"\"\"
        self._trust_scorer = trust_scorer

    def broadcast_pet_update(self, data: dict) -> None:
        \"\"\"Send a pet-update message to all connected WS clients.\"\"\"
        if not self._clients:
            return
        msg = json.dumps({"_pet_update": True, **data}, default=str,
                         ensure_ascii=True)
        for ws in list(self._clients):
            try:
                asyncio.run_coroutine_threadsafe(
                    ws.send(msg), self._loop
                )
            except Exception:
                pass

    def start(self) -> None:"""

code = code.replace(OLD_START, NEW_METHODS, 1)

# 3. Inject trust into events before broadcasting (in the handler function)
OLD_SEND = """                    if batch:
                        data = json.dumps(batch, default=str, ensure_ascii=True)
                        await websocket.send(data)"""

NEW_SEND = """                    if batch:
                        # Inject trust scores into events
                        if self._trust_scorer:
                            for evt in batch:
                                aid = evt.get("agent_id", "")
                                if aid:
                                    evt["trust"] = self._trust_scorer.get_score(aid)
                        data = json.dumps(batch, default=str, ensure_ascii=True)
                        await websocket.send(data)"""

code = code.replace(OLD_SEND, NEW_SEND, 1)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(code)

print("ws_bridge.py patched: +trust injection, +broadcast_pet_update")
