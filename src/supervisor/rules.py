"""Rule layer for the agent task supervisor."""

from typing import List

from supervisor.models import RuleViolation, TaskObservation, TaskSpec


class SupervisorRule:
    """Base rule contract."""

    rule_id = "rule.base"

    def check(
        self,
        spec: TaskSpec,
        observation: TaskObservation,
        history=None,
    ) -> List[RuleViolation]:
        raise NotImplementedError


class TimeoutRule(SupervisorRule):
    rule_id = "task.timeout"

    def check(self, spec, observation, history=None):
        if spec.timeout_s is None or observation.duration_s is None:
            return []
        if observation.duration_s <= spec.timeout_s:
            return []
        return [
            RuleViolation(
                rule_id=self.rule_id,
                severity="high",
                message="task runtime exceeded timeout",
                details={
                    "timeout_s": spec.timeout_s,
                    "duration_s": observation.duration_s,
                },
            )
        ]


class ForbiddenToolRule(SupervisorRule):
    rule_id = "task.forbidden_tool"

    def check(self, spec, observation, history=None):
        forbidden = sorted(set(spec.forbidden_tools) & set(observation.used_tools))
        if not forbidden:
            return []
        return [
            RuleViolation(
                rule_id=self.rule_id,
                severity="critical",
                message="forbidden tool used",
                details={"tools": forbidden},
            )
        ]


class MissingEvidenceRule(SupervisorRule):
    rule_id = "task.missing_evidence"

    def check(self, spec, observation, history=None):
        if observation.state != "completed" or not spec.required_evidence:
            return []
        confirmed = {
            item.evidence_type
            for item in observation.evidence
            if item.confirmed
        }
        missing = [name for name in spec.required_evidence if name not in confirmed]
        if not missing:
            return []
        return [
            RuleViolation(
                rule_id=self.rule_id,
                severity="high",
                message="required evidence missing",
                details={"missing": missing},
            )
        ]


class EmptyOutputRule(SupervisorRule):
    rule_id = "task.empty_output"

    def check(self, spec, observation, history=None):
        if observation.state != "completed":
            return []
        has_output = bool((observation.output or "").strip())
        has_evidence = bool(observation.evidence)
        if has_output or has_evidence:
            return []
        return [
            RuleViolation(
                rule_id=self.rule_id,
                severity="medium",
                message="task marked completed but produced no output",
                details={},
            )
        ]


class RetryBudgetRule(SupervisorRule):
    rule_id = "task.retry_budget"

    def check(self, spec, observation, history=None):
        if observation.state != "failed":
            return []
        if observation.attempt < spec.max_attempts:
            return []
        return [
            RuleViolation(
                rule_id=self.rule_id,
                severity="high",
                message="retry budget exhausted",
                details={
                    "attempt": observation.attempt,
                    "max_attempts": spec.max_attempts,
                },
            )
        ]


def default_rules():
    """Default MVP rule set."""

    return [
        TimeoutRule(),
        ForbiddenToolRule(),
        MissingEvidenceRule(),
        EmptyOutputRule(),
        RetryBudgetRule(),
    ]
