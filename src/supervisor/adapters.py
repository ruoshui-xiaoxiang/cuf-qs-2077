"""Execution adapters for supervised task running."""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from supervisor.models import TaskEvidence


@dataclass
class AdapterExecutionResult:
    """Normalized execution result produced by an adapter."""

    state: str
    output: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    used_tools: List[str] = field(default_factory=list)
    duration_s: Optional[float] = None
    evidence: List[TaskEvidence] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_observation_payload(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "output": self.output,
            "errors": list(self.errors),
            "used_tools": list(self.used_tools),
            "duration_s": self.duration_s,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
        }


class TaskExecutorAdapter:
    """Base adapter contract for supervised execution."""

    def run(self, spec, attempt: int, context=None) -> AdapterExecutionResult:
        raise NotImplementedError


class FunctionTaskAdapter(TaskExecutorAdapter):
    """Wrap a local callable as a task executor."""

    def __init__(self, func: Callable[..., Any]):
        self.func = func

    def run(self, spec, attempt: int, context=None) -> AdapterExecutionResult:
        started = time.perf_counter()
        try:
            raw = self.func(spec=spec, attempt=attempt, context=context or {})
        except Exception as exc:
            duration_s = time.perf_counter() - started
            return AdapterExecutionResult(
                state="failed",
                errors=[f"{type(exc).__name__}: {exc}"],
                duration_s=duration_s,
                metadata={"exception": type(exc).__name__},
            )
        duration_s = time.perf_counter() - started
        return self._normalize(raw, duration_s)

    def _normalize(self, raw: Any, duration_s: float) -> AdapterExecutionResult:
        if isinstance(raw, AdapterExecutionResult):
            if raw.duration_s is None:
                raw.duration_s = duration_s
            return raw
        if not isinstance(raw, dict):
            raise TypeError(
                "FunctionTaskAdapter expected dict or AdapterExecutionResult, "
                f"got {type(raw).__name__}"
            )
        evidence = []
        for item in raw.get("evidence", []) or []:
            if isinstance(item, TaskEvidence):
                evidence.append(item)
            elif isinstance(item, dict):
                evidence.append(
                    TaskEvidence(
                        evidence_type=item.get("evidence_type", "unknown"),
                        summary=item.get("summary", ""),
                        confirmed=bool(item.get("confirmed", False)),
                        payload=item.get("payload", {}) or {},
                    )
                )
        return AdapterExecutionResult(
            state=raw.get("state", "failed"),
            output=raw.get("output"),
            errors=list(raw.get("errors", []) or []),
            used_tools=list(raw.get("used_tools", []) or []),
            duration_s=raw.get("duration_s", duration_s),
            evidence=evidence,
            metadata=dict(raw.get("metadata", {}) or {}),
        )


class JsonSubprocessTaskAdapter(TaskExecutorAdapter):
    """Run an external process that emits a JSON task result to stdout."""

    def __init__(self, command, cwd=None, extra_env=None):
        self.command = list(command)
        self.cwd = cwd
        self.extra_env = dict(extra_env or {})

    def run(self, spec, attempt: int, context=None) -> AdapterExecutionResult:
        started = time.perf_counter()
        env = os.environ.copy()
        env.update(self.extra_env)
        env["SUPERVISOR_TASK_ID"] = spec.task_id
        env["SUPERVISOR_TASK_NAME"] = spec.name
        env["SUPERVISOR_TASK_ATTEMPT"] = str(attempt)

        try:
            completed = subprocess.run(
                self.command,
                cwd=self.cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=spec.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_s = time.perf_counter() - started
            return AdapterExecutionResult(
                state="failed",
                errors=[f"TimeoutExpired: {exc}"],
                used_tools=["subprocess"],
                duration_s=duration_s,
                metadata={"adapter": "json_subprocess", "timeout": True},
            )

        duration_s = time.perf_counter() - started
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        if completed.returncode != 0:
            return AdapterExecutionResult(
                state="failed",
                output=stdout or None,
                errors=[x for x in [stderr, f"returncode={completed.returncode}"] if x],
                used_tools=["subprocess"],
                duration_s=duration_s,
                metadata={
                    "adapter": "json_subprocess",
                    "returncode": completed.returncode,
                },
            )

        try:
            raw = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            return AdapterExecutionResult(
                state="failed",
                output=stdout or None,
                errors=[f"JSONDecodeError: {exc}"],
                used_tools=["subprocess"],
                duration_s=duration_s,
                metadata={"adapter": "json_subprocess", "invalid_json": True},
            )

        normalized = FunctionTaskAdapter(lambda **_: raw)._normalize(raw, duration_s)
        normalized.metadata.setdefault("adapter", "json_subprocess")
        if stderr:
            normalized.metadata["stderr"] = stderr
        if "subprocess" not in normalized.used_tools:
            normalized.used_tools.append("subprocess")
        return normalized
