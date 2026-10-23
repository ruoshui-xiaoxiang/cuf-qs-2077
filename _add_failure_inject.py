"""Add failure_inject_rate to config + proxy handler per-request logging."""
import json
import pathlib

# ── 1. Config: add failure_inject_rate ──
cfg_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\config\monitor_config.json")
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
cfg.setdefault("proxy", {})["failure_inject_rate"] = 0.0
cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
print("[OK] config: added failure_inject_rate=0.0")

# ── 2. Proxy handler: read failure_inject_rate + per-request JSONL log ──
ac_path = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\src\w0\api_collector.py")
code = ac_path.read_text(encoding="utf-8")

# 2a: LLMProxy.__init__ reads failure_inject_rate from proxy_config
old_init = '''    def __init__(self, host: str, port: int, target_base: str,
                 collector: APICollector,
                 intervention_layer=None):
        self._host = host
        self._port = port
        self._target_base = target_base
        self._collector = collector
        self._intervention = intervention_layer
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None'''

new_init = '''    def __init__(self, host: str, port: int, target_base: str,
                 collector: APICollector,
                 intervention_layer=None,
                 proxy_config: Optional[Dict] = None):
        self._host = host
        self._port = port
        self._target_base = target_base
        self._collector = collector
        self._intervention = intervention_layer
        self._failure_inject_rate = (proxy_config or {}).get(
            "failure_inject_rate", 0.0)
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None'''

code = code.replace(old_init, new_init)

# 2b: start_proxy passes proxy_config to LLMProxy
old_create = '''        self._proxy = LLMProxy(host, port, target_base, self,
                               intervention_layer=intervention_layer)'''
new_create = '''        self._proxy = LLMProxy(host, port, target_base, self,
                               intervention_layer=intervention_layer,
                               proxy_config=self._proxy_config)'''
code = code.replace(old_create, new_create)

# 2c: In Handler.start(), capture failure_inject_rate
old_start_locals = '''    def start(self) -> None:
        collector_ref = self._collector
        target = self._target_base
        intervention_ref = self._intervention'''
new_start_locals = '''    def start(self) -> None:
        collector_ref = self._collector
        target = self._target_base
        intervention_ref = self._intervention
        failure_rate = self._failure_inject_rate
        proxy_config = getattr(collector_ref, '_proxy_config', {})
        # Per-request detailed log (JSONL)
        import os as _os
        _req_log_path = _os.path.join(
            proxy_config.get("_log_dir", "logs"),
            "proxy_request_log.jsonl")
        _req_log_lock = threading.Lock()
        _req_counter = [0]  # mutable counter for closure'''
code = code.replace(old_start_locals, new_start_locals)

# 2d: Replace the trust verification block with failure injection + per-request log
old_verify = '''                # ── Fix 2 & 3: Verify response → update trust ──
                if trust_scorer is not None:
                    ok = verify_response_quality(status, resp_body)
                    if not ok:
                        trust_scorer.record_failed(agent_id, "mismatch")
                        log.info("VERIFY FAILED: agent=%s trust->%.3f",
                                 agent_id,
                                 trust_scorer.get_score(agent_id))
                    else:
                        trust_scorer.record_confirmed(agent_id)'''

new_verify = '''                # ── Fix 2 & 3: Verify response → update trust ──
                # With failure_inject_rate, randomly override verification
                # to simulate real failures (still uses real LLM response).
                trust_before = trust
                verify_ok = verify_response_quality(status, resp_body)
                injected_failure = False
                if failure_rate > 0 and verify_ok and random.random() < failure_rate:
                    verify_ok = False
                    injected_failure = True
                if trust_scorer is not None:
                    if not verify_ok:
                        trust_scorer.record_failed(agent_id, "mismatch")
                        log.info("VERIFY FAILED%s: agent=%s trust->%.3f",
                                 " (INJECTED)" if injected_failure else "",
                                 agent_id,
                                 trust_scorer.get_score(agent_id))
                    else:
                        trust_scorer.record_confirmed(agent_id)
                trust_after = (trust_scorer.get_score(agent_id)
                               if trust_scorer else trust)
                # Per-request intervention log (real proxy-level data)
                _req_counter[0] += 1
                _entry = {
                    "seq": _req_counter[0],
                    "ts": ts_now(),
                    "agent_id": agent_id,
                    "trust_before": round(trust_before, 4),
                    "trust_after": round(trust_after, 4),
                    "intervention_level": level if intervention else 0,
                    "action": ("circuit_break" if (intervention and level >= 3)
                               else "rate_limit" if (intervention and level >= 2)
                               else "inject" if (intervention and level >= 1)
                               else "forward"),
                    "tokens": tokens,
                    "latency_s": round(latency, 3),
                    "status": status,
                    "verify_ok": verify_ok,
                    "injected_failure": injected_failure,
                    "model": model,
                }
                with _req_log_lock:
                    with open(_req_log_path, "a", encoding="utf-8") as _lf:
                        _lf.write(json.dumps(_entry, ensure_ascii=False) + "\\n")'''

code = code.replace(old_verify, new_verify)

ac_path.write_text(code, encoding="utf-8")
print("[OK] proxy: failure injection + per-request logging")

# ── 3. Verify ──
code2 = ac_path.read_text(encoding="utf-8")
checks = [
    ("failure_inject_rate read from config",
     "failure_inject_rate" in code2 and "_failure_inject_rate" in code2),
    ("random override in handler",
     "random.random() < failure_rate" in code2),
    ("injected_failure flag",
     "injected_failure = True" in code2),
    ("per-request JSONL log",
     "proxy_request_log.jsonl" in code2),
    ("trust_before/trust_after tracked",
     "trust_before" in code2 and "trust_after" in code2),
]
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")
all_pass = all(ok for _, ok in checks)
print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}!")
