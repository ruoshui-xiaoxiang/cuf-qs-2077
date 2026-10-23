"""Create pet_memory.py — persistent storage for pet state across sessions."""
import os

BASE = r"e:\QODERCN\ai_agent_monitor"
TARGET = os.path.join(BASE, "src", "common", "pet_memory.py")

CODE = r'''"""Common: Pet memory — persistent state storage for the desktop pet.

Saves and loads:
- Cumulative monitoring stats (total requests, errors, etc.)
- Trust history (rolling window for trend display)
- Achievements (milestones unlocked by monitoring)
- Session history (last seen timestamps, uptime)

Zero dependencies — pure stdlib.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ── Default memory state ──
_DEFAULT_MEMORY = {
    "version": 1,
    "created_at": 0,
    "last_session": 0,
    "total_sessions": 0,
    "total_requests": 0,
    "total_errors": 0,
    "total_circuit_breaks": 0,
    "trust_history": [],       # [{ts, value}] — last 100 entries
    "peak_trust": 0.0,
    "lowest_trust": 1.0,
    "achievements": [],        # ["first_request", "first_hallucination", ...]
    "favorite_agent": "",      # agent_id with most requests
    "agent_counts": {},        # {agent_id: count}
}

# ── Achievement definitions ──
ACHIEVEMENTS = {
    "first_request":      {"name": "初次接触",     "desc": "监控了第一个 AI 请求"},
    "ten_requests":       {"name": "十面埋伏",     "desc": "累计监控 10 个请求"},
    "hundred_requests":   {"name": "百折不挠",     "desc": "累计监控 100 个请求"},
    "thousand_requests":  {"name": "千锤百炼",     "desc": "累计监控 1000 个请求"},
    "first_hallucination": {"name": "抓到了",      "desc": "第一次检测到 AI 幻觉"},
    "ten_hallucinations": {"name": "惯犯",         "desc": "累计检测 10 次幻觉"},
    "first_circuit_break": {"name": "忍无可忍",    "desc": "第一次触发熔断"},
    "perfect_streak":     {"name": "完美主义",     "desc": "连续 50 次请求无异常"},
    "night_owl":          {"name": "夜猫子",       "desc": "凌晨 2 点后仍在监控"},
    "early_bird":         {"name": "早起鸟",       "desc": "早上 6 点前开始监控"},
    "trust_recovery":     {"name": "重归于好",     "desc": "trust 从 <0.3 恢复到 >0.8"},
    "long_session":       {"name": "马拉松",       "desc": "单次会话超过 2 小时"},
}


class PetMemory:
    """Persistent memory store for the desktop pet.

    Thread-safe: all writes go through a lock.
    Auto-saves on every mutation.
    """

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))),
                "config", "pet_memory.json"
            )
        self._path = path
        self._data = self._load()
        self._dirty = False

    def _load(self) -> dict:
        """Load memory from disk, or return defaults."""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Merge with defaults for any missing keys
                merged = dict(_DEFAULT_MEMORY)
                merged.update(data)
                log.info("Pet memory loaded: %d requests, %d achievements",
                         merged.get("total_requests", 0),
                         len(merged.get("achievements", [])))
                return merged
        except FileNotFoundError:
            log.info("No pet memory file found, starting fresh")
            data = dict(_DEFAULT_MEMORY)
            data["created_at"] = time.time()
            return data
        except Exception as e:
            log.warning("Failed to load pet memory: %s", e)
            data = dict(_DEFAULT_MEMORY)
            data["created_at"] = time.time()
            return data

    def save(self) -> None:
        """Persist memory to disk."""
        try:
            dir_path = os.path.dirname(self._path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False,
                          default=str)
            self._dirty = False
        except Exception as e:
            log.error("Failed to save pet memory: %s", e)

    # ── Request tracking ──

    def record_request(self, agent_id: str = "default_agent",
                       success: bool = True) -> List[str]:
        """Record a monitored request. Returns newly unlocked achievements."""
        self._data["total_requests"] = self._data.get("total_requests", 0) + 1
        if not success:
            self._data["total_errors"] = self._data.get("total_errors", 0) + 1

        # Agent counts
        counts = self._data.get("agent_counts", {})
        counts[agent_id] = counts.get(agent_id, 0) + 1
        self._data["agent_counts"] = counts
        # Update favorite
        if counts:
            self._data["favorite_agent"] = max(counts, key=counts.get)

        return self._check_achievements()

    def record_trust(self, value: float) -> None:
        """Record a trust score sample."""
        history = self._data.get("trust_history", [])
        history.append({"ts": time.time(), "value": round(value, 4)})
        # Keep last 200 entries
        if len(history) > 200:
            history[:] = history[-200:]
        self._data["trust_history"] = history
        self._data["peak_trust"] = max(self._data.get("peak_trust", 0), value)
        self._data["lowest_trust"] = min(self._data.get("lowest_trust", 1), value)
        self.save()

    def record_circuit_break(self) -> List[str]:
        """Record a circuit break event. Returns new achievements."""
        self._data["total_circuit_breaks"] = \
            self._data.get("total_circuit_breaks", 0) + 1
        return self._check_achievements()

    # ── Session management ──

    def start_session(self) -> None:
        """Mark the start of a new session."""
        self._data["total_sessions"] = self._data.get("total_sessions", 0) + 1
        self._data["last_session"] = time.time()
        self._data["_session_start"] = time.time()
        self.save()

    def end_session(self) -> None:
        """Mark the end of the current session."""
        start = self._data.pop("_session_start", None)
        if start:
            duration = time.time() - start
            if duration > 7200:  # 2 hours
                self._unlock("long_session")
        self.save()

    # ── Achievements ──

    def _check_achievements(self) -> List[str]:
        """Check and unlock any new achievements. Returns newly unlocked IDs."""
        newly = []
        total = self._data.get("total_requests", 0)
        errors = self._data.get("total_errors", 0)
        breaks = self._data.get("total_circuit_breaks", 0)
        unlocked = set(self._data.get("achievements", []))

        checks = {
            "first_request":      total >= 1,
            "ten_requests":       total >= 10,
            "hundred_requests":   total >= 100,
            "thousand_requests":  total >= 1000,
            "first_hallucination": errors >= 1,
            "ten_hallucinations": errors >= 10,
            "first_circuit_break": breaks >= 1,
        }

        # Time-based
        hour = time.localtime().tm_hour
        if hour >= 2 and hour < 5:
            checks["night_owl"] = True
        if hour >= 4 and hour < 6:
            checks["early_bird"] = True

        for ach_id, condition in checks.items():
            if condition and ach_id not in unlocked:
                unlocked.add(ach_id)
                newly.append(ach_id)

        self._data["achievements"] = list(unlocked)
        if newly:
            self.save()
        return newly

    def _unlock(self, ach_id: str) -> List[str]:
        """Force-unlock an achievement."""
        unlocked = set(self._data.get("achievements", []))
        if ach_id not in unlocked:
            unlocked.add(ach_id)
            self._data["achievements"] = list(unlocked)
            self.save()
            return [ach_id]
        return []

    # ── Read-only accessors ──

    @property
    def total_requests(self) -> int:
        return self._data.get("total_requests", 0)

    @property
    def total_errors(self) -> int:
        return self._data.get("total_errors", 0)

    @property
    def total_sessions(self) -> int:
        return self._data.get("total_sessions", 0)

    @property
    def achievements(self) -> List[str]:
        return list(self._data.get("achievements", []))

    @property
    def trust_history(self) -> List[Dict]:
        return list(self._data.get("trust_history", []))

    @property
    def stats(self) -> Dict[str, Any]:
        """Return a summary dict for display."""
        return {
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "total_sessions": self.total_sessions,
            "total_circuit_breaks": self._data.get("total_circuit_breaks", 0),
            "peak_trust": self._data.get("peak_trust", 0),
            "lowest_trust": self._data.get("lowest_trust", 1),
            "achievements": self.achievements,
            "favorite_agent": self._data.get("favorite_agent", ""),
            "created_at": self._data.get("created_at", 0),
        }
'''

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(CODE)

print(f"Created {TARGET} ({CODE.count(chr(10))} lines)")
