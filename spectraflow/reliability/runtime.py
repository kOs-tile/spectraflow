"""Normalized runtime-trace evidence for SPECTRAFLOW reliability analysis.

This module classifies already-recorded task metadata. It does not execute tasks,
infer hidden side effects, or treat a terminal process exit as semantic success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RuntimeTaskTrace:
    task_id: str
    status: str
    attempts: int
    model_invocations: int
    actor: str | None = None
    process_status: str | None = None
    process_exit_code: int | None = None
    acceptance_status: str | None = None
    final_status: str | None = None
    failure_phase: str | None = None
    failure: str | None = None
    started_at: str | None = None
    terminal_at: str | None = None


@dataclass(frozen=True)
class RuntimeTaskAssessment:
    outcome_class: str
    verified_pass: bool
    completed_without_acceptance_evidence: bool
    explicit_failure: bool
    pre_model_failure: bool
    post_model_failure: bool
    timeout_failure: bool
    duration_seconds: float | None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def runtime_trace_from_mapping(payload: dict[str, Any]) -> RuntimeTaskTrace:
    """Parse a normalized/sanitized task record into the trace contract."""
    return RuntimeTaskTrace(
        task_id=str(payload["task_id"]),
        status=str(payload["status"]),
        attempts=int(payload.get("attempts", 0)),
        model_invocations=int(payload.get("model_invocations", 0)),
        actor=payload.get("actor"),
        process_status=payload.get("process_status"),
        process_exit_code=payload.get("process_exit_code"),
        acceptance_status=payload.get("acceptance_status"),
        final_status=payload.get("final_status"),
        failure_phase=payload.get("failure_phase"),
        failure=payload.get("failure"),
        started_at=payload.get("started_at"),
        terminal_at=payload.get("terminal_at"),
    )


def assess_runtime_task(trace: RuntimeTaskTrace) -> RuntimeTaskAssessment:
    """Classify explicit runtime evidence without upgrading missing evidence."""

    acceptance = (trace.acceptance_status or "").lower()
    final_status = (trace.final_status or "").upper()
    status = trace.status.lower()
    failure_text = (trace.failure or "").lower()
    process_status = (trace.process_status or "").lower()

    verified_pass = (
        status == "completed"
        and (acceptance == "pass" or final_status == "PASS")
    )
    completed_without_acceptance = (
        status == "completed" and not verified_pass
    )
    explicit_failure = status == "failed"
    pre_model_failure = explicit_failure and trace.model_invocations == 0
    post_model_failure = explicit_failure and trace.model_invocations > 0
    timeout_failure = explicit_failure and (
        "timeout" in failure_text or process_status == "timed_out"
    )

    duration_seconds = None
    started = _parse_dt(trace.started_at)
    terminal = _parse_dt(trace.terminal_at)
    if started is not None and terminal is not None:
        duration_seconds = max(0.0, (terminal - started).total_seconds())

    if verified_pass:
        outcome_class = "verified_pass"
    elif completed_without_acceptance:
        outcome_class = "completed_unverified"
    elif explicit_failure:
        outcome_class = "failed"
    else:
        outcome_class = "non_terminal_or_unknown"

    return RuntimeTaskAssessment(
        outcome_class=outcome_class,
        verified_pass=verified_pass,
        completed_without_acceptance_evidence=completed_without_acceptance,
        explicit_failure=explicit_failure,
        pre_model_failure=pre_model_failure,
        post_model_failure=post_model_failure,
        timeout_failure=timeout_failure,
        duration_seconds=duration_seconds,
    )
