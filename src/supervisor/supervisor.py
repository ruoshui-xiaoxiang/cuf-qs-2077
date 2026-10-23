"""Supervisor core built from the project's four-layer pattern."""

import time
import uuid

from supervisor.policies import DefaultDecisionPolicy
from supervisor.rules import default_rules


class TaskSupervisor:
    """Observe -> validate -> decide -> emit audit entry."""

    def __init__(self, rules=None, policy=None, store=None):
        self.rules = list(rules or default_rules())
        self.policy = policy or DefaultDecisionPolicy()
        self.store = store

    def evaluate(self, spec, observation, history=None):
        history = list(history or [])
        violations = []
        for rule in self.rules:
            violations.extend(rule.check(spec, observation, history=history))
        decision = self.policy.decide(spec, observation, violations)
        entry = self._build_entry(spec, observation, decision, violations, history)
        if self.store is not None:
            self.store.append(entry)
        return entry

    def _build_entry(self, spec, observation, decision, violations, history):
        now = time.time()
        return {
            "evaluation_id": str(uuid.uuid4()),
            "task_id": spec.task_id,
            "timestamp": now,
            "w0": {
                "task_spec": spec.to_dict(),
                "observation": observation.to_dict(),
                "history_size": len(history),
            },
            "w1": {
                "violation_count": len(violations),
                "violations": [item.to_dict() for item in violations],
            },
            "w2": {
                "decision": decision.to_dict(),
            },
            "w3": {
                "control_action": decision.action,
                "status": decision.status,
                "confirmation_status": decision.confirmation_status,
                "next_step": decision.next_step,
                "owner": decision.owner,
            },
        }


def build_default_supervisor(log_path):
    """Factory kept small so the caller can wire it fast."""

    from supervisor.store import JsonlAuditStore

    return TaskSupervisor(store=JsonlAuditStore(log_path))
