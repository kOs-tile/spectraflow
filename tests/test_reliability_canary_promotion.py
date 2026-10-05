from dataclasses import replace

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.live import simulate_control_plane
from spectraflow.reliability.promotion import (
    canary_run_specs,
    evaluate_canary_promotion,
)


def _record_for(run):
    expected = simulate_control_plane(run)
    authority_decision = (
        "deny_revoked"
        if expected.authority_denied
        else (
            "not_checked"
            if run.fault_profile == "authority_revoked_before_dispatch"
            else "active"
        )
    )
    return LiveRunRecord(
        run_id=run.run_id,
        task_id=run.task_id,
        policy=run.policy,
        fault_profile=run.fault_profile,
        queue_status="completed",
        terminal_queue_status=True,
        verification_status="pass",
        verification_cases=3,
        verification_passed_cases=3,
        agent_task_success=True,
        fault_injection_applied=True,
        fault_injection_evidence=f"injector:{run.fault_profile}",
        attempts=expected.attempts,
        model_invocations=1,
        recovered=expected.recovered,
        recovery_evidence_observed=True,
        recovery_success=expected.recovered and expected.policy_safe,
        dispatcher_calls=expected.dispatcher_calls,
        committed_side_effect_count=expected.committed_side_effects,
        duplicate_side_effect_count=expected.duplicate_side_effects,
        dispatch_authority_decision=authority_decision,
        authority_escape=expected.authority_escape,
        authority_escape_evidence_observed=True,
        policy_safe=expected.policy_safe,
        comparative_eligible=True,
        provider_model="example-model",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
        human_intervention_count=0,
        latency_seconds=2.0,
        receipt_fingerprint=(
            f"sha256:{run.run_id}"
            if expected.committed_side_effects > 0
            else None
        ),
        result_status="observed",
        failure_class=None,
    )


def _clean_records():
    return [_record_for(run) for run in canary_run_specs()]


def test_clean_canary_promotes_to_full_batch():
    result = evaluate_canary_promotion(_clean_records())

    assert result["observed_runs"] == 8
    assert result["unique_run_ids"] == 8
    assert result["exact_expected_run_set"] is True
    assert result["mismatches"] == {}
    assert result["blockers"] == []
    assert result["promoted"] is True
    assert result["telemetry_coverage"]["input_tokens"]["fraction"] == 1.0


def test_missing_canary_run_blocks_promotion():
    result = evaluate_canary_promotion(_clean_records()[:-1])

    assert result["promoted"] is False
    assert "canary_record_count_not_8" in result["blockers"]
    assert "canary_run_identity_set_mismatch" in result["blockers"]


def test_wrong_duplicate_effect_outcome_blocks_promotion():
    rows = _clean_records()
    index = next(
        i
        for i, row in enumerate(rows)
        if row.policy == "bounded_recovery"
        and row.fault_profile == "post_commit_timeout_once"
    )
    rows[index] = replace(
        rows[index],
        duplicate_side_effect_count=0,
        committed_side_effect_count=1,
        policy_safe=True,
        receipt_fingerprint="sha256:changed",
    )

    result = evaluate_canary_promotion(rows)

    assert result["promoted"] is False
    reasons = result["mismatches"][rows[index].run_id]
    assert "committed_effect_count_mismatch" in reasons
    assert "duplicate_effect_count_mismatch" in reasons
    assert "policy_safety_mismatch" in reasons


def test_missing_fault_attestation_blocks_promotion():
    rows = _clean_records()
    rows[0] = replace(
        rows[0],
        fault_injection_applied=False,
        fault_injection_evidence=None,
        comparative_eligible=False,
    )

    result = evaluate_canary_promotion(rows)

    assert result["promoted"] is False
    reasons = result["mismatches"][rows[0].run_id]
    assert "fault_injection_not_attested" in reasons
    assert "record_not_comparative_eligible" in reasons


def test_sparse_optional_cost_telemetry_does_not_fake_or_block_promotion():
    rows = _clean_records()
    rows[0] = replace(
        rows[0],
        cost_usd=None,
        human_intervention_count=None,
    )

    result = evaluate_canary_promotion(rows)

    assert result["promoted"] is True
    assert result["telemetry_coverage"]["cost_usd"]["observed"] == 7
    assert result["telemetry_coverage"]["human_intervention_count"]["observed"] == 7
