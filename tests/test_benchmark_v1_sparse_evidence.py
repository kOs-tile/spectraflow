from spectraflow.reliability.benchmark_metrics import summarize_benchmark
from spectraflow.reliability.benchmark_v1 import (
    BenchmarkObservation,
    FailureMode,
    RecoveryPolicy,
)


def test_unknown_safety_evidence_is_not_counted_as_zero():
    row = BenchmarkObservation(
        task_id="task-unknown",
        scenario_id="scenario-unknown",
        recovery_policy=RecoveryPolicy.BASELINE,
        failure_mode=FailureMode.NONE,
        terminal_status="completed",
        verified_success=False,
        safe_completion=False,
        attempts=1,
        model_invocations=1,
        tool_calls=0,
    )

    result = summarize_benchmark([row])

    assert result["duplicate_side_effect_observed"] == 0
    assert result["duplicate_side_effect_rate"] is None
    assert result["unauthorized_action_observed"] == 0
    assert result["unauthorized_action_rate"] is None
    assert result["human_intervention_observed"] == 0
    assert result["human_intervention_rate"] is None
