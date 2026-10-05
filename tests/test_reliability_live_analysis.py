from dataclasses import replace

from spectraflow.reliability.analysis import (
    analyze_live_records,
    wilson_interval,
)
from spectraflow.reliability.collector import LiveRunRecord


def _record(
    run_id,
    *,
    policy="idempotent_recovery",
    fault="post_commit_timeout_once",
    success=True,
    safe=True,
    recovered=True,
    recovery_observed=True,
    authority_escape=False,
    authority_observed=True,
    duplicates=0,
):
    return LiveRunRecord(
        run_id=run_id,
        task_id="task",
        policy=policy,
        fault_profile=fault,
        queue_status="completed",
        terminal_queue_status=True,
        verification_status="pass" if success else "fail",
        verification_cases=3,
        verification_passed_cases=3 if success else 2,
        agent_task_success=success,
        fault_injection_applied=True,
        fault_injection_evidence="injector:evidence",
        attempts=2,
        model_invocations=2,
        recovered=recovered,
        recovery_evidence_observed=recovery_observed,
        recovery_success=recovered and safe,
        dispatcher_calls=2,
        committed_side_effect_count=1 if safe else 2,
        duplicate_side_effect_count=duplicates,
        dispatch_authority_decision="active",
        authority_escape=authority_escape,
        authority_escape_evidence_observed=authority_observed,
        policy_safe=safe,
        comparative_eligible=success,
        provider_model="test-model",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
        human_intervention_count=0,
        latency_seconds=10.0,
        receipt_fingerprint="receipt",
        result_status="recovered",
        failure_class=None,
    )


def test_wilson_interval_is_bounded_and_non_degenerate():
    interval = wilson_interval(8, 10)
    assert interval is not None
    assert 0.0 <= interval["low"] < 0.8
    assert 0.8 < interval["high"] <= 1.0
    assert wilson_interval(0, 0) is None


def test_analysis_excludes_unknown_recovery_from_denominator():
    observed = _record("a")
    unknown = _record("b", recovery_observed=False, recovered=False)

    result = analyze_live_records([observed, unknown], expected_runs=2)
    group = result["by_policy"]["idempotent_recovery"]

    assert group["recovery"]["observed"] == 1
    assert group["recovery"]["successes"] == 1
    assert group["recovery"]["rate"] == 1.0
    assert group["recovery_evidence_coverage"]["coverage"] == 0.5


def test_analysis_excludes_unknown_authority_escape_from_denominator():
    observed = _record("a", authority_escape=False)
    unknown = _record("b", authority_observed=False)

    result = analyze_live_records([observed, unknown], expected_runs=2)
    group = result["by_policy"]["idempotent_recovery"]

    assert group["authority_escape"]["observed"] == 1
    assert group["authority_escape"]["successes"] == 0
    assert group["authority_escape"]["rate"] == 0.0
    assert group["authority_evidence_coverage"]["coverage"] == 0.5


def test_analysis_keeps_task_correctness_and_policy_safety_separate():
    safe = _record("safe", policy="idempotent_recovery", safe=True)
    unsafe = _record(
        "unsafe",
        policy="bounded_recovery",
        safe=False,
        duplicates=1,
    )

    result = analyze_live_records([safe, unsafe], expected_runs=2)

    assert result["by_policy"]["idempotent_recovery"]["task_success"]["rate"] == 1.0
    assert result["by_policy"]["idempotent_recovery"]["comparative_policy_safety"]["rate"] == 1.0
    assert result["by_policy"]["bounded_recovery"]["task_success"]["rate"] == 1.0
    assert result["by_policy"]["bounded_recovery"]["comparative_policy_safety"]["rate"] == 0.0
    assert result["by_policy"]["bounded_recovery"]["duplicate_effect_runs"]["rate"] == 1.0


def test_complete_run_set_requires_unique_terminal_expected_count():
    rows = [_record(f"run-{i}") for i in range(4)]
    result = analyze_live_records(rows, expected_runs=4)
    assert result["complete_run_set"] is True

    duplicate = rows[:3] + [replace(rows[0])]
    result = analyze_live_records(duplicate, expected_runs=4)
    assert result["complete_run_set"] is False
    assert result["duplicate_run_ids"] == 1


def test_numeric_coverage_is_explicit():
    a = _record("a")
    b = replace(
        _record("b"),
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
        human_intervention_count=None,
        latency_seconds=None,
    )

    result = analyze_live_records([a, b], expected_runs=2)
    overall = result["overall"]

    assert overall["input_tokens"]["observed"] == 1
    assert overall["input_tokens"]["coverage"] == 0.5
    assert overall["cost_usd"]["observed"] == 1
    assert overall["latency_seconds"]["observed"] == 1
