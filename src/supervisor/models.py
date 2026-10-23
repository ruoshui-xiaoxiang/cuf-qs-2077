"""Agent task supervisor data models.

First version keeps the contract intentionally small:
- task spec
- runtime observation
- rule violation
- supervisor decision
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskEvidence:
    """Observed proof attached to a task run."""

    evidence_type: str
    summary: str
    confirmed: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskSpec:
    """Static task contract used by the supervisor."""

    task_id: str
    name: str
    max_attempts: int = 2
    timeout_s: Optional[float] = None
    required_evidence: List[str] = field(default_factory=list)
    forbidden_tools: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskObservation:
    """Runtime facts reported by an agent execution."""

    task_id: str
    state: str
    attempt: int = 1
    output: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    used_tools: List[str] = field(default_factory=list)
    duration_s: Optional[float] = None
    evidence: List[TaskEvidence] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data


@dataclass
class RuleViolation:
    """Rule engine output."""

    rule_id: str
    severity: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SupervisorDecision:
    """Control result returned by the supervisor."""

    action: str
    status: str
    reason: str
    confirmation_status: str
    next_step: Optional[str] = None
    owner: Optional[str] = None
    violations: List[RuleViolation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["violations"] = [item.to_dict() for item in self.violations]
        return data
