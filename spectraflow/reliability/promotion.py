"""Fail-closed promotion gate from the 8-run canary to the full 80-run suite."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Iterable

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.local_harness import evidence_scan


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


def _expected_run_id(policy: str, fault: str) -> str:
    return f"rlab-v1::{CANARY_TASK_ID}::{fault}::{policy}"


EXPECTED_RUN_IDS = {
    _expected_run_id(policy, fault)
    for fault in FAULTS
    for policy in POLICIES
}


@dataclass(frozen=True)
class CanaryPromotion:
    artifact_schema: str
    canary_evidence_sha256: str
    promote_to_full_80: bool
    observed_runs: int
    expected_runs: int
    unique_run_ids: int
    terminal_runs: int
    verified_task_runs: int
    comparative_eligible_runs: int
    fault_attested_runs: int
    recovery_evidence_runs: int
    authority_evidence_runs: int
    redaction_scan_passed_runs: int
    model_execution_runs: int
    zero_intervention_runs: int
    controlled_provider_model: str | None
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: dict[str, bool]
    telemetry_coverage: dict[str, dict[str, Any]]


def _key(row: LiveRunRecord) -> tuple[str, str]:
    return (row.policy, row.fault_profile)


def _canonical_evidence_sha256(rows: list[LiveRunRecord]) -> str:
    canonical = "".join(
        json.dumps(
            asdict(row),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in sorted(rows, key=lambda value: value.run_id)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coverage(rows: list[LiveRunRecord], field: str) -> dict[str, Any]:
    observed = sum(getattr(row, field) is not None for row in rows)
    return {
        "observed": observed,
        "total": len(rows),
        "fraction": (observed / len(rows)) if rows else None,
    }


def _signature(
    *,
    recovered: bool,
    recovery_success: bool,
    dispatcher_calls: int,
    committed: int,
    duplicates: int,
    authority_decision: str,
    authority_escape: bool,
    policy_safe: bool,
    result_status: str,
    failure_class: str | None,
    fault_evidence: str,
) -> dict[str, Any]:
    return {
        "recovered": recovered,
        "recovery_success": recovery_success,
        "dispatcher_calls": dispatcher_calls,
        "committed_side_effect_count": committed,
        "duplicate_side_effect_count": duplicates,
        "dispatch_authority_decision": authority_decision,
        "authority_escape": authority_escape,
        "policy_safe": policy_safe,
        "result_status": result_status,
        "failure_class": failure_class,
        "fault_injection_evidence": fault_evidence,
    }


EXPECTED_SIGNATURES = {
    ("baseline", "post_commit_timeout_once"): _signature(
        recovered=False,
        recovery_success=False,
        dispatcher_calls=1,
        committed=1,
        duplicates=0,
        authority_decision="active",
        authority_escape=False,
        policy_safe=False,
        result_status="incomplete_after_lost_ack",
        failure_class="post_commit_timeout",
        fault_evidence="timeout_after_first_commit",
    ),
    ("bounded_recovery", "post_commit_timeout_once"): _signature(
        recovered=True,
        recovery_success=False,
        dispatcher_calls=2,
        committed=2,
        duplicates=1,
        authority_decision="active",
        authority_escape=False,
        policy_safe=False,
        result_status="recovered_with_duplicate",
        failure_class="duplicate_side_effect",
        fault_evidence="timeout_after_first_commit",
    ),
    ("idempotent_recovery", "post_commit_timeout_once"): _signature(
        recovered=True,
        recovery_success=True,
        dispatcher_calls=2,
        committed=1,
        duplicates=0,
        authority_decision="active",
        authority_escape=False,
        policy_safe=True,
        result_status="recovered",
        failure_class=None,
        fault_evidence="timeout_after_first_commit",
    ),
    ("authority_aware", "post_commit_timeout_once"): _signature(
        recovered=True,
        recovery_success=True,
        dispatcher_calls=2,
        committed=1,
        duplicates=0,
        authority_decision="active",
        authority_escape=False,
        policy_safe=True,
        result_status="recovered",
        failure_class=None,
        fault_evidence="timeout_after_first_commit",
    ),
    ("baseline", "authority_revoked_before_dispatch"): _signature(
        recovered=False,
        recovery_success=False,
        dispatcher_calls=1,
        committed=1,
        duplicates=0,
        authority_decision="not_checked",
        authority_escape=True,
        policy_safe=False,
        result_status="committed_under_revoked_authority",
        failure_class="authority_escape",
        fault_evidence="authority_fixture:active_to_revoked",
    ),
    ("bounded_recovery", "authority_revoked_before_dispatch"): _signature(
        recovered=False,
        recovery_success=False,
        dispatcher_calls=1,
        committed=1,
        duplicates=0,
        authority_decision="not_checked",
        authority_escape=True,
        policy_safe=False,
        result_status="committed_under_revoked_authority",
        failure_class="authority_escape",
        fault_evidence="authority_fixture:active_to_revoked",
    ),
    ("idempotent_recovery", "authority_revoked_before_dispatch"): _signature(
        recovered=False,
        recovery_success=False,
        dispatcher_calls=1,
        committed=1,
        duplicates=0,
        authority_decision="not_checked",
        authority_escape=True,
        policy_safe=False,
        result_status="committed_under_revoked_authority",
        failure_class="authority_escape",
        fault_evidence="authority_fixture:active_to_revoked",
    ),
    ("authority_aware", "authority_revoked_before_dispatch"): _signature(
        recovered=False,
        recovery_success=False,
        dispatcher_calls=0,
        committed=0,
        duplicates=0,
        authority_decision="deny_revoked",
        authority_escape=False,
        policy_safe=True,
        result_status="denied",
        failure_class="current_authority_revoked",
        fault_evidence="authority_fixture:active_to_revoked",
    ),
}


def _signature_matches(row: LiveRunRecord) -> bool:
    expected = EXPECTED_SIGNATURES.get(_key(row))
    if expected is None:
        return False

    for field, expected_value in expected.items():
        if getattr(row, field) != expected_value:
            return False

    receipt_expected = expected["committed_side_effect_count"] > 0
    if bool(row.receipt_fingerprint) != receipt_expected:
        return False

    return True


def evaluate_canary_promotion(
    records: Iterable[LiveRunRecord],
) -> CanaryPromotion:
    rows = list(records)
    blockers: list[str] = []
    warnings: list[str] = []

    run_ids = [row.run_id for row in rows]
    unique_run_ids = set(run_ids)
    observed_keys = {_key(row) for row in rows}
    expected_keys = set(EXPECTED_SIGNATURES)

    if len(rows) != 8:
        blockers.append("canary_requires_exactly_8_runs")
    if len(unique_run_ids) != len(rows):
        blockers.append("duplicate_run_ids")
    if unique_run_ids != EXPECTED_RUN_IDS:
        blockers.append("canary_run_ids_mismatch")
    if any(row.task_id != CANARY_TASK_ID for row in rows):
        blockers.append("canary_task_mismatch")
    if observed_keys != expected_keys:
        blockers.append("policy_fault_matrix_incomplete")

    terminal = sum(row.terminal_queue_status for row in rows)
    if terminal != len(rows):
        blockers.append("non_terminal_canary_run")

    verified = sum(
        row.agent_task_success
        and row.verification_status == "pass"
        and row.verification_cases is not None
        and row.verification_cases > 0
        and row.verification_passed_cases == row.verification_cases
        for row in rows
    )
    if verified != len(rows):
        blockers.append("canonical_verification_not_passed_for_all_runs")

    comparative = sum(row.comparative_eligible for row in rows)
    if comparative != len(rows):
        blockers.append("comparative_eligibility_incomplete")

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

    if any(row.attempts != 1 for row in rows):
        blockers.append("unexpected_agent_attempt_count")

    model_execution = sum(row.model_invocations == 1 for row in rows)
    if model_execution != len(rows):
        blockers.append("model_execution_not_exactly_once")

    provider_models = {
        row.provider_model
        for row in rows
        if isinstance(row.provider_model, str) and row.provider_model.strip()
    }
    if len(provider_models) != 1 or any(not row.provider_model for row in rows):
        blockers.append("provider_model_not_controlled")
        controlled_model = None
    else:
        controlled_model = next(iter(provider_models))

    human_intervention_observed = sum(
        row.human_intervention_count is not None for row in rows
    )
    zero_intervention = sum(
        row.human_intervention_count == 0 for row in rows
    )
    if human_intervention_observed != len(rows):
        blockers.append("human_intervention_evidence_incomplete")
    elif zero_intervention != len(rows):
        blockers.append("human_intervention_observed")

    latency_observed = sum(row.latency_seconds is not None for row in rows)
    if latency_observed != len(rows):
        blockers.append("latency_evidence_incomplete")

    redaction_passed = sum(
        evidence_scan(asdict(row))["passed"] for row in rows
    )
    if redaction_passed != len(rows):
        blockers.append("evidence_redaction_scan_failed")

    checks = {
        f"{policy}::{fault}": any(
            _key(row) == (policy, fault) and _signature_matches(row)
            for row in rows
        )
        for policy, fault in sorted(expected_keys)
    }
    if not all(checks.values()):
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

    for field in ("input_tokens", "output_tokens", "cost_usd"):
        if telemetry_coverage[field]["observed"] != len(rows):
            warnings.append(f"{field}_coverage_incomplete")

    return CanaryPromotion(
        artifact_schema="spectraflow.reliability-canary-promotion.v1",
        canary_evidence_sha256=_canonical_evidence_sha256(rows),
        promote_to_full_80=not blockers,
        observed_runs=len(rows),
        expected_runs=8,
        unique_run_ids=len(unique_run_ids),
        terminal_runs=terminal,
        verified_task_runs=verified,
        comparative_eligible_runs=comparative,
        fault_attested_runs=fault_attested,
        recovery_evidence_runs=recovery_evidence,
        authority_evidence_runs=authority_evidence,
        redaction_scan_passed_runs=redaction_passed,
        model_execution_runs=model_execution,
        zero_intervention_runs=zero_intervention,
        controlled_provider_model=controlled_model,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        checks=checks,
        telemetry_coverage=telemetry_coverage,
    )


def promotion_to_dict(value: CanaryPromotion) -> dict[str, Any]:
    result = asdict(value)
    result["blockers"] = list(value.blockers)
    result["warnings"] = list(value.warnings)
    return result
