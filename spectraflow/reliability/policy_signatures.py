"""Canonical fault/policy signatures for Reliability Lab live v1."""

from __future__ import annotations

from typing import Any

from spectraflow.reliability.collector import LiveRunRecord


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


def expected_run_id(task_id: str, policy: str, fault: str) -> str:
    return f"rlab-v1::{task_id}::{fault}::{policy}"


def expected_run_ids(task_id: str) -> set[str]:
    return {
        expected_run_id(task_id, policy, fault)
        for fault in FAULTS
        for policy in POLICIES
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


def key_for_record(row: LiveRunRecord) -> tuple[str, str]:
    return (row.policy, row.fault_profile)


def record_matches_expected_signature(row: LiveRunRecord) -> bool:
    expected = EXPECTED_SIGNATURES.get(key_for_record(row))
    if expected is None:
        return False

    for field, expected_value in expected.items():
        if getattr(row, field) != expected_value:
            return False

    receipt_expected = expected["committed_side_effect_count"] > 0
    return bool(row.receipt_fingerprint) == receipt_expected
