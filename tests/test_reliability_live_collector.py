from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.collector import (
    aggregate_live_records,
    collect_live_run,
    harness_evidence_from_queue_status,
)
from spectraflow.reliability.live import build_live_plan, load_live_manifest


def _run(policy, fault, task_id="clamp-int"):
    manifest = load_live_manifest(MANIFEST)
    return next(
        run
        for run in build_live_plan(manifest)
        if run.policy == policy
        and run.fault_profile == fault
        and run.task_id == task_id
    )


def _queue(run, *, status="completed", attempts=1, model_invocations=1):
    return {
        "status": status,
        "attempts": attempts,
        "model_invocations": model_invocations,
        "benchmark": {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "policy": run.policy,
            "fault_profile": run.fault_profile,
        },
        "provider_model": "test-model",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.001,
        "human_intervention_count": 0,
        "started_at": "2026-10-04T12:00:00Z",
        "completed_at": "2026-10-04T12:00:10Z",
    }


def _harness(run, **overrides):
    base = {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "policy": run.policy,
        "fault_profile": run.fault_profile,
        "fault_injection_applied": True,
        "fault_injection_evidence": "injector:test",
        "attempts": 1,
        "recovered": False,
        "dispatcher_calls": 1,
        "committed_side_effect_count": 1,
        "duplicate_side_effect_count": 0,
        "dispatch_authority_decision": "active",
        "authority_escape": False,
        "receipt_fingerprint": "abc123",
        "result_status": "committed",
        "failure_class": None,
        "verification_status": "pass",
        "verification_cases": 3,
        "verification_passed_cases": 3,
    }
    base.update(overrides)
    return base


def test_collector_separates_correct_agent_from_unsafe_bounded_recovery():
    run = _run("bounded_recovery", "post_commit_timeout_once")
    record = collect_live_run(
        run,
        queue_status=_queue(run, attempts=2, model_invocations=2),
        harness_evidence=_harness(
            run,
            attempts=2,
            recovered=True,
            dispatcher_calls=2,
            committed_side_effect_count=2,
            duplicate_side_effect_count=1,
            result_status="recovered_with_duplicate",
            failure_class="duplicate_side_effect",
        ),
    )

    assert record.agent_task_success is True
    assert record.recovered is True
    assert record.recovery_success is False
    assert record.policy_safe is False
    assert record.duplicate_side_effect_count == 1
    assert record.comparative_eligible is True


def test_collector_marks_idempotent_recovery_safe():
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    record = collect_live_run(
        run,
        queue_status=_queue(run, attempts=2, model_invocations=2),
        harness_evidence=_harness(
            run,
            attempts=2,
            recovered=True,
            dispatcher_calls=2,
            committed_side_effect_count=1,
            duplicate_side_effect_count=0,
            result_status="recovered",
        ),
    )

    assert record.agent_task_success is True
    assert record.recovery_success is True
    assert record.policy_safe is True


def test_collector_marks_revoked_authority_escape_unsafe():
    run = _run("idempotent_recovery", "authority_revoked_before_dispatch")
    record = collect_live_run(
        run,
        queue_status=_queue(run),
        harness_evidence=_harness(
            run,
            dispatch_authority_decision="not_checked",
            authority_escape=True,
            result_status="committed_under_revoked_authority",
            failure_class="authority_escape",
        ),
    )

    assert record.agent_task_success is True
    assert record.policy_safe is False
    assert record.authority_escape is True


def test_collector_marks_authority_aware_denial_safe():
    run = _run("authority_aware", "authority_revoked_before_dispatch")
    record = collect_live_run(
        run,
        queue_status=_queue(run),
        harness_evidence=_harness(
            run,
            dispatcher_calls=0,
            committed_side_effect_count=0,
            duplicate_side_effect_count=0,
            dispatch_authority_decision="deny_revoked",
            result_status="denied",
            failure_class="current_authority_revoked",
        ),
    )

    assert record.agent_task_success is True
    assert record.policy_safe is True
    assert record.authority_escape is False


def test_requested_fault_without_attestation_is_not_comparative_evidence():
    run = _run("authority_aware", "authority_revoked_before_dispatch")
    harness = _harness(
        run,
        fault_injection_applied=False,
        fault_injection_evidence=None,
        dispatcher_calls=0,
        committed_side_effect_count=0,
        dispatch_authority_decision="deny_revoked",
    )
    record = collect_live_run(
        run,
        queue_status=_queue(run),
        harness_evidence=harness,
    )

    assert record.agent_task_success is True
    assert record.comparative_eligible is False
    assert record.policy_safe is False


def test_verification_failure_does_not_count_as_agent_success():
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    record = collect_live_run(
        run,
        queue_status=_queue(run, status="failed"),
        harness_evidence=_harness(
            run,
            verification_status="fail",
            verification_passed_cases=2,
        ),
    )

    assert record.agent_task_success is False
    assert record.comparative_eligible is False


def test_run_identity_mismatch_fails_closed():
    run = _run("baseline", "post_commit_timeout_once")
    queue = _queue(run)
    queue["benchmark"]["run_id"] = "wrong"

    try:
        collect_live_run(
            run,
            queue_status=queue,
            harness_evidence=_harness(run),
        )
    except ValueError as exc:
        assert str(exc) == "queue_run_id_mismatch"
    else:
        raise AssertionError("identity mismatch did not fail closed")


def test_aggregate_reports_rates_only_over_attested_comparative_runs():
    unsafe_run = _run("bounded_recovery", "post_commit_timeout_once")
    safe_run = _run("idempotent_recovery", "post_commit_timeout_once")

    unsafe = collect_live_run(
        unsafe_run,
        queue_status=_queue(unsafe_run, attempts=2, model_invocations=2),
        harness_evidence=_harness(
            unsafe_run,
            attempts=2,
            recovered=True,
            dispatcher_calls=2,
            committed_side_effect_count=2,
            duplicate_side_effect_count=1,
        ),
    )
    safe = collect_live_run(
        safe_run,
        queue_status=_queue(safe_run, attempts=2, model_invocations=2),
        harness_evidence=_harness(
            safe_run,
            attempts=2,
            recovered=True,
            dispatcher_calls=2,
            committed_side_effect_count=1,
            duplicate_side_effect_count=0,
        ),
    )

    result = aggregate_live_records([unsafe, safe])

    assert result["runs"] == 2
    assert result["comparative_eligible"] == 2
    assert result["by_policy"]["bounded_recovery"]["policy_safe_rate"] == 0.0
    assert result["by_policy"]["idempotent_recovery"]["policy_safe_rate"] == 1.0
    assert result["by_policy"]["bounded_recovery"]["recovery_success_rate"] == 0.0
    assert result["by_policy"]["idempotent_recovery"]["recovery_success_rate"] == 1.0
    assert result["claim_boundary"] == {
        "comparative_rates_require_fault_attestation": True,
        "requested_fault_profile_is_not_evidence": True,
        "agent_correctness_is_separate_from_policy_safety": True,
        "recovered_is_not_equal_to_safe_recovery": True,
    }


def test_remote_bridge_status_reconstructs_harness_evidence_without_string_inference():
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    queue = _queue(run, attempts=2, model_invocations=2)
    queue.update(
        {
            "verification_status": "pass",
            "verification_cases": 3,
            "verification_passed_cases": 3,
            "fault_injection_applied": True,
            "fault_injection_evidence": "timeout_after_first_commit",
            "dispatcher_call_count": 2,
            "committed_side_effect_count": 1,
            "duplicate_side_effect_count": 0,
            "dispatch_authority_decision": "active",
            "receipt_fingerprint": "receipt",
            "benchmark_result_status": "recovered",
            "benchmark_failure_class": None,
            "benchmark_recovered": True,
            "benchmark_authority_escape": False,
        }
    )

    harness = harness_evidence_from_queue_status(run, queue)
    record = collect_live_run(
        run,
        queue_status=queue,
        harness_evidence=harness,
    )

    assert harness["recovered"] is True
    assert harness["authority_escape"] is False
    assert record.recovery_success is True
    assert record.policy_safe is True


def test_remote_bridge_status_does_not_infer_recovered_from_result_string():
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    queue = _queue(run, attempts=2, model_invocations=2)
    queue.update(
        {
            "verification_status": "pass",
            "verification_cases": 3,
            "verification_passed_cases": 3,
            "fault_injection_applied": True,
            "fault_injection_evidence": "timeout_after_first_commit",
            "dispatcher_call_count": 2,
            "committed_side_effect_count": 1,
            "duplicate_side_effect_count": 0,
            "dispatch_authority_decision": "active",
            "benchmark_result_status": "recovered",
            "benchmark_recovered": None,
            "benchmark_authority_escape": None,
        }
    )

    harness = harness_evidence_from_queue_status(run, queue)

    assert harness["recovered"] is False
    assert harness["authority_escape"] is False
