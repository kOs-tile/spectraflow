"""Statistical analysis for completed Reliability Lab live records.

The analyzer is evidence-aware:
- missing recovery/authority evidence is excluded from those denominators;
- task correctness and policy safety stay separate;
- rates include Wilson 95% confidence intervals;
- cost/token/intervention/latency report coverage before aggregates.
"""

from __future__ import annotations

import math
from statistics import mean, median
from typing import Any, Callable, Iterable

from spectraflow.reliability.collector import LiveRunRecord


Z95 = 1.959963984540054


def wilson_interval(successes: int, total: int, *, z: float = Z95) -> dict[str, float] | None:
    if total <= 0:
        return None
    p = successes / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    margin = (
        z
        * math.sqrt((p * (1 - p) + z2 / (4 * total)) / total)
        / denom
    )
    return {
        "low": max(0.0, center - margin),
        "high": min(1.0, center + margin),
    }


def _rate(successes: int, total: int) -> dict[str, Any]:
    return {
        "successes": successes,
        "observed": total,
        "rate": (successes / total) if total else None,
        "wilson95": wilson_interval(successes, total),
    }


def _observed_numeric(
    rows: list[LiveRunRecord],
    field: str,
) -> dict[str, Any]:
    values = [
        getattr(row, field)
        for row in rows
        if getattr(row, field) is not None
    ]
    numeric = [float(value) for value in values]
    return {
        "observed": len(numeric),
        "total_runs": len(rows),
        "coverage": (len(numeric) / len(rows)) if rows else None,
        "sum": sum(numeric) if numeric else None,
        "mean": mean(numeric) if numeric else None,
        "median": median(numeric) if numeric else None,
    }


def _summarize_group(rows: list[LiveRunRecord]) -> dict[str, Any]:
    terminal = [row for row in rows if row.terminal_queue_status]
    verification_observed = [
        row for row in terminal
        if row.verification_status in {"pass", "fail", "error"}
    ]
    comparative = [row for row in terminal if row.comparative_eligible]
    recovery_observed = [
        row for row in comparative if row.recovery_evidence_observed
    ]
    authority_observed = [
        row for row in comparative
        if row.authority_escape_evidence_observed
    ]
    duplicate_observed = [
        row for row in comparative
        if row.duplicate_side_effect_count is not None
    ]

    return {
        "runs": len(rows),
        "terminal": len(terminal),
        "terminal_coverage": (len(terminal) / len(rows)) if rows else None,
        "task_success": _rate(
            sum(row.agent_task_success for row in verification_observed),
            len(verification_observed),
        ),
        "verification_coverage": {
            "observed": len(verification_observed),
            "total_runs": len(rows),
            "coverage": (
                len(verification_observed) / len(rows)
                if rows else None
            ),
        },
        "comparative_policy_safety": _rate(
            sum(row.policy_safe for row in comparative),
            len(comparative),
        ),
        "comparative_eligible": len(comparative),
        "recovery": _rate(
            sum(row.recovered for row in recovery_observed),
            len(recovery_observed),
        ),
        "safe_recovery": _rate(
            sum(row.recovery_success for row in recovery_observed),
            len(recovery_observed),
        ),
        "recovery_evidence_coverage": {
            "observed": len(recovery_observed),
            "comparative_eligible": len(comparative),
            "coverage": (
                len(recovery_observed) / len(comparative)
                if comparative else None
            ),
        },
        "authority_escape": _rate(
            sum(row.authority_escape for row in authority_observed),
            len(authority_observed),
        ),
        "authority_evidence_coverage": {
            "observed": len(authority_observed),
            "comparative_eligible": len(comparative),
            "coverage": (
                len(authority_observed) / len(comparative)
                if comparative else None
            ),
        },
        "duplicate_effect_runs": _rate(
            sum((row.duplicate_side_effect_count or 0) > 0 for row in duplicate_observed),
            len(duplicate_observed),
        ),
        "duplicate_effect_total": sum(
            row.duplicate_side_effect_count or 0
            for row in duplicate_observed
        ),
        "model_invocations": {
            "sum": sum(row.model_invocations for row in rows),
            "mean": (
                mean(row.model_invocations for row in rows)
                if rows else None
            ),
        },
        "input_tokens": _observed_numeric(rows, "input_tokens"),
        "output_tokens": _observed_numeric(rows, "output_tokens"),
        "cost_usd": _observed_numeric(rows, "cost_usd"),
        "human_intervention": _observed_numeric(
            rows, "human_intervention_count"
        ),
        "latency_seconds": _observed_numeric(rows, "latency_seconds"),
    }


def _group(
    records: list[LiveRunRecord],
    key: Callable[[LiveRunRecord], str],
) -> dict[str, dict[str, Any]]:
    values = sorted({key(row) for row in records})
    return {
        value: _summarize_group(
            [row for row in records if key(row) == value]
        )
        for value in values
    }


def analyze_live_records(
    records: Iterable[LiveRunRecord],
    *,
    expected_runs: int = 80,
) -> dict[str, Any]:
    rows = list(records)

    by_policy = _group(rows, lambda row: row.policy)
    by_fault = _group(rows, lambda row: row.fault_profile)
    by_policy_fault = _group(
        rows,
        lambda row: f"{row.policy}::{row.fault_profile}",
    )

    run_ids = [row.run_id for row in rows]
    unique_run_ids = len(set(run_ids))
    duplicate_run_ids = len(run_ids) - unique_run_ids
    terminal = sum(row.terminal_queue_status for row in rows)

    return {
        "schema_version": 1,
        "expected_runs": expected_runs,
        "runs": len(rows),
        "unique_run_ids": unique_run_ids,
        "duplicate_run_ids": duplicate_run_ids,
        "terminal_runs": terminal,
        "complete_run_set": (
            len(rows) == expected_runs
            and unique_run_ids == expected_runs
            and duplicate_run_ids == 0
            and terminal == expected_runs
        ),
        "overall": _summarize_group(rows),
        "by_policy": by_policy,
        "by_fault_profile": by_fault,
        "by_policy_fault": by_policy_fault,
        "claim_boundary": {
            "missing_evidence_excluded_from_metric_denominators": True,
            "task_success_separate_from_policy_safety": True,
            "requested_fault_profile_not_counted_as_fault_evidence": True,
            "confidence_intervals": "Wilson 95%",
            "production_world_reliability_claim": False,
        },
    }
