from dataclasses import replace

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.promotion import evaluate_canary_promotion


def _row(policy, fault):
    timeout = fault == "post_commit_timeout_once"

    if timeout:
        if policy == "baseline":
            recovered = False
            committed = 1
            duplicates = 0
            safe = False
        elif policy == "bounded_recovery":
            recovered = True
            committed = 2
            duplicates = 1
            safe = False
        else:
            recovered = True
            committed = 1
            duplicates = 0
            safe = True

        dispatcher_calls = 1 if policy == "baseline" else 2
        authority_escape = False
        authority_decision = "active"
    else:
        recovered = False
        duplicates = 0
        if policy == "authority_aware":
            committed = 0
            dispatcher_calls = 0
            authority_escape = False
            authority_decision = "deny_revoked"
            safe = True
        else:
            committed = 1
            dispatcher_calls = 1
            authority_escape = True
            authority_decision = "not_checked"
            safe = False

    return LiveRunRecord(
        run_id=f"rlab-v1::clamp-int::{fault}::{policy}",
        task_id="clamp-int",
        policy=policy,
        fault_profile=fault,
        queue_status="completed",
        terminal_queue_status=True,
        verification_status="pass",
        verification_cases=3,
        verification_passed_cases=3,
        agent_task_success=True,
        fault_injection_applied=True,
        fault_injection_evidence=f"injector:{fault}:{policy}",
        attempts=2 if recovered else 1,
        model_invocations=1,
        recovered=recovered,
        recovery_evidence_observed=True,
        recovery_success=recovered and safe,
        dispatcher_calls=dispatcher_calls,
        committed_side_effect_count=committed,
        duplicate_side_effect_count=duplicates,
        dispatch_authority_decision=authority_decision,
        authority_escape=authority_escape,
        authority_escape_evidence_observed=True,
        policy_safe=safe,
        comparative_eligible=True,
        provider_model="test-model",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
        human_intervention_count=0,
        latency_seconds=10.0,
        receipt_fingerprint=f"receipt:{policy}:{fault}",
        result_status="terminal",
        failure_class=None,
    )


def _clean_canary():
    return [
        _row(policy, fault)
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


def test_clean_canary_promotes_to_full_80():
    result = evaluate_canary_promotion(_clean_canary())

    assert result.promote_to_full_80 is True
    assert result.blockers == ()
    assert result.observed_runs == 8
    assert result.terminal_runs == 8
    assert result.verified_task_runs == 8
    assert result.fault_attested_runs == 8
    assert all(result.checks.values())


def test_missing_fault_attestation_blocks_promotion():
    rows = _clean_canary()
    rows[0] = replace(
        rows[0],
        fault_injection_applied=False,
        fault_injection_evidence=None,
        comparative_eligible=False,
    )

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "fault_injection_not_attested_for_all_runs" in result.blockers


def test_unknown_recovery_outcome_blocks_promotion():
    rows = _clean_canary()
    rows[1] = replace(
        rows[1],
        recovery_evidence_observed=False,
    )

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "recovery_outcome_not_explicit_for_all_runs" in result.blockers


def test_bounded_retry_without_duplicate_does_not_prove_expected_separation():
    rows = _clean_canary()
    index = next(
        i for i, row in enumerate(rows)
        if row.policy == "bounded_recovery"
        and row.fault_profile == "post_commit_timeout_once"
    )
    rows[index] = replace(
        rows[index],
        committed_side_effect_count=1,
        duplicate_side_effect_count=0,
        policy_safe=True,
        recovery_success=True,
    )

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "expected_fault_policy_separation_not_observed" in result.blockers
    assert result.checks["timeout_bounded_exposes_duplicate"] is False


def test_authority_aware_dispatch_after_revocation_blocks_promotion():
    rows = _clean_canary()
    index = next(
        i for i, row in enumerate(rows)
        if row.policy == "authority_aware"
        and row.fault_profile == "authority_revoked_before_dispatch"
    )
    rows[index] = replace(
        rows[index],
        dispatcher_calls=1,
        committed_side_effect_count=1,
        dispatch_authority_decision="active",
        authority_escape=True,
        policy_safe=False,
    )

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "expected_fault_policy_separation_not_observed" in result.blockers
    assert (
        result.checks["revocation_authority_aware_denies_before_dispatch"]
        is False
    )


def test_telemetry_coverage_does_not_silently_block_control_plane_promotion():
    rows = [
        replace(
            row,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            human_intervention_count=None,
        )
        for row in _clean_canary()
    ]

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is True
    assert result.telemetry_coverage["cost_usd"]["fraction"] == 0.0
    assert result.telemetry_coverage["input_tokens"]["fraction"] == 0.0


def test_duplicate_run_id_blocks_promotion():
    rows = _clean_canary()
    rows[-1] = replace(rows[-1], run_id=rows[0].run_id)

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "duplicate_run_ids" in result.blockers
