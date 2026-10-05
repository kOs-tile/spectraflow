import pytest

from spectraflow.reliability.benchmark_v1 import (
    BenchmarkObservation,
    FailureMode,
    RecoveryPolicy,
)


def _observation(**overrides):
    values = {
        "task_id": "task-001",
        "scenario_id": "scenario-001",
        "recovery_policy": RecoveryPolicy.BOUNDED_RETRY,
        "failure_mode": FailureMode.TOOL_TIMEOUT,
        "terminal_status": "completed",
        "verified_success": True,
        "safe_completion": True,
        "attempts": 2,
        "model_invocations": 1,
        "tool_calls": 2,
        "human_interventions": 0,
        "duplicate_side_effects": 0,
        "unauthorized_actions": 0,
        "verification_failures": 0,
    }
    values.update(overrides)
    return BenchmarkObservation(**values)


def test_valid_observation_serializes_enum_values():
    payload = _observation().to_dict()

    assert payload["recovery_policy"] == "bounded_retry"
    assert payload["failure_mode"] == "tool_timeout"
    assert payload["verified_success"] is True


def test_verified_success_cannot_hide_verification_failure():
    with pytest.raises(ValueError, match="verified_success"):
        _observation(verification_failures=1).validate()


def test_safe_completion_cannot_hide_duplicate_side_effect():
    with pytest.raises(ValueError, match="safe_completion"):
        _observation(duplicate_side_effects=1).validate()


def test_safe_completion_cannot_hide_unauthorized_action():
    with pytest.raises(ValueError, match="safe_completion"):
        _observation(unauthorized_actions=1).validate()


@pytest.mark.parametrize(
    "field",
    [
        "attempts",
        "model_invocations",
        "tool_calls",
        "human_interventions",
        "duplicate_side_effects",
        "unauthorized_actions",
        "verification_failures",
    ],
)
def test_count_fields_must_be_non_negative(field):
    with pytest.raises(ValueError):
        _observation(**{field: -1}).validate()
