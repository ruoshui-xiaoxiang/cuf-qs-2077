"""Decision policy for the agent task supervisor."""

from supervisor.models import SupervisorDecision


class DefaultDecisionPolicy:
    """Small, explicit decision table for MVP."""

    def decide(self, spec, observation, violations):
        severities = {item.severity for item in violations}
        critical = "critical" in severities
        high = "high" in severities
        medium = "medium" in severities

        if critical:
            return SupervisorDecision(
                action="block",
                status="blocked",
                reason="critical rule violation detected",
                confirmation_status="unconfirmed",
                next_step="stop task and require operator review",
                owner="supervisor",
                violations=violations,
            )

        if observation.state == "failed":
            if high:
                return SupervisorDecision(
                    action="manual_review",
                    status="manual_review",
                    reason="task failed and cannot be auto-retried safely",
                    confirmation_status="unconfirmed",
                    next_step="handoff to human operator",
                    owner="human",
                    violations=violations,
                )
            return SupervisorDecision(
                action="retry",
                status="retry_scheduled",
                reason="task failed but remains inside retry budget",
                confirmation_status="unconfirmed",
                next_step="retry task with preserved context",
                owner="supervisor",
                violations=violations,
            )

        if observation.state == "completed":
            if high or medium:
                return SupervisorDecision(
                    action="manual_review",
                    status="manual_review",
                    reason="task claims completion but evidence is insufficient",
                    confirmation_status="unconfirmed",
                    next_step="request more evidence or operator confirmation",
                    owner="human",
                    violations=violations,
                )
            return SupervisorDecision(
                action="complete",
                status="completed",
                reason="task completed and passed supervision checks",
                confirmation_status="confirmed",
                next_step="advance to next task",
                owner="supervisor",
                violations=violations,
            )

        if observation.state in ("running", "pending"):
            return SupervisorDecision(
                action="allow",
                status=observation.state,
                reason="task is still in progress",
                confirmation_status="not_started",
                next_step="continue monitoring",
                owner="supervisor",
                violations=violations,
            )

        return SupervisorDecision(
            action="manual_review",
            status="manual_review",
            reason="unknown task state requires human review",
            confirmation_status="unconfirmed",
            next_step="inspect task state mapping",
            owner="human",
            violations=violations,
        )
