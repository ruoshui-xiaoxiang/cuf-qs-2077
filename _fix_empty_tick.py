"""Fix: tick should not verify when no new API call happened."""
import pathlib

mr = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\src\w3\monitor_runner.py")
code = mr.read_text(encoding="utf-8")

# The problem: when no API call happened, metric.total_tokens=0 and metric.model=""
# This triggers the verification failure. Fix: only verify when there's actual data.
old = """        # Response quality verification (Fix 3: real verification)
        # Trust updates now happen in proxy handler per-request.
        # Here we check the latest metric for error signals.
        if metric.error or (metric.total_tokens == 0 and not metric.model):
            self._trust.record_failed(agent_id, "mismatch")
            self._rules.record_event("claim_mismatch",
                                     f"API error round {rid}")
            self._bus.publish({
                "ts": ts, "level": "alert",
                "agent_id": agent_id, "kind": "response_verify_failed",
                "summary": f"Response verification failed (error={metric.error})",
                "evidence_chain": {
                    "agent_claim": f"API call round {rid}",
                    "claim_type": "api_response",
                    "steps": [
                        {"layer": "api_receipt", "passed": not metric.error,
                         "detail": f"status={'error' if metric.error else 'ok'}"},
                        {"layer": "content_check", "passed": metric.total_tokens > 0,
                         "detail": f"tokens={metric.total_tokens}"},
                    ],
                    "confirmation_status": "failed",
                },
                "context": {"round_id": rid, "model": metric.model,
                            "trust": self._trust.get_score(agent_id)},
            })
        elif metric.total_tokens > 0:
            # Successful response with content - trust already updated in proxy
            pass"""

new = """        # Response quality verification (Fix 3: real verification)
        # Trust updates happen in proxy handler per-request.
        # Here we only check for API-level errors in the latest metric.
        # Skip if no API call has happened (tokens=0 and no model).
        if metric.total_tokens > 0 and metric.error:
            self._trust.record_failed(agent_id, "mismatch")
            self._rules.record_event("claim_mismatch",
                                     f"API error round {rid}")
            self._bus.publish({
                "ts": ts, "level": "alert",
                "agent_id": agent_id, "kind": "response_verify_failed",
                "summary": f"API returned error (code={metric.error_code})",
                "evidence_chain": {
                    "agent_claim": f"API call round {rid}",
                    "claim_type": "api_response",
                    "steps": [
                        {"layer": "api_receipt", "passed": False,
                         "detail": f"status={metric.error_code}"},
                        {"layer": "content_check", "passed": metric.total_tokens > 0,
                         "detail": f"tokens={metric.total_tokens}"},
                    ],
                    "confirmation_status": "failed",
                },
                "context": {"round_id": rid, "model": metric.model,
                            "trust": self._trust.get_score(agent_id)},
            })"""

code = code.replace(old, new)
mr.write_text(code, encoding="utf-8")
print("[OK] Fixed empty-tick false positive")

# Verify
code2 = mr.read_text(encoding="utf-8")
print("  PASS: no more 'not metric.model' check" if "not metric.model" not in code2 else "  FAIL")
print("  PASS: only checks metric.error with tokens>0" if "metric.total_tokens > 0 and metric.error" in code2 else "  FAIL")
