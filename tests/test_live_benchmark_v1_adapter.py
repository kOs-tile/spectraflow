import pytest

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.live_benchmark_adapter import (
    live_record_to_observation,
)


def _record(**overrides):
    values = {
        "run_id": "rlab-v1::task::post_commit_timeout_once::idempotent_recovery",
        "task_id": "task",
        "policy": "idempotent_recovery",
        "fault_profile": "post_commit_timeout_once",
        "queue_status": "completed",
        "terminal_queue_status": True,
        "verification_status": "pass",
        "verification_cases": 3,
        "verification_passed_cases": 3,
        "agent_task_success": True,
        "fault_injection_applied": True,
        "fault_injection_evidence": "injector:evidence",
        "attempts": 2,
        "model_invocations": 2,
        "recovered": True,
        "recovery_evidence_observed": True,
        "recovery_success": True,
        "dispatcher_calls": 2,
        "committed_side_effect_count": 1,
        "duplicate_side_effect_count": 0,
        "dispatch_authority_decision": "active",
        "authority_escape": False,
        "authority_escape_evidence_observed": True,
        "policy_safe": True,
        "comparative_eligible": True,
        "provider_model": "example-model",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.01,
        "human_intervention_count": 0,
        "latency_seconds": 1.5,
        "receipt_fingerprint": "sha256:receipt",
        "result_status": "recovered",
        "failure_class": None,
    }
    values.update(overrides)
    return LiveRunRecord(**values)


def test_safe_live_record_maps_without_inventing_tool_calls():
    observation = live_record_to_observation(_record())

    assert observation.recovery_policy.value == "idempotent_recovery"
    assert observation.failure_mode.value == "post_commit_timeout"
    assert observation.verified_success is True
    assert observation.safe_completion is True
    assert observation.tool_calls is None
    assert observation.dispatcher_calls == 2
    assert observation.duplicate_side_effects == 0
    assert observation.unauthorized_actions == 0
    assert observation.verification_failures == 0
    assert observation.latency_ms == 1500.0


def test_authority_escape_maps_to_unauthorized_action():
    observation = live_record_to_observation(
        _record(
            policy="bounded_recovery",
            fault_profile="authority_revoked_before_dispatch",
            run_id="rlab-v1::task::authority_revoked_before_dispatch::bounded_recovery",
            policy_safe=False,
            authority_escape=True,
            dispatch_authority_decision="not_checked",
        )
    )

    assert observation.failure_mode.value == "authority_revoked"
    assert observation.safe_completion is False
    assert observation.unauthorized_actions == 1


def test_missing_authority_and_duplicate_evidence_stays_unknown():
    observation = live_record_to_observation(
        _record(
            policy_safe=False,
            duplicate_side_effect_count=None,
            authority_escape=False,
            authority_escape_evidence_observed=False,
            human_intervention_count=None,
            verification_status=None,
            agent_task_success=False,
        )
    )

    assert observation.duplicate_side_effects is None
    assert observation.unauthorized_actions is None
    assert observation.human_interventions is None
    assert observation.verification_failures is None


def test_unknown_policy_fails_closed():
    with pytest.raises(ValueError, match="unsupported live policy"):
        live_record_to_observation(_record(policy="future_policy"))
