from dataclasses import asdict, replace
import json


from benchmark.reliability_canary_promote import evaluate_collector_file
from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.promotion import evaluate_canary_promotion


def _row(policy: str, fault: str) -> LiveRunRecord:
    if fault == "post_commit_timeout_once":
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

        authority_escape = False
        authority_decision = "active"
        fault_evidence = "timeout_after_first_commit"
    else:
        recovered = False
        recovery_success = False
        duplicates = 0
        fault_evidence = "authority_fixture:active_to_revoked"
        if policy == "authority_aware":
            committed = 0
            dispatcher_calls = 0
            authority_escape = False
            authority_decision = "deny_revoked"
            safe = True
            result_status = "denied"
            failure_class = "current_authority_revoked"
        else:
            committed = 1
            dispatcher_calls = 1
            authority_escape = True
            authority_decision = "not_checked"
            safe = False
            result_status = "committed_under_revoked_authority"
            failure_class = "authority_escape"

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
        provider_model="controlled-test-model",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
        human_intervention_count=0,
        latency_seconds=10.0,
        receipt_fingerprint=(
            f"receipt:{policy}:{fault}" if committed > 0 else None
        ),
        result_status=result_status,
        failure_class=failure_class,
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
    assert result.artifact_schema == "spectraflow.reliability-canary-promotion.v1"
    assert len(result.canary_evidence_sha256) == 64
    assert result.blockers == ()
    assert result.warnings == ()
    assert result.observed_runs == 8
    assert result.unique_run_ids == 8
    assert result.terminal_runs == 8
    assert result.verified_task_runs == 8
    assert result.comparative_eligible_runs == 8
    assert result.fault_attested_runs == 8
    assert result.recovery_evidence_runs == 8
    assert result.authority_evidence_runs == 8
    assert result.redaction_scan_passed_runs == 8
    assert result.model_execution_runs == 8
    assert result.zero_intervention_runs == 8
    assert result.controlled_provider_model == "controlled-test-model"
    assert all(result.checks.values())


def test_promotion_artifact_binds_to_order_independent_canary_digest():
    rows = _clean_canary()

    forward = evaluate_canary_promotion(rows)
    reverse = evaluate_canary_promotion(list(reversed(rows)))

    assert forward.artifact_schema == "spectraflow.reliability-canary-promotion.v1"
    assert len(forward.canary_evidence_sha256) == 64
    assert forward.canary_evidence_sha256 == reverse.canary_evidence_sha256


def test_canary_evidence_mutation_changes_promotion_digest():
    rows = _clean_canary()
    original = evaluate_canary_promotion(rows)

    rows[0] = replace(rows[0], input_tokens=101)
    changed = evaluate_canary_promotion(rows)

    assert original.canary_evidence_sha256 != changed.canary_evidence_sha256


def test_wrong_run_identity_blocks_promotion():
    rows = _clean_canary()
    rows[0] = replace(rows[0], run_id="wrong-run-id")

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "canary_run_ids_mismatch" in result.blockers


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
    assert "comparative_eligibility_incomplete" in result.blockers


def test_bounded_retry_without_duplicate_blocks_policy_separation():
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
        result_status="recovered",
        failure_class=None,
    )

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "expected_fault_policy_separation_not_observed" in result.blockers
    assert result.checks[
        "bounded_recovery::post_commit_timeout_once"
    ] is False


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
        receipt_fingerprint="receipt:unexpected",
        result_status="committed_under_revoked_authority",
        failure_class="authority_escape",
    )

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "expected_fault_policy_separation_not_observed" in result.blockers


def test_agent_retry_or_multiple_model_invocations_blocks_promotion():
    rows = _clean_canary()
    rows[0] = replace(rows[0], attempts=2, model_invocations=2)

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "unexpected_agent_attempt_count" in result.blockers
    assert "model_execution_not_exactly_once" in result.blockers


def test_provider_model_must_be_controlled_across_all_arms():
    rows = _clean_canary()
    rows[0] = replace(rows[0], provider_model="different-model")

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "provider_model_not_controlled" in result.blockers
    assert result.controlled_provider_model is None


def test_human_intervention_blocks_autonomous_canary():
    rows = _clean_canary()
    rows[0] = replace(rows[0], human_intervention_count=1)

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "human_intervention_observed" in result.blockers


def test_missing_latency_blocks_live_instrumentation_gate():
    rows = _clean_canary()
    rows[0] = replace(rows[0], latency_seconds=None)

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "latency_evidence_incomplete" in result.blockers


def test_secret_like_evidence_blocks_promotion():
    rows = _clean_canary()
    rows[0] = replace(rows[0], provider_model="Bearer should-not-persist")

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "evidence_redaction_scan_failed" in result.blockers


def test_missing_token_and_cost_telemetry_warns_but_does_not_fake_zero():
    rows = [
        replace(
            row,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
        )
        for row in _clean_canary()
    ]

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is True
    assert result.telemetry_coverage["input_tokens"]["observed"] == 0
    assert result.telemetry_coverage["output_tokens"]["observed"] == 0
    assert result.telemetry_coverage["cost_usd"]["observed"] == 0
    assert result.warnings == (
        "input_tokens_coverage_incomplete",
        "output_tokens_coverage_incomplete",
        "cost_usd_coverage_incomplete",
    )


def test_missing_human_intervention_evidence_fails_closed():
    rows = _clean_canary()
    rows[0] = replace(rows[0], human_intervention_count=None)

    result = evaluate_canary_promotion(rows)

    assert result.promote_to_full_80 is False
    assert "human_intervention_evidence_incomplete" in result.blockers



def test_remote_collect_output_can_feed_promotion_directly(tmp_path):
    rows = _clean_canary()
    path = tmp_path / "canary-collect.json"
    path.write_text(
        json.dumps(
            {
                "bridge_version": "0.2.2",
                "statuses_collected": 8,
                "aggregate": {
                    "records": [asdict(row) for row in rows],
                },
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_collector_file(path)

    assert result["promote_to_full_80"] is True
    assert result["observed_runs"] == 8
    assert result["controlled_provider_model"] == "controlled-test-model"
