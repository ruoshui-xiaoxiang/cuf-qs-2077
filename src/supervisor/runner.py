"""Execution loop that connects adapter -> supervisor -> control decision."""

from typing import Any, Dict, List, Optional

from supervisor.models import TaskObservation


class SupervisedTaskRunner:
    """Run a task through the supervisor until a terminal decision is reached."""

    TERMINAL_ACTIONS = {"complete", "manual_review", "block"}

    def __init__(self, supervisor, adapter):
        self.supervisor = supervisor
        self.adapter = adapter

    def run(self, spec, context: Optional[Dict[str, Any]] = None):
        context = dict(context or {})
        evaluations: List[Dict[str, Any]] = []
        max_attempts = max(int(spec.max_attempts or 1), 1)

        for attempt in range(1, max_attempts + 1):
            result = self.adapter.run(spec, attempt=attempt, context=context)
            observation = TaskObservation(
                task_id=spec.task_id,
                state=result.state,
                attempt=attempt,
                output=result.output,
                errors=result.errors,
                used_tools=result.used_tools,
                duration_s=result.duration_s,
                evidence=result.evidence,
                metadata=result.metadata,
            )
            entry = self.supervisor.evaluate(
                spec,
                observation,
                history=[item["w0"]["observation"] for item in evaluations],
            )
            evaluations.append(entry)
            action = entry["w2"]["decision"]["action"]

            if action in self.TERMINAL_ACTIONS:
                return self._final_result(spec.task_id, evaluations)
            if action != "retry":
                return self._final_result(spec.task_id, evaluations)

        return self._final_result(spec.task_id, evaluations)

    def _final_result(self, task_id, evaluations):
        last = evaluations[-1] if evaluations else None
        return {
            "task_id": task_id,
            "attempts": len(evaluations),
            "evaluations": evaluations,
            "final_decision": last["w2"]["decision"] if last else None,
        }
