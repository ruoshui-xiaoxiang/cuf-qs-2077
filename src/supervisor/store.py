"""Audit store for the agent task supervisor."""

import json
import time
from pathlib import Path


class JsonlAuditStore:
    """Append-only JSONL audit store, mirroring the project's existing style."""

    def __init__(self, log_path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry):
        payload = dict(entry)
        payload.setdefault("timestamp", time.time())
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        return payload

    def load_all(self):
        if not self.log_path.exists():
            return []
        items = []
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                items.append(json.loads(line))
        return items
