"""Patch AI Agent Monitor: add token-level behavioral stats to proxy."""
import os, textwrap

MONITOR = r"e:\QODERCN\ai_agent_monitor"

# ── 1. Patch schemas.py: add token stats fields ──
schema_path = os.path.join(MONITOR, "src", "w0", "schemas.py")
with open(schema_path, "r", encoding="utf-8") as f:
    content = f.read()

if "repeat_ratio" not in content:
    content = content.replace(
        '    model: str = ""\n\n    def to_dict',
        '    model: str = ""\n'
        '    # Token-level behavioral stats (Phase 3)\n'
        '    repeat_ratio: float = 0.0\n'
        '    unique_ratio: float = 0.0\n'
        '    length_z: float = 0.0\n'
        '\n'
        '    def to_dict'
    )
    with open(schema_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("[1/4] schemas.py: added repeat_ratio, unique_ratio, length_z")
else:
    print("[1/4] schemas.py: already patched")

# ── 2. Create token_stats.py ──
ts_path = os.path.join(MONITOR, "src", "w0", "token_stats.py")
ts_code = textwrap.dedent('''\
    """W0: Token-level behavioral statistics.

    Extracts lightweight statistical features from LLM response text
    without semantic understanding. These features help detect
    degenerate generation patterns (repetition, low diversity, etc.)
    that format-only validation misses.
    """

    import logging
    import re
    from collections import deque
    from typing import Dict, Optional, Tuple

    log = logging.getLogger(__name__)


    class TokenStatsTracker:
        """Tracks token-level statistics across API responses.

        Maintains a sliding window of completion lengths for z-score
        computation, and extracts per-response features:
          - repeat_ratio: fraction of adjacent duplicate characters
          - unique_ratio: unique characters / total characters
          - length_z: z-score of completion length vs recent history
        """

        def __init__(self, window_size: int = 50):
            self._window = deque(maxlen=window_size)
            self._window_size = window_size

        def analyze(self, response_json: dict) -> Dict[str, float]:
            """Extract token stats from an API response JSON.

            Args:
                response_json: Parsed JSON from LLM API response.

            Returns:
                Dict with keys: repeat_ratio, unique_ratio, length_z,
                content_length. All in [0, 1] except length_z (unbounded).
            """
            content = self._extract_content(response_json)
            if not content or len(content) < 5:
                return {"repeat_ratio": 0.0, "unique_ratio": 0.0,
                        "length_z": 0.0, "content_length": 0}

            repeat = self._repeat_ratio(content)
            unique = self._unique_ratio(content)
            length = len(content)
            z = self._length_zscore(length)
            self._window.append(length)

            return {"repeat_ratio": round(repeat, 4),
                    "unique_ratio": round(unique, 4),
                    "length_z": round(z, 4),
                    "content_length": length}

        def _extract_content(self, rj: dict) -> str:
            """Extract text content from OpenAI-compatible response."""
            try:
                choices = rj.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "")
                    if content:
                        return content
                    # Fallback: tool_calls JSON
                    tc = choices[0].get("message", {}).get("tool_calls", [])
                    if tc:
                        return str(tc)
                # Fallback: raw text field
                return rj.get("text", "") or rj.get("completion", "")
            except (AttributeError, IndexError, TypeError):
                return ""

        @staticmethod
        def _repeat_ratio(text: str) -> float:
            """Fraction of characters that are same as previous char."""
            if len(text) < 2:
                return 0.0
            repeats = sum(1 for i in range(1, len(text))
                         if text[i] == text[i - 1])
            return repeats / (len(text) - 1)

        @staticmethod
        def _unique_ratio(text: str) -> float:
            """Ratio of unique characters to total characters."""
            if not text:
                return 0.0
            return len(set(text)) / len(text)

        def _length_zscore(self, length: int) -> float:
            """Z-score of completion length vs sliding window."""
            if len(self._window) < 3:
                return 0.0
            mean = sum(self._window) / len(self._window)
            var = sum((x - mean) ** 2 for x in self._window) / len(self._window)
            std = var ** 0.5
            if std < 1:
                return 0.0
            return (length - mean) / std

        @staticmethod
        def assess_quality(stats: Dict[str, float]) -> Tuple[bool, str]:
            """Assess response quality based on token stats.

            Returns:
                (is_healthy, reason) tuple.
            """
            repeat = stats.get("repeat_ratio", 0)
            unique = stats.get("unique_ratio", 0)
            length = stats.get("content_length", 0)

            if length < 5:
                return False, "empty_response"

            # High repetition = degenerate generation
            if repeat > 0.3:
                return False, f"high_repetition({repeat:.2f})"

            # Very low diversity = model stuck
            if unique < 0.3 and length > 20:
                return False, f"low_diversity({unique:.2f})"

            return True, "token_stats_ok"
''')

with open(ts_path, "w", encoding="utf-8") as f:
    f.write(ts_code)
print("[2/4] token_stats.py: created")

# ── 3. Patch api_collector.py: integrate token stats ──
ac_path = os.path.join(MONITOR, "src", "w0", "api_collector.py")
with open(ac_path, "r", encoding="utf-8") as f:
    ac = f.read()

# 3a. Add import
if "token_stats" not in ac:
    ac = ac.replace(
        "from src.common.utils import ts_now",
        "from src.common.utils import ts_now\nfrom src.w0.token_stats import TokenStatsTracker"
    )

# 3b. Add tracker to APICollector.__init__
if "_token_tracker" not in ac:
    ac = ac.replace(
        "        # Set by MonitorRunner for direct event publishing in proxy mode\n        self._bus = None",
        "        # Set by MonitorRunner for direct event publishing in proxy mode\n"
        "        self._bus = None\n"
        "        # Token-level behavioral stats tracker\n"
        "        self._token_tracker = TokenStatsTracker()"
    )

# 3c. Add token stats extraction after response parsing in proxy handler
# Find the spot after "model = rj.get("model", "")" and before MetricSnapshot
if "token_stats_result" not in ac:
    ac = ac.replace(
        '''                    model = rj.get("model", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

                snap = MetricSnapshot(''',
        '''                    model = rj.get("model", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

                # ── Token-level behavioral stats ──
                token_stats_result = {"repeat_ratio": 0, "unique_ratio": 0,
                                      "length_z": 0, "content_length": 0}
                try:
                    rj_stats = json.loads(resp_body)
                    token_stats_result = collector_ref._token_tracker.analyze(rj_stats)
                except Exception:
                    pass

                snap = MetricSnapshot('''
    )

# 3d. Add token stats fields to MetricSnapshot construction
if "repeat_ratio=token_stats_result" not in ac:
    ac = ac.replace(
        '''                    model=model,
                )
                collector_ref.push_metric(snap)''',
        '''                    model=model,
                    repeat_ratio=token_stats_result.get("repeat_ratio", 0),
                    unique_ratio=token_stats_result.get("unique_ratio", 0),
                    length_z=token_stats_result.get("length_z", 0),
                )
                collector_ref.push_metric(snap)'''
    )

# 3e. Add token stats to trust scoring
if "token_stats_ok" not in ac:
    ac = ac.replace(
        '''                # ── Fix 2 & 3: Verify response → update trust ──
                if trust_scorer is not None:
                    ok = verify_response_quality(status, resp_body)
                    if not ok:
                        trust_scorer.record_failed(agent_id, "mismatch")
                        log.info("VERIFY FAILED: agent=%s trust->%.3f",
                                 agent_id,
                                 trust_scorer.get_score(agent_id))
                    else:
                        trust_scorer.record_confirmed(agent_id)''',
        '''                # ── Fix 2 & 3: Verify response → update trust ──
                if trust_scorer is not None:
                    ok = verify_response_quality(status, resp_body)
                    if not ok:
                        trust_scorer.record_failed(agent_id, "mismatch")
                        log.info("VERIFY FAILED: agent=%s trust->%.3f",
                                 agent_id,
                                 trust_scorer.get_score(agent_id))
                    else:
                        # Token-level quality check (catches "smiling liar")
                        from src.w0.token_stats import TokenStatsTracker
                        ts_healthy, ts_reason = TokenStatsTracker.assess_quality(
                            token_stats_result)
                        if not ts_healthy:
                            trust_scorer.record_failed(agent_id, "mismatch")
                            log.info("TOKEN STATS FAIL: agent=%s reason=%s "
                                     "trust->%.3f", agent_id, ts_reason,
                                     trust_scorer.get_score(agent_id))
                        else:
                            trust_scorer.record_confirmed(agent_id)'''
    )

# 3f. Add token stats to event bus publish
if '"repeat_ratio"' not in ac:
    ac = ac.replace(
        '''                        "context": {"model": snap.model,
                                    "tokens": snap.total_tokens,
                                    "latency_s": snap.latency_s,
                                    "error": snap.error,
                                    "source": "proxy"},''',
        '''                        "context": {"model": snap.model,
                                    "tokens": snap.total_tokens,
                                    "latency_s": snap.latency_s,
                                    "error": snap.error,
                                    "source": "proxy",
                                    "repeat_ratio": snap.repeat_ratio,
                                    "unique_ratio": snap.unique_ratio,
                                    "length_z": snap.length_z},'''
    )

with open(ac_path, "w", encoding="utf-8") as f:
    f.write(ac)
print("[3/4] api_collector.py: integrated token stats")

# ── 4. Patch monitor_runner.py: record token stats in baseline + archive ──
mr_path = os.path.join(MONITOR, "src", "w3", "monitor_runner.py")
with open(mr_path, "r", encoding="utf-8") as f:
    mr = f.read()

if "repeat_ratio" not in mr:
    # Add token stats to baseline recording
    mr = mr.replace(
        '        self._baseline.record_metric("tool_calls", metric.tool_calls)',
        '        self._baseline.record_metric("tool_calls", metric.tool_calls)\n'
        '        self._baseline.record_metric("repeat_ratio", metric.repeat_ratio)\n'
        '        self._baseline.record_metric("unique_ratio", metric.unique_ratio)'
    )
    with open(mr_path, "w", encoding="utf-8") as f:
        f.write(mr)
    print("[4/4] monitor_runner.py: added token stats to baseline")
else:
    print("[4/4] monitor_runner.py: already patched")

print()
print("All patches applied successfully.")
