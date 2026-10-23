"""Rewrite token_stats.py with word-level analysis."""
import os

code = r'''"""W0: Token-level behavioral statistics.

Extracts lightweight statistical features from LLM response text
without semantic understanding. Uses WORD-level analysis for
detecting degenerate generation patterns.
"""

import logging
import re
from collections import deque
from typing import Dict, Tuple

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r'\w+', re.UNICODE)


class TokenStatsTracker:
    """Tracks token-level statistics across API responses."""

    def __init__(self, window_size: int = 50):
        self._window = deque(maxlen=window_size)

    def analyze(self, response_json: dict) -> Dict[str, float]:
        """Extract token stats from an API response JSON."""
        content = self._extract_content(response_json)
        words = _WORD_RE.findall(content.lower()) if content else []
        n_words = len(words)

        if n_words < 3:
            return {"repeat_ratio": 0.0, "unique_ratio": 0.0,
                    "length_z": 0.0, "content_length": 0}

        repeat = self._word_repeat_ratio(words)
        unique = self._word_unique_ratio(words)
        z = self._length_zscore(n_words)
        self._window.append(n_words)

        return {"repeat_ratio": round(repeat, 4),
                "unique_ratio": round(unique, 4),
                "length_z": round(z, 4),
                "content_length": n_words}

    @staticmethod
    def _extract_content(rj: dict) -> str:
        """Extract text content from OpenAI-compatible response."""
        try:
            choices = rj.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
                if content:
                    return content
                tc = choices[0].get("message", {}).get("tool_calls", [])
                if tc:
                    return str(tc)
            return rj.get("text", "") or rj.get("completion", "")
        except (AttributeError, IndexError, TypeError):
            return ""

    @staticmethod
    def _word_repeat_ratio(words: list) -> float:
        """Fraction of words identical to the immediately preceding word."""
        if len(words) < 2:
            return 0.0
        repeats = sum(1 for i in range(1, len(words))
                      if words[i] == words[i - 1])
        return repeats / (len(words) - 1)

    @staticmethod
    def _word_unique_ratio(words: list) -> float:
        """Type-token ratio: unique words / total words."""
        if not words:
            return 0.0
        return len(set(words)) / len(words)

    def _length_zscore(self, n_words: int) -> float:
        """Z-score of word count vs sliding window."""
        if len(self._window) < 3:
            return 0.0
        mean = sum(self._window) / len(self._window)
        var = sum((x - mean) ** 2 for x in self._window) / len(self._window)
        std = var ** 0.5
        if std < 1:
            return 0.0
        return (n_words - mean) / std

    @staticmethod
    def assess_quality(stats: Dict[str, float]) -> Tuple[bool, str]:
        """Assess response quality based on token stats.

        Thresholds calibrated for word-level analysis:
          - repeat_ratio > 0.15 -> degenerate repetition
          - unique_ratio < 0.40 -> very low vocabulary diversity
        """
        repeat = stats.get("repeat_ratio", 0)
        unique = stats.get("unique_ratio", 0)
        n_words = stats.get("content_length", 0)

        if n_words < 3:
            return False, "empty_response"

        if repeat > 0.15:
            return False, f"high_repetition({repeat:.2f})"

        if unique < 0.40 and n_words > 10:
            return False, f"low_diversity({unique:.2f})"

        return True, "token_stats_ok"
'''

path = r"e:\QODERCN\ai_agent_monitor\src\w0\token_stats.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(code)
print("token_stats.py rewritten (word-level)")
