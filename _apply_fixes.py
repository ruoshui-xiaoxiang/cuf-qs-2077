"""Apply all 3 fixes to AI Agent Monitor.

Fix 1: Proxy reads REAL trust score from TrustScorer
Fix 2: Successful API calls trigger record_confirmed
Fix 3: Real response verification replaces 8% random mismatch
"""
import pathlib

# ═══════════════════════════════════════════════════════════
# Fix api_collector.py
# ═══════════════════════════════════════════════════════════
api_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\src\w0\api_collector.py")
code = api_path.read_text(encoding="utf-8")

# ── Fix A1: Add response verifier before LLMProxy class ──
old_llmproxy = 'class LLMProxy:'
new_llmproxy = '''def verify_response_quality(status: int, resp_body: bytes) -> bool:
    """Check if an API response indicates a successful, valid result.

    Returns True if the response looks good, False if it indicates
    an error, hallucination, or failure.
    """
    if status >= 400:
        return False
    try:
        text = resp_body.decode("utf-8", errors="replace")
    except Exception:
        return False
    if not text or len(text.strip()) < 10:
        return False
    # Scan for error keywords in response
    error_keywords = [
        '"error"', '"type": "error"', "traceback", "exception",
        "not found", "does not exist", "permission denied",
    ]
    text_lower = text.lower()
    if any(kw in text_lower for kw in error_keywords):
        return False
    # Check JSON structure for explicit error field
    try:
        rj = json.loads(text)
        if "error" in rj:
            return False
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return True


class LLMProxy:'''
code = code.replace(old_llmproxy, new_llmproxy)

# ── Fix A2: Replace proxy handler intervention + trust + verify ──
old_handler_body = '''            def do_POST(self) -> None:
                t0 = time.monotonic()
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len else b""

                # Intervention check (if layer is available)
                if intervention_ref is not None:
                    agent_id = collector_ref._agent_id
                    # We need trust score from somewhere; use a shared ref
                    # In proxy mode, trust is checked via collector
                    trust = getattr(collector_ref, '_proxy_trust_score', 1.0)
                    level = intervention_ref.check(agent_id, trust)
                    if level >= 3:  # circuit break
                        reject_body = json.dumps({
                            "error": {"message": "Circuit break: monitor "
                                     "detected untrustworthy behavior",
                                      "type": "monitor_circuit_break"}
                        }).encode()
                        self.send_response(429)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(reject_body)
                        return
                    if level >= 2:  # rate limit
                        time.sleep(intervention_ref._rate_limit_delay_s)

                # Forward to real API
                url = target + self.path
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={k: v for k, v in self.headers.items()
                             if k.lower() != "host"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        resp_body = resp.read()
                        status = resp.status
                except urllib.error.HTTPError as e:
                    status = e.code
                    resp_body = e.read() if e.fp else b""
                except Exception as e:
                    status = 502
                    resp_body = str(e).encode()

                latency = time.monotonic() - t0

                # Extract metrics from response
                tokens = 0
                model = ""
                try:
                    rj = json.loads(resp_body)
                    usage = rj.get("usage", {})
                    tokens = usage.get("total_tokens", 0)
                    model = rj.get("model", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

                snap = MetricSnapshot(
                    ts=ts_now(),
                    agent_id=collector_ref._agent_id,
                    total_tokens=tokens,
                    latency_s=round(latency, 3),
                    error=(status >= 400),
                    error_code=str(status) if status >= 400 else None,
                    model=model,
                )
                collector_ref.push_metric(snap)

                # Publish event directly to EventBus for UI visibility
                if collector_ref._bus is not None:
                    collector_ref._bus.publish({
                        "ts": ts_now(),
                        "level": "ok" if status < 400 else "alert",
                        "agent_id": collector_ref._agent_id_for_events,
                        "kind": "metric_ok" if status < 400 else "api_error",
                        "summary": (f"{snap.model} | {snap.total_tokens}tok "
                                    f"{snap.latency_s:.1f}s | proxy"),
                        "evidence_chain": {},
                        "context": {"model": snap.model,
                                    "tokens": snap.total_tokens,
                                    "latency_s": snap.latency_s,
                                    "error": snap.error,
                                    "source": "proxy"},
                    })

                # Forward response to client
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp_body)'''

new_handler_body = '''            def do_POST(self) -> None:
                t0 = time.monotonic()
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len else b""

                agent_id = collector_ref._agent_id
                trust_scorer = getattr(collector_ref, '_trust_scorer', None)
                intervention = intervention_ref

                # ── Fix 1: Read REAL trust score ──
                trust = 0.8  # default initial
                if trust_scorer is not None:
                    trust = trust_scorer.get_score(agent_id)

                # ── Intervention check ──
                if intervention is not None:
                    level = intervention.check(agent_id, trust)
                    if level >= 3:  # circuit break: REJECT
                        reject_body = json.dumps({
                            "error": {"message": "Circuit break: monitor "
                                     "detected untrustworthy behavior",
                                      "type": "monitor_circuit_break"}
                        }).encode()
                        self.send_response(429)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(reject_body)
                        log.info("CIRCUIT BREAK: agent=%s trust=%.3f",
                                 agent_id, trust)
                        return
                    if level >= 2:  # rate limit: enforce real delay
                        now = time.monotonic()
                        last = getattr(collector_ref, '_last_proxy_req_time', 0)
                        min_interval = 1.0 / max(
                            intervention._rate_limit_qps, 0.01)
                        elapsed = now - last
                        if elapsed < min_interval:
                            delay = min_interval - elapsed
                            time.sleep(delay)
                            log.info("RATE LIMIT: delay=%.2fs agent=%s",
                                     delay, agent_id)
                        collector_ref._last_proxy_req_time = time.monotonic()
                    elif level >= 1:  # inject: add correction to request
                        try:
                            req_json = json.loads(body)
                            correction = {
                                "role": "system",
                                "content": ("[Monitor Warning] Your recent "
                                    "outputs have not been fully verified. "
                                    "Please double-check your claims before "
                                    "proceeding.")
                            }
                            msgs = req_json.get("messages", [])
                            msgs.insert(0, correction)
                            req_json["messages"] = msgs
                            body = json.dumps(req_json).encode("utf-8")
                            log.info("INJECT: correction injected agent=%s "
                                     "trust=%.3f", agent_id, trust)
                        except (json.JSONDecodeError, Exception) as e:
                            log.debug("Inject failed: %s", e)

                # Forward to real API
                url = target + self.path
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={k: v for k, v in self.headers.items()
                             if k.lower() != "host"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        resp_body = resp.read()
                        status = resp.status
                except urllib.error.HTTPError as e:
                    status = e.code
                    resp_body = e.read() if e.fp else b""
                except Exception as e:
                    status = 502
                    resp_body = str(e).encode()

                latency = time.monotonic() - t0

                # Extract metrics from response
                tokens = 0
                model = ""
                try:
                    rj = json.loads(resp_body)
                    usage = rj.get("usage", {})
                    tokens = usage.get("total_tokens", 0)
                    model = rj.get("model", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

                snap = MetricSnapshot(
                    ts=ts_now(),
                    agent_id=collector_ref._agent_id,
                    total_tokens=tokens,
                    latency_s=round(latency, 3),
                    error=(status >= 400),
                    error_code=str(status) if status >= 400 else None,
                    model=model,
                )
                collector_ref.push_metric(snap)

                # ── Fix 2 & 3: Verify response → update trust ──
                if trust_scorer is not None:
                    ok = verify_response_quality(status, resp_body)
                    if not ok:
                        trust_scorer.record_failed(agent_id, "mismatch")
                        log.info("VERIFY FAILED: agent=%s trust->%.3f",
                                 agent_id,
                                 trust_scorer.get_score(agent_id))
                    else:
                        trust_scorer.record_confirmed(agent_id)

                # Publish event directly to EventBus for UI visibility
                if collector_ref._bus is not None:
                    collector_ref._bus.publish({
                        "ts": ts_now(),
                        "level": "ok" if status < 400 else "alert",
                        "agent_id": collector_ref._agent_id_for_events,
                        "kind": "metric_ok" if status < 400 else "api_error",
                        "summary": (f"{snap.model} | {snap.total_tokens}tok "
                                    f"{snap.latency_s:.1f}s | proxy"),
                        "evidence_chain": {},
                        "context": {"model": snap.model,
                                    "tokens": snap.total_tokens,
                                    "latency_s": snap.latency_s,
                                    "error": snap.error,
                                    "source": "proxy"},
                    })

                # Forward response to client
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp_body)'''

code = code.replace(old_handler_body, new_handler_body)
api_path.write_text(code, encoding="utf-8")
print(f"[OK] api_collector.py patched ({len(code)} bytes)")

# ═══════════════════════════════════════════════════════════
# Fix monitor_runner.py
# ═══════════════════════════════════════════════════════════
mr_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\src\w3\monitor_runner.py")
code = mr_path.read_text(encoding="utf-8")

# ── Fix M1: Wire trust_scorer + intervention to collector ──
old_init_end = '''        self._running = False
        self._round = 0
        self._interval = interval'''
new_init_end = '''        # Wire trust scorer and intervention layer to collector/proxy
        # so proxy can read REAL trust scores and apply interventions
        self._collector._trust_scorer = self._trust
        self._collector._proxy._intervention = self._intervention

        self._running = False
        self._round = 0
        self._interval = interval'''
code = code.replace(old_init_end, new_init_end)

# ── Fix M3: Replace random mismatch with response-based verification ──
old_random = '''        # Simulate occasional claim_mismatch for demo
        if random.random() < 0.08:
            self._trust.record_failed(agent_id, "mismatch")
            self._rules.record_event("claim_mismatch",
                                     f"File not found round {rid}")
            self._bus.publish({
                "ts": ts, "level": "alert",
                "agent_id": agent_id, "kind": "claim_mismatch",
                "summary": "Agent claimed file_write but file not found",
                "evidence_chain": {
                    "agent_claim": "Saved report to /data/output.csv",
                    "claim_type": "file_write",
                    "steps": [
                        {"layer": "api_receipt", "passed": True,
                         "detail": "HTTP 200"},
                        {"layer": "side_effect", "passed": False,
                         "detail": "File not found: /data/output.csv"},
                        {"layer": "causal_judgment", "passed": False,
                         "detail": "confirmation_status=failed"},
                    ],
                    "confirmation_status": "failed",
                    "outcome_semantics": "effect_refuted",
                },
                "context": {"round_id": rid, "model": metric.model,
                            "trust": self._trust.get_score(agent_id)},
            })'''
new_random = '''        # Response quality verification (Fix 3: real verification)
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
            pass'''
code = code.replace(old_random, new_random)

# ── Fix M2: Remove duplicate intervention apply from tick ──
# (Intervention now happens in proxy; tick only reports for UI)
old_intervention_apply = '''        # Intervention layer: check and record tokens
        trust_score = self._trust.get_score(agent_id)
        intervention_level = self._intervention.check(agent_id, trust_score)
        if intervention_level > 0:
            result = self._intervention.apply(
                agent_id, intervention_level, trust_score,
                evidence=chain_dict if chain_dict else None,
            )
            self._intervention.record_tokens(agent_id, metric.total_tokens)
            if not result["allowed"]:
                self._bus.publish({
                    "ts": ts_now(), "level": "alert",
                    "agent_id": agent_id,
                    "kind": f"intervention_{result['action']}",
                    "summary": result["record"].detail,
                    "evidence_chain": {},
                    "context": {"round_id": rid,
                                "intervention": result["record"].to_dict()},
                })
            elif result["action"] == "inject_correction":
                self._bus.publish({
                    "ts": ts_now(), "level": "warn",
                    "agent_id": agent_id,
                    "kind": "intervention_inject",
                    "summary": result["record"].detail,
                    "evidence_chain": {},
                    "context": {"round_id": rid,
                                "intervention": result["record"].to_dict()},
                })

        # Write detailed intervention log (JSONL)
        if intervention_level > 0:
            log_entry = {
                "ts": ts_now(),
                "round_id": rid,
                "agent_id": agent_id,
                "trust_score": round(trust_score, 4),
                "intervention_level": intervention_level,
                "action": result["action"],
                "allowed": result["allowed"],
                "delay_s": result.get("delay_s", 0),
                "tokens_this_round": metric.total_tokens,
                "cumulative_tokens": self._intervention._token_accumulator.get(agent_id, 0),
                "trust_level": self._trust.get_trust_level(agent_id),
                "rule_transitions": transition if intervention_level > 0 else None,
                "detail": result["record"].detail,
            }
            with self._intervention_log_lock:
                with open(self._intervention_log_path, "a", encoding="utf-8") as lf:
                    lf.write(json.dumps(log_entry, ensure_ascii=False) + "\\n")'''
new_intervention_apply = '''        # Intervention status report (actual intervention happens in proxy)
        # Tick only reads current state for UI display and archiving
        trust_score = self._trust.get_score(agent_id)
        intervention_level = self._intervention.check(agent_id, trust_score)
        self._intervention.record_tokens(agent_id, metric.total_tokens)

        # Publish intervention status event for UI
        if intervention_level > 0:
            level_names = {1: "inject", 2: "rate_limit", 3: "circuit_break"}
            self._bus.publish({
                "ts": ts_now(), "level": "warn",
                "agent_id": agent_id,
                "kind": f"intervention_status_{level_names.get(intervention_level, 'unknown')}",
                "summary": f"Trust={trust_score:.3f} Level={intervention_level}",
                "evidence_chain": {},
                "context": {"round_id": rid,
                            "trust": trust_score,
                            "level": intervention_level},
            })

        # Write intervention status log (JSONL)
        if intervention_level > 0:
            log_entry = {
                "ts": ts_now(),
                "round_id": rid,
                "agent_id": agent_id,
                "trust_score": round(trust_score, 4),
                "intervention_level": intervention_level,
                "action": f"status_{level_names.get(intervention_level, 'unknown')}",
                "allowed": True,
                "delay_s": 0,
                "tokens_this_round": metric.total_tokens,
                "cumulative_tokens": self._intervention._token_accumulator.get(agent_id, 0),
                "trust_level": self._trust.get_trust_level(agent_id),
                "rule_transitions": transition,
                "detail": f"Proxy-enforced intervention level {intervention_level}",
            }
            with self._intervention_log_lock:
                with open(self._intervention_log_path, "a", encoding="utf-8") as lf:
                    lf.write(json.dumps(log_entry, ensure_ascii=False) + "\\n")'''
code = code.replace(old_intervention_apply, new_intervention_apply)

mr_path.write_text(code, encoding="utf-8")
print(f"[OK] monitor_runner.py patched ({len(code)} bytes)")

# ═══════════════════════════════════════════════════════════
# Verify patches
# ═══════════════════════════════════════════════════════════
print("\n=== Verification ===")

# Check api_collector.py
api_code = api_path.read_text(encoding="utf-8")
checks = [
    ("verify_response_quality function", "def verify_response_quality" in api_code),
    ("trust_scorer reference", "trust_scorer = getattr(collector_ref, '_trust_scorer'" in api_code),
    ("real trust score reading", "trust = trust_scorer.get_score(agent_id)" in api_code),
    ("record_confirmed call", "trust_scorer.record_confirmed(agent_id)" in api_code),
    ("record_failed call", "trust_scorer.record_failed(agent_id" in api_code),
    ("real rate limit delay", "min_interval = 1.0 / max" in api_code),
    ("evidence injection in proxy", 'correction = {' in api_code),
    ("no more trust=1.0 default", "_proxy_trust_score', 1.0)" not in api_code),
]
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")

# Check monitor_runner.py
mr_code = mr_path.read_text(encoding="utf-8")
checks2 = [
    ("trust_scorer wired to collector", "_collector._trust_scorer = self._trust" in mr_code),
    ("intervention wired to proxy", "_collector._proxy._intervention = self._intervention" in mr_code),
    ("random mismatch removed", "random.random() < 0.08" not in mr_code),
    ("response verification added", "Response quality verification" in mr_code),
    ("duplicate apply removed", "self._intervention.apply(" not in mr_code),
    ("status reporting kept", "intervention_status_" in mr_code),
]
for name, ok in checks2:
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")

all_pass = all(ok for _, ok in checks + checks2)
print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}!")
