"""Patch monitor_runner.py to add intervention detailed logging."""
import pathlib

f = pathlib.Path(r'e:\QODERCN\ai_agent_monitor\src\w3\monitor_runner.py')
code = f.read_text(encoding='utf-8')

# 1. Add os import
code = code.replace(
    'import json\nimport logging\nimport random',
    'import json\nimport logging\nimport os\nimport random'
)

# 2. Add intervention log path after InterventionLayer init
old_block = (
    '        self._intervention = InterventionLayer(config.get("intervention", {}))\n'
    '\n'
    '        self._running = False'
)
new_block = (
    '        self._intervention = InterventionLayer(config.get("intervention", {}))\n'
    '        # Intervention detailed log (JSONL for research analysis)\n'
    '        self._intervention_log_path = os.path.join(log_dir, "intervention_log.jsonl")\n'
    '        self._intervention_log_lock = threading.Lock()\n'
    '\n'
    '        self._running = False'
)
code = code.replace(old_block, new_block)

# 2b. Initialize transition before the diffs loop
old_loop = '        # Publish diff events through W2 rules\n        for d in diffs:'
new_loop = '        # Publish diff events through W2 rules\n        transition = None\n        for d in diffs:'
code = code.replace(old_loop, new_loop)

# 3. Add intervention logging after the existing intervention publish blocks
# Find the section after intervention events are published and add JSONL logging
old_intervention_end = (
    '            elif result["action"] == "inject_correction":\n'
    '                self._bus.publish({\n'
    '                    "ts": ts_now(), "level": "warn",\n'
    '                    "agent_id": agent_id,\n'
    '                    "kind": "intervention_inject",\n'
    '                    "summary": result["record"].detail,\n'
    '                    "evidence_chain": {},\n'
    '                    "context": {"round_id": rid,\n'
    '                                "intervention": result["record"].to_dict()},\n'
    '                })'
)
new_intervention_end = (
    '            elif result["action"] == "inject_correction":\n'
    '                self._bus.publish({\n'
    '                    "ts": ts_now(), "level": "warn",\n'
    '                    "agent_id": agent_id,\n'
    '                    "kind": "intervention_inject",\n'
    '                    "summary": result["record"].detail,\n'
    '                    "evidence_chain": {},\n'
    '                    "context": {"round_id": rid,\n'
    '                                "intervention": result["record"].to_dict()},\n'
    '                })\n'
    '\n'
    '        # Write detailed intervention log (JSONL)\n'
    '        if intervention_level > 0:\n'
    '            log_entry = {\n'
    '                "ts": ts_now(),\n'
    '                "round_id": rid,\n'
    '                "agent_id": agent_id,\n'
    '                "trust_score": round(trust_score, 4),\n'
    '                "intervention_level": intervention_level,\n'
    '                "action": result["action"],\n'
    '                "allowed": result["allowed"],\n'
    '                "delay_s": result.get("delay_s", 0),\n'
    '                "tokens_this_round": metric.total_tokens,\n'
    '                "cumulative_tokens": self._intervention._token_accumulator.get(agent_id, 0),\n'
    '                "trust_level": self._trust.get_trust_level(agent_id),\n'
    '                "rule_transitions": transition if intervention_level > 0 else None,\n'
    '                "detail": result["record"].detail,\n'
    '            }\n'
    '            with self._intervention_log_lock:\n'
    '                with open(self._intervention_log_path, "a", encoding="utf-8") as lf:\n'
    '                    lf.write(json.dumps(log_entry, ensure_ascii=False) + "\\n")'
)
code = code.replace(old_intervention_end, new_intervention_end)

f.write_text(code, encoding='utf-8')
print('Patched monitor_runner.py with intervention logging')
