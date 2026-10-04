"""Reliability Lab corpus normalization and metric-coverage helpers.

The real-corpus layer is deliberately evidence-conservative:
- historical session status is not upgraded into semantic task success;
- queue completion is not upgraded into verified PASS without explicit acceptance;
- unavailable token/cost/side-effect/intervention fields remain unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any, Iterable


TERMINAL_FAILURE_STATUSES = {
    "failed",
    "blocked",
    "cancelled",
    "superseded",
}


@dataclass(frozen=True)
class Coverage:
    observed: int
    total: int
    fraction: float
    note: str


@dataclass(frozen=True)
class ExecutionControlAssessment:
    task_id: str
    status: str
    outcome_class: str
    verified_pass: bool
    completed_unverified: bool
    explicit_failure: bool
    blocked: bool
    unresolved: bool
    pre_model_failure: bool
    post_model_failure: bool
    timeout_failure: bool
    retry_count: int
    model_invocations: int
    attempts: int
    duration_seconds: float | None
    task_success_evaluable: bool


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assess_execution_control_task(
    row: dict[str, Any],
) -> ExecutionControlAssessment:
    status = str(row.get("status") or "unknown").lower()
    acceptance = str(row.get("acceptance_status") or "").lower()
    final_status = str(row.get("final_status") or "").upper()
    attempts = int(row.get("attempts") or 0)
    model_invocations = int(row.get("model_invocations") or 0)

    verified_pass = (
        status == "completed"
        and (acceptance == "pass" or final_status == "PASS")
    )
    completed_unverified = status == "completed" and not verified_pass
    explicit_failure = status == "failed"
    blocked = status == "blocked"
    unresolved = status in {"ready", "running", "queued", "unknown"}

    pre_model_failure = explicit_failure and model_invocations == 0
    post_model_failure = explicit_failure and model_invocations > 0
    timeout_failure = explicit_failure and bool(row.get("timeout_signal"))
    retry_count = max(0, attempts - 1)

    started = _parse_dt(row.get("started_at") or row.get("auto_started_at"))
    terminal = _parse_dt(
        row.get("completed_at")
        or row.get("failed_at")
        or row.get("blocked_at")
    )
    duration_seconds = None
    if started is not None and terminal is not None:
        duration_seconds = max(0.0, (terminal - started).total_seconds())

    task_success_evaluable = (
        status in TERMINAL_FAILURE_STATUSES
        or (
            status == "completed"
            and bool(row.get("acceptance_status") or row.get("final_status"))
        )
    )

    if verified_pass:
        outcome_class = "verified_pass"
    elif completed_unverified:
        outcome_class = "completed_unverified"
    elif explicit_failure:
        outcome_class = "failed"
    elif blocked:
        outcome_class = "blocked"
    elif unresolved:
        outcome_class = "unresolved"
    else:
        outcome_class = status

    return ExecutionControlAssessment(
        task_id=str(row.get("task_id")),
        status=status,
        outcome_class=outcome_class,
        verified_pass=verified_pass,
        completed_unverified=completed_unverified,
        explicit_failure=explicit_failure,
        blocked=blocked,
        unresolved=unresolved,
        pre_model_failure=pre_model_failure,
        post_model_failure=post_model_failure,
        timeout_failure=timeout_failure,
        retry_count=retry_count,
        model_invocations=model_invocations,
        attempts=attempts,
        duration_seconds=duration_seconds,
        task_success_evaluable=task_success_evaluable,
    )


def coverage(
    observed: int,
    total: int,
    note: str,
) -> Coverage:
    return Coverage(
        observed=observed,
        total=total,
        fraction=(observed / total) if total else 0.0,
        note=note,
    )


def numeric_summary(values: Iterable[float | int]) -> dict[str, float | int | None]:
    rows = list(values)
    if not rows:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "mean": None,
            "max": None,
        }
    return {
        "count": len(rows),
        "min": min(rows),
        "median": median(rows),
        "mean": sum(rows) / len(rows),
        "max": max(rows),
    }
