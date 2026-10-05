from spectraflow.reliability.benchmark_metrics import summarize_benchmark
from spectraflow.reliability.benchmark_v1 import (
    BenchmarkObservation,
    FailureMode,
    RecoveryPolicy,
)


def _row(
    task_id,
    *,
    verified_success,
    safe_completion,
    failure_mode=FailureMode.TOOL_TIMEOUT,
    policy=RecoveryPolicy.BOUNDED_RETRY,
    input_tokens=None,
    cost=None,
    latency=None,
    interventions=0,
    duplicates=0,
    unauthorized=0,
    verification_failures=0,
):
    return BenchmarkObservation(
        task_id=task_id,
        scenario_id=f"scenario-{task_id}",
        recovery_policy=policy,
        failure_mode=failure_mode,
        terminal_status="completed" if verified_success else "failed",
        verified_success=verified_success,
        safe_completion=safe_completion,
        attempts=2,
        model_invocations=1,
        tool_calls=2,
        input_tokens=input_tokens,
        estimated_cost_usd=cost,
        latency_ms=latency,
        human_interventions=interventions,
        duplicate_side_effects=duplicates,
        unauthorized_actions=unauthorized,
        verification_failures=verification_failures,
    )


def test_summary_uses_explicit_denominators():
    result = summarize_benchmark(
        [
            _row("1", verified_success=True, safe_completion=True),
            _row("2", verified_success=True, safe_completion=False, duplicates=1),
            _row("3", verified_success=False, safe_completion=False, interventions=1),
            _row(
                "4",
                verified_success=False,
                safe_completion=False,
                unauthorized=1,
            ),
        ]
    )

    assert result["runs"] == 4
    assert result["verified_success_rate"] == 0.5
    assert result["safe_completion_rate"] == 0.25
    assert result["duplicate_side_effect_rate"] == 0.25
    assert result["unauthorized_action_rate"] == 0.25
    assert result["human_intervention_rate"] == 0.25


def test_missing_cost_token_latency_are_not_treated_as_zero():
    result = summarize_benchmark(
        [
            _row(
                "1",
                verified_success=True,
                safe_completion=True,
                input_tokens=100,
                cost=0.02,
                latency=500,
            ),
            _row(
                "2",
                verified_success=True,
                safe_completion=True,
            ),
        ]
    )

    assert result["input_tokens"] == {"observed": 1, "mean": 100}
    assert result["estimated_cost_usd"] == {"observed": 1, "mean": 0.02}
    assert result["latency_ms"] == {"observed": 1, "mean": 500}


def test_recovery_metrics_are_grouped_by_failure_mode():
    result = summarize_benchmark(
        [
            _row("1", verified_success=True, safe_completion=True),
            _row("2", verified_success=False, safe_completion=False),
            _row(
                "3",
                verified_success=True,
                safe_completion=True,
                failure_mode=FailureMode.PROVIDER_FAILURE,
            ),
        ]
    )

    timeout = result["recovery_by_failure_mode"]["tool_timeout"]
    provider = result["recovery_by_failure_mode"]["provider_failure"]

    assert timeout["recovery_denominator"] == 2
    assert timeout["verified_recovery_rate"] == 0.5
    assert timeout["safe_recovery_rate"] == 0.5
    assert provider["verified_recovery_rate"] == 1.0
    assert provider["safe_recovery_rate"] == 1.0


def test_empty_summary_keeps_rates_null():
    result = summarize_benchmark([])

    assert result["runs"] == 0
    assert result["verified_success_rate"] is None
    assert result["safe_completion_rate"] is None
    assert result["estimated_cost_usd"] == {"observed": 0, "mean": None}
