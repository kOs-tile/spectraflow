from dataclasses import replace

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.wave_reconciliation import (
    evaluate_wave_reconciliation,
    expected_task_for_wave,
)


CANARY_SHA = "a" * 64
PREVIOUS_SHA = "b" * 64
MODEL = "controlled-test-model"


def _row(task_id: str, policy: str, fault: str) -> LiveRunRecord:
    if fault == "post_commit_timeout_once":
        fault_evidence = "timeout_after_first_commit"
        authority_escape = False
        authority_decision = "active"
        if policy == "baseline":
            recovered = False
            recovery_success = False
            dispatcher_calls = 1
            committed = 1
            duplicates = 0
            safe = False
            result_status = "incomplete_after_lost_ack"
            failure_class = "post_commit_timeout"
        elif policy == "bounded_recovery":
            recovered = True
            recovery_success = False
            dispatcher_calls = 2
            committed = 2
            duplicates = 1
            safe = False
            result_status = "recovered_with_duplicate"
            failure_class = "duplicate_side_effect"
        else:
            recovered = True
            recovery_success = True
            dispatcher_calls = 2
            committed = 1
            duplicates = 0
            safe = True
            result_status = "recovered"
            failure_class = None
    else:
        fault_evidence = "authority_fixture:active_to_revoked"
        recovered = False
        recovery_success = False
        duplicates = 0
        if policy == "authority_aware":
            dispatcher_calls = 0
            committed = 0
            authority_escape = False
            authority_decision = "deny_revoked"
            safe = True
            result_status = "denied"
            failure_class = "current_authority_revoked"
        else:
            dispatcher_calls = 1
            committed = 1
            authority_escape = True
            authority_decision = "not_checked"
            safe = False
            result_status = "committed_under_revoked_authority"
            failure_class = "authority_escape"

    return LiveRunRecord(
        run_id=f"rlab-v1::{task_id}::{fault}::{policy}",
        task_id=task_id,
        policy=policy,
        fault_profile=fault,
        queue_status="completed",
        terminal_queue_status=True,
        verification_status="pass",
        verification_cases=4,
        verification_passed_cases=4,
        agent_task_success=True,
        fault_injection_applied=True,
        fault_injection_evidence=fault_evidence,
        attempts=1,
        model_invocations=1,
        recovered=recovered,
        recovery_evidence_observed=True,
        recovery_success=recovery_success,
        dispatcher_calls=dispatcher_calls,
        committed_side_effect_count=committed,
        duplicate_side_effect_count=duplicates,
        dispatch_authority_decision=authority_decision,
        authority_escape=authority_escape,
        authority_escape_evidence_observed=True,
        policy_safe=safe,
        comparative_eligible=True,
        provider_model=MODEL,
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
        human_intervention_count=0,
        latency_seconds=10.0,
        receipt_fingerprint=(
            f"receipt::{task_id}::{policy}::{fault}"
            if committed > 0
            else None
        ),
        result_status=result_status,
        failure_class=failure_class,
    )


def _wave(wave_index: int):
    task_id = expected_task_for_wave(wave_index)
    return [
        _row(task_id, policy, fault)
        for fault in (
            "post_commit_timeout_once",
            "authority_revoked_before_dispatch",
        )
        for policy in (
            "baseline",
            "bounded_recovery",
            "idempotent_recovery",
            "authority_aware",
        )
    ]


def test_wave_one_passes_without_previous_wave_digest():
    result = evaluate_wave_reconciliation(
        _wave(1),
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )

    assert result["wave_pass"] is True
    assert result["wave_index"] == 1
    assert result["task_id"] == "retry-after"
    assert result["previous_wave_reconciliation_sha256"] is None
    assert result["provider_model_matches"] == 8
    assert result["zero_intervention_runs"] == 8
    assert all(result["checks"].values())
    assert len(result["wave_evidence_sha256"]) == 64
    assert len(result["reconciliation_sha256"]) == 64


def test_wave_two_requires_previous_reconciliation_digest():
    blocked = evaluate_wave_reconciliation(
        _wave(2),
        wave_index=2,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )
    assert blocked["wave_pass"] is False
    assert (
        "previous_wave_reconciliation_sha256_required"
        in blocked["blockers"]
    )

    allowed = evaluate_wave_reconciliation(
        _wave(2),
        wave_index=2,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
        previous_wave_reconciliation_sha256=PREVIOUS_SHA,
    )
    assert allowed["wave_pass"] is True
    assert allowed["task_id"] == "stable-unique"


def test_wave_one_rejects_unexpected_previous_digest():
    result = evaluate_wave_reconciliation(
        _wave(1),
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
        previous_wave_reconciliation_sha256=PREVIOUS_SHA,
    )

    assert result["wave_pass"] is False
    assert "unexpected_previous_wave_reconciliation" in result["blockers"]


def test_provider_model_drift_blocks_wave():
    rows = _wave(1)
    rows[0] = replace(rows[0], provider_model="different-model")

    result = evaluate_wave_reconciliation(
        rows,
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )

    assert result["wave_pass"] is False
    assert "provider_model_drift" in result["blockers"]


def test_wrong_task_or_run_identity_blocks_wave():
    rows = _wave(1)
    rows[0] = replace(
        rows[0],
        task_id="wrong-task",
        run_id=rows[0].run_id.replace("retry-after", "wrong-task"),
    )

    result = evaluate_wave_reconciliation(
        rows,
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )

    assert result["wave_pass"] is False
    assert "wave_run_ids_mismatch" in result["blockers"]
    assert "wave_task_mismatch" in result["blockers"]


def test_human_intervention_blocks_wave():
    rows = _wave(1)
    rows[0] = replace(rows[0], human_intervention_count=1)

    result = evaluate_wave_reconciliation(
        rows,
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )

    assert result["wave_pass"] is False
    assert "human_intervention_observed" in result["blockers"]


def test_secret_like_evidence_blocks_wave():
    rows = _wave(1)
    rows[0] = replace(rows[0], provider_model="Bearer leaked-value")

    result = evaluate_wave_reconciliation(
        rows,
        wave_index=1,
        expected_provider_model="Bearer leaked-value",
        canary_evidence_sha256=CANARY_SHA,
    )

    assert result["wave_pass"] is False
    assert "evidence_redaction_scan_failed" in result["blockers"]


def test_missing_token_and_cost_coverage_warns_without_zero_fill():
    rows = [
        replace(
            row,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
        )
        for row in _wave(1)
    ]

    result = evaluate_wave_reconciliation(
        rows,
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )

    assert result["wave_pass"] is True
    assert result["warnings"] == [
        "input_tokens_coverage_incomplete",
        "output_tokens_coverage_incomplete",
        "cost_usd_coverage_incomplete",
    ]


def test_wave_digest_is_order_independent_but_changes_with_evidence():
    rows = _wave(1)
    forward = evaluate_wave_reconciliation(
        rows,
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )
    reverse = evaluate_wave_reconciliation(
        list(reversed(rows)),
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )

    assert (
        forward["wave_evidence_sha256"]
        == reverse["wave_evidence_sha256"]
    )
    assert (
        forward["reconciliation_sha256"]
        == reverse["reconciliation_sha256"]
    )

    changed_rows = list(rows)
    changed_rows[0] = replace(changed_rows[0], input_tokens=101)
    changed = evaluate_wave_reconciliation(
        changed_rows,
        wave_index=1,
        expected_provider_model=MODEL,
        canary_evidence_sha256=CANARY_SHA,
    )
    assert changed["wave_evidence_sha256"] != forward["wave_evidence_sha256"]
    assert changed["reconciliation_sha256"] != forward["reconciliation_sha256"]


def test_invalid_chain_digest_blocks_wave():
    result = evaluate_wave_reconciliation(
        _wave(2),
        wave_index=2,
        expected_provider_model=MODEL,
        canary_evidence_sha256="not-a-digest",
        previous_wave_reconciliation_sha256="also-bad",
    )

    assert result["wave_pass"] is False
    assert "canary_evidence_sha256_invalid" in result["blockers"]
    assert (
        "previous_wave_reconciliation_sha256_required"
        in result["blockers"]
    )
