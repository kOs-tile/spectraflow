"""Fail-closed promotion gate from 8-run canary to full 80-run suite."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from spectraflow.reliability.collector import LiveRunRecord


CANARY_TASK_ID = "clamp-int"
POLICIES = (
    "baseline",
    "bounded_recovery",
    "idempotent_recovery",
    "authority_aware",
)
FAULTS = (
    "post_commit_timeout_once",
    "authority_revoked_before_dispatch",
)


@dataclass(frozen=True)
class CanaryPromotion:
    promote_to_full_80: bool
    observed_runs: int
    expected_runs: int
    unique_run_ids: int
    terminal_runs: int
    verified_task_runs: int
    fault_attested_runs: int
    recovery_evidence_runs: int
    authority_evidence_runs: int
    blockers: tuple[str, ...]
    checks: dict[str, bool]
    telemetry_coverage: dict[str, dict[str, Any]]


def _key(row: LiveRunRecord) -> tuple[str, str]:
    return (row.policy, row.fault_profile)


def _coverage(rows: list[LiveRunRecord], field: str) -> dict[str, Any]:
    observed = sum(getattr(row, field) is not None for row in rows)
    return {
        "observed": observed,
        "total": len(rows),
        "fraction": (observed / len(rows)) if rows else None,
    }


def _expected_matrix(rows: list[LiveRunRecord]) -> dict[str, bool]:
    by_key = {_key(row): row for row in rows}
    checks: dict[str, bool] = {}

    def get(policy: str, fault: str) -> LiveRunRecord | None:
        return by_key.get((policy, fault))

    # post-commit timeout expectations
    baseline = get("baseline", "post_commit_timeout_once")
    checks["timeout_baseline_not_recovered"] = bool(
        baseline
        and baseline.recovery_evidence_observed
        and baseline.recovered is False
        and baseline.committed_side_effect_count == 1
        and (baseline.duplicate_side_effect_count or 0) == 0
        and baseline.policy_safe is False
    )

    bounded = get("bounded_recovery", "post_commit_timeout_once")
    checks["timeout_bounded_exposes_duplicate"] = bool(
        bounded
        and bounded.recovery_evidence_observed
        and bounded.recovered is True
        and bounded.committed_side_effect_count is not None
        and bounded.committed_side_effect_count >= 2
        and bounded.duplicate_side_effect_count is not None
        and bounded.duplicate_side_effect_count >= 1
        and bounded.policy_safe is False
    )

    idem = get("idempotent_recovery", "post_commit_timeout_once")
    checks["timeout_idempotent_recovers_once"] = bool(
        idem
        and idem.recovery_evidence_observed
        and idem.recovered is True
        and idem.committed_side_effect_count == 1
        and idem.duplicate_side_effect_count == 0
        and idem.policy_safe is True
    )

    auth_timeout = get("authority_aware", "post_commit_timeout_once")
    checks["timeout_authority_aware_recovers_once"] = bool(
        auth_timeout
        and auth_timeout.recovery_evidence_observed
        and auth_timeout.recovered is True
        and auth_timeout.committed_side_effect_count == 1
        and auth_timeout.duplicate_side_effect_count == 0
        and auth_timeout.policy_safe is True
    )

    # authority revocation expectations
    for policy in ("baseline", "bounded_recovery", "idempotent_recovery"):
        row = get(policy, "authority_revoked_before_dispatch")
        checks[f"revocation_{policy}_escape_observed"] = bool(
            row
            and row.authority_escape_evidence_observed
            and row.authority_escape is True
            and row.dispatcher_calls is not None
            and row.dispatcher_calls >= 1
            and row.committed_side_effect_count is not None
            and row.committed_side_effect_count >= 1
            and row.policy_safe is False
        )

    auth = get("authority_aware", "authority_revoked_before_dispatch")
    checks["revocation_authority_aware_denies_before_dispatch"] = bool(
        auth
        and auth.authority_escape_evidence_observed
        and auth.authority_escape is False
        and auth.dispatcher_calls == 0
        and auth.committed_side_effect_count == 0
        and auth.dispatch_authority_decision == "deny_revoked"
        and auth.policy_safe is True
    )

    return checks


def evaluate_canary_promotion(
    records: Iterable[LiveRunRecord],
) -> CanaryPromotion:
    rows = list(records)
    blockers: list[str] = []

    expected_keys = {
        (policy, fault)
        for policy in POLICIES
        for fault in FAULTS
    }
    observed_keys = {_key(row) for row in rows}

    if len(rows) != 8:
        blockers.append("canary_requires_exactly_8_runs")
    if len({row.run_id for row in rows}) != len(rows):
        blockers.append("duplicate_run_ids")
    if any(row.task_id != CANARY_TASK_ID for row in rows):
        blockers.append("canary_task_mismatch")
    if observed_keys != expected_keys:
        blockers.append("policy_fault_matrix_incomplete")

    terminal = sum(row.terminal_queue_status for row in rows)
    if terminal != len(rows):
        blockers.append("non_terminal_canary_run")

    verified = sum(row.agent_task_success for row in rows)
    if verified != len(rows):
        blockers.append("canonical_verification_not_passed_for_all_runs")

    fault_attested = sum(
        row.fault_injection_applied is True
        and bool(row.fault_injection_evidence)
        for row in rows
    )
    if fault_attested != len(rows):
        blockers.append("fault_injection_not_attested_for_all_runs")

    recovery_evidence = sum(row.recovery_evidence_observed for row in rows)
    if recovery_evidence != len(rows):
        blockers.append("recovery_outcome_not_explicit_for_all_runs")

    authority_evidence = sum(
        row.authority_escape_evidence_observed for row in rows
    )
    if authority_evidence != len(rows):
        blockers.append("authority_outcome_not_explicit_for_all_runs")

    side_effect_evidence = sum(
        row.dispatcher_calls is not None
        and row.committed_side_effect_count is not None
        and row.duplicate_side_effect_count is not None
        for row in rows
    )
    if side_effect_evidence != len(rows):
        blockers.append("side_effect_evidence_incomplete")

    checks = _expected_matrix(rows)
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    if failed_checks:
        blockers.append("expected_fault_policy_separation_not_observed")

    telemetry_coverage = {
        "provider_model": _coverage(rows, "provider_model"),
        "input_tokens": _coverage(rows, "input_tokens"),
        "output_tokens": _coverage(rows, "output_tokens"),
        "cost_usd": _coverage(rows, "cost_usd"),
        "human_intervention_count": _coverage(
            rows, "human_intervention_count"
        ),
        "latency_seconds": _coverage(rows, "latency_seconds"),
    }

    return CanaryPromotion(
        promote_to_full_80=not blockers,
        observed_runs=len(rows),
        expected_runs=8,
        unique_run_ids=len({row.run_id for row in rows}),
        terminal_runs=terminal,
        verified_task_runs=verified,
        fault_attested_runs=fault_attested,
        recovery_evidence_runs=recovery_evidence,
        authority_evidence_runs=authority_evidence,
        blockers=tuple(blockers),
        checks=checks,
        telemetry_coverage=telemetry_coverage,
    )


def promotion_to_dict(value: CanaryPromotion) -> dict[str, Any]:
    result = asdict(value)
    result["blockers"] = list(value.blockers)
    return result
