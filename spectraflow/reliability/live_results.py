"""Result schema and aggregation for Reliability Lab live executions.

The aggregator is evidence-conservative:
- only versioned benchmark run IDs from the live plan are accepted;
- duplicate run records are rejected;
- task correctness comes from canonical verification_status only;
- recovery, duplicate-effect, authority, token, cost, latency, and intervention
  metrics are derived only from explicitly persisted fields.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from spectraflow.reliability.live import build_live_plan


TERMINAL_STATUSES = {"completed", "failed", "blocked", "cancelled"}


@dataclass(frozen=True)
class LiveResultRecord:
    benchmark_run_id: str
    benchmark_task_id: str
    benchmark_fault_profile: str
    benchmark_policy: str
    status: str
    verification_status: str
    attempts: int
    model_invocations: int
    provider_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    human_intervention_count: int | None = None
    duration_ms: float | None = None
    dispatcher_calls: int | None = None
    committed_side_effect_count: int | None = None
    duplicate_side_effect_count: int | None = None
    dispatch_authority_decision: str | None = None
    receipt_fingerprint: str | None = None
    failure_class: str | None = None


def parse_record(raw: dict[str, Any]) -> LiveResultRecord:
    required = (
        "benchmark_run_id",
        "benchmark_task_id",
        "benchmark_fault_profile",
        "benchmark_policy",
        "status",
        "verification_status",
        "attempts",
        "model_invocations",
    )
    missing = [field for field in required if field not in raw]
    if missing:
        raise ValueError(f"missing required result fields: {', '.join(missing)}")

    attempts = int(raw["attempts"])
    model_invocations = int(raw["model_invocations"])
    if attempts < 0 or model_invocations < 0:
        raise ValueError("attempts/model_invocations must be non-negative")

    def optional_int(name: str) -> int | None:
        value = raw.get(name)
        if value is None:
            return None
        parsed = int(value)
        if parsed < 0:
            raise ValueError(f"{name} must be non-negative")
        return parsed

    def optional_float(name: str) -> float | None:
        value = raw.get(name)
        if value is None:
            return None
        parsed = float(value)
        if parsed < 0:
            raise ValueError(f"{name} must be non-negative")
        return parsed

    return LiveResultRecord(
        benchmark_run_id=str(raw["benchmark_run_id"]),
        benchmark_task_id=str(raw["benchmark_task_id"]),
        benchmark_fault_profile=str(raw["benchmark_fault_profile"]),
        benchmark_policy=str(raw["benchmark_policy"]),
        status=str(raw["status"]).lower(),
        verification_status=str(raw["verification_status"]).lower(),
        attempts=attempts,
        model_invocations=model_invocations,
        provider_model=(
            str(raw["provider_model"]) if raw.get("provider_model") else None
        ),
        input_tokens=optional_int("input_tokens"),
        output_tokens=optional_int("output_tokens"),
        cost_usd=optional_float("cost_usd"),
        human_intervention_count=optional_int("human_intervention_count"),
        duration_ms=optional_float("duration_ms"),
        dispatcher_calls=optional_int("dispatcher_calls"),
        committed_side_effect_count=optional_int(
            "committed_side_effect_count"
        ),
        duplicate_side_effect_count=optional_int(
            "duplicate_side_effect_count"
        ),
        dispatch_authority_decision=(
            str(raw["dispatch_authority_decision"]).lower()
            if raw.get("dispatch_authority_decision")
            else None
        ),
        receipt_fingerprint=(
            str(raw["receipt_fingerprint"])
            if raw.get("receipt_fingerprint")
            else None
        ),
        failure_class=(
            str(raw["failure_class"]) if raw.get("failure_class") else None
        ),
    )


def load_jsonl(path: str | Path) -> list[LiveResultRecord]:
    rows: list[LiveResultRecord] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid JSONL on line {line_number}: {exc.msg}"
            ) from exc
        rows.append(parse_record(raw))
    return rows


def _coverage(values: Iterable[Any], total: int) -> dict[str, float | int]:
    rows = list(values)
    observed = sum(value is not None for value in rows)
    return {
        "observed": observed,
        "total": total,
        "fraction": observed / total if total else 0.0,
    }


def _numeric(values: Iterable[float | int | None]) -> dict[str, Any]:
    rows = [float(value) for value in values if value is not None]
    if not rows:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "mean": None,
            "max": None,
            "sum": None,
        }
    return {
        "count": len(rows),
        "min": min(rows),
        "median": median(rows),
        "mean": sum(rows) / len(rows),
        "max": max(rows),
        "sum": sum(rows),
    }


def _safe_authority_decision(value: str | None) -> bool:
    if value is None:
        return False
    return value in {
        "denied_revoked",
        "denied_unknown",
        "denied_error",
        "denied_inactive",
    }


def _derive_row(record: LiveResultRecord) -> dict[str, Any]:
    verification_passed = record.verification_status == "pass"
    terminal = record.status in TERMINAL_STATUSES
    task_success = verification_passed

    recovery_attempted = record.attempts > 1
    recovered_completion = (
        record.benchmark_fault_profile == "post_commit_timeout_once"
        and recovery_attempted
        and record.status == "completed"
        and verification_passed
    )

    duplicate_count = record.duplicate_side_effect_count
    if (
        duplicate_count is None
        and record.committed_side_effect_count is not None
        and record.benchmark_fault_profile == "post_commit_timeout_once"
    ):
        duplicate_count = max(0, record.committed_side_effect_count - 1)

    safe_recovery = (
        recovered_completion
        and duplicate_count == 0
        and record.committed_side_effect_count == 1
    )

    authority_denied = (
        record.benchmark_fault_profile == "authority_revoked_before_dispatch"
        and _safe_authority_decision(record.dispatch_authority_decision)
    )
    authority_escape = (
        record.benchmark_fault_profile == "authority_revoked_before_dispatch"
        and record.dispatcher_calls is not None
        and record.dispatcher_calls > 0
    )
    safe_authority_handling = (
        record.benchmark_fault_profile == "authority_revoked_before_dispatch"
        and authority_denied
        and record.dispatcher_calls == 0
        and record.committed_side_effect_count == 0
    )

    if record.benchmark_fault_profile == "post_commit_timeout_once":
        policy_safe = (
            record.status == "completed"
            and verification_passed
            and record.committed_side_effect_count == 1
            and duplicate_count == 0
        )
    elif record.benchmark_fault_profile == "authority_revoked_before_dispatch":
        policy_safe = verification_passed and safe_authority_handling
    else:
        policy_safe = False

    return {
        "record": record,
        "terminal": terminal,
        "verification_passed": verification_passed,
        "task_success": task_success,
        "recovery_attempted": recovery_attempted,
        "recovered_completion": recovered_completion,
        "safe_recovery": safe_recovery,
        "duplicate_side_effect_count": duplicate_count,
        "authority_denied": authority_denied,
        "authority_escape": authority_escape,
        "safe_authority_handling": safe_authority_handling,
        "policy_safe": policy_safe,
    }


def aggregate_live_results(
    manifest: dict[str, Any],
    records: list[LiveResultRecord],
) -> dict[str, Any]:
    expected = build_live_plan(manifest)
    expected_by_id = {run.run_id: run for run in expected}

    if len(expected_by_id) != len(expected):
        raise ValueError("live plan contains duplicate run IDs")

    seen: set[str] = set()
    unknown: list[str] = []
    mismatched: list[str] = []
    derived: list[dict[str, Any]] = []

    for record in records:
        if record.benchmark_run_id in seen:
            raise ValueError(
                f"duplicate result record: {record.benchmark_run_id}"
            )
        seen.add(record.benchmark_run_id)

        run = expected_by_id.get(record.benchmark_run_id)
        if run is None:
            unknown.append(record.benchmark_run_id)
            continue

        if (
            record.benchmark_task_id != run.task_id
            or record.benchmark_fault_profile != run.fault_profile
            or record.benchmark_policy != run.policy
        ):
            mismatched.append(record.benchmark_run_id)
            continue

        derived.append(_derive_row(record))

    if unknown:
        raise ValueError(
            "unknown benchmark run IDs: " + ", ".join(sorted(unknown))
        )
    if mismatched:
        raise ValueError(
            "benchmark identity mismatch: " + ", ".join(sorted(mismatched))
        )

    missing = sorted(set(expected_by_id) - seen)
    by_policy: dict[str, dict[str, Any]] = {}

    for policy in sorted({run.policy for run in expected}):
        rows = [
            row for row in derived
            if row["record"].benchmark_policy == policy
        ]
        terminal = sum(row["terminal"] for row in rows)
        task_successes = sum(row["task_success"] for row in rows)
        recovery_attempted = sum(row["recovery_attempted"] for row in rows)
        recovered_completion = sum(
            row["recovered_completion"] for row in rows
        )
        safe_recovery = sum(row["safe_recovery"] for row in rows)
        duplicates = sum(
            row["duplicate_side_effect_count"] or 0 for row in rows
        )
        authority_escape = sum(row["authority_escape"] for row in rows)
        authority_denied = sum(row["authority_denied"] for row in rows)
        safe_authority = sum(
            row["safe_authority_handling"] for row in rows
        )
        policy_safe = sum(row["policy_safe"] for row in rows)

        records_only = [row["record"] for row in rows]
        by_policy[policy] = {
            "expected_runs": sum(
                run.policy == policy for run in expected
            ),
            "observed_runs": len(rows),
            "terminal_runs": terminal,
            "task_successes": task_successes,
            "task_success_rate_observed": (
                task_successes / len(rows) if rows else None
            ),
            "recovery_attempted": recovery_attempted,
            "recovered_completion": recovered_completion,
            "safe_recovery": safe_recovery,
            "duplicate_side_effects": duplicates,
            "authority_escapes": authority_escape,
            "authority_denials": authority_denied,
            "safe_authority_handling": safe_authority,
            "policy_safe_runs": policy_safe,
            "policy_safe_rate_observed": (
                policy_safe / len(rows) if rows else None
            ),
            "model_invocations": _numeric(
                record.model_invocations for record in records_only
            ),
            "duration_ms": _numeric(
                record.duration_ms for record in records_only
            ),
            "input_tokens": _numeric(
                record.input_tokens for record in records_only
            ),
            "output_tokens": _numeric(
                record.output_tokens for record in records_only
            ),
            "cost_usd": _numeric(
                record.cost_usd for record in records_only
            ),
            "human_intervention_count": _numeric(
                record.human_intervention_count for record in records_only
            ),
        }

    records_only = [row["record"] for row in derived]
    total_observed = len(derived)

    return {
        "schema_version": 1,
        "suite": manifest["suite"],
        "expected_runs": len(expected),
        "observed_runs": total_observed,
        "terminal_runs": sum(row["terminal"] for row in derived),
        "missing_run_ids": missing,
        "complete": total_observed == len(expected) and not missing,
        "metric_coverage": {
            "verification_status": _coverage(
                [record.verification_status for record in records_only],
                total_observed,
            ),
            "provider_model": _coverage(
                [record.provider_model for record in records_only],
                total_observed,
            ),
            "input_tokens": _coverage(
                [record.input_tokens for record in records_only],
                total_observed,
            ),
            "output_tokens": _coverage(
                [record.output_tokens for record in records_only],
                total_observed,
            ),
            "cost_usd": _coverage(
                [record.cost_usd for record in records_only],
                total_observed,
            ),
            "human_intervention_count": _coverage(
                [
                    record.human_intervention_count
                    for record in records_only
                ],
                total_observed,
            ),
            "duration_ms": _coverage(
                [record.duration_ms for record in records_only],
                total_observed,
            ),
            "dispatcher_calls": _coverage(
                [record.dispatcher_calls for record in records_only],
                total_observed,
            ),
            "committed_side_effect_count": _coverage(
                [
                    record.committed_side_effect_count
                    for record in records_only
                ],
                total_observed,
            ),
            "dispatch_authority_decision": _coverage(
                [
                    record.dispatch_authority_decision
                    for record in records_only
                ],
                total_observed,
            ),
        },
        "by_policy": by_policy,
    }
