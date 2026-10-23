"""Fix proxy wiring: pass intervention_layer through start_proxy."""
import pathlib

# Fix api_collector.py: start_proxy accepts intervention_layer
api = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\src\w0\api_collector.py")
code = api.read_text(encoding="utf-8")

old = """    def start_proxy(self) -> None:
        \"\"\"Start the local LLM API proxy (non-blocking).\"\"\"
        if self._mock:
            log.info("Mock mode: proxy not started")
            return
        host = self._proxy_config.get("host", "127.0.0.1")
        port = self._proxy_config.get("port", 20780)
        target_base = self._proxy_config.get("target_base",
                                              "https://api.openai.com")
        self._proxy = LLMProxy(host, port, target_base, self)
        self._proxy.start()"""

new = """    def start_proxy(self, intervention_layer=None) -> None:
        \"\"\"Start the local LLM API proxy (non-blocking).\"\"\"
        if self._mock:
            log.info("Mock mode: proxy not started")
            return
        host = self._proxy_config.get("host", "127.0.0.1")
        port = self._proxy_config.get("port", 20780)
        target_base = self._proxy_config.get("target_base",
                                              "https://api.openai.com")
        self._proxy = LLMProxy(host, port, target_base, self,
                               intervention_layer=intervention_layer)
        self._proxy.start()"""

code = code.replace(old, new)
api.write_text(code, encoding="utf-8")
print("[OK] start_proxy accepts intervention_layer")

# Fix monitor_runner.py: pass intervention through start_proxy
mr = pathlib.Path(r"e:\QODERCN\ai_agent_monitor\src\w3\monitor_runner.py")
code = mr.read_text(encoding="utf-8")

# Fix the wire-up (remove broken _proxy._intervention)
old_wire = """        # Wire trust scorer and intervention layer to collector/proxy
        # so proxy can read REAL trust scores and apply interventions
        self._collector._trust_scorer = self._trust
        self._collector._proxy._intervention = self._intervention"""
new_wire = """        # Wire trust scorer to collector so proxy can read REAL trust scores
        self._collector._trust_scorer = self._trust"""
code = code.replace(old_wire, new_wire)

# Fix start_proxy call
old_start = "            self._collector.start_proxy()"
new_start = "            self._collector.start_proxy(intervention_layer=self._intervention)"
code = code.replace(old_start, new_start)

mr.write_text(code, encoding="utf-8")
print("[OK] monitor_runner passes intervention through start_proxy")

# Verify
api_code = api.read_text(encoding="utf-8")
mr_code = mr.read_text(encoding="utf-8")
checks = [
    ("start_proxy has intervention_layer param",
     "def start_proxy(self, intervention_layer=None)" in api_code),
    ("LLMProxy created with intervention_layer",
     "intervention_layer=intervention_layer)" in api_code),
    ("runner passes intervention to start_proxy",
     "start_proxy(intervention_layer=self._intervention)" in mr_code),
    ("no broken _proxy._intervention",
     "_proxy._intervention" not in mr_code),
    ("trust_scorer still wired",
     "_collector._trust_scorer = self._trust" in mr_code),
]
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}: {name}")

all_pass = all(ok for _, ok in checks)
print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}!")
