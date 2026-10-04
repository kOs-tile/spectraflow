from spectraflow.reliability import (
    ResultValidationError,
    run_recovery_trial,
)


def test_success_is_not_retried():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        return {"ok": True}

    result = run_recovery_trial(operation, max_attempts=3)

    assert result.success is True
    assert result.recovered is False
    assert result.attempts == 1
    assert calls == 1


def test_timeout_then_success_is_recovered_once():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("synthetic tool timeout")
        return {"ok": True}

    result = run_recovery_trial(operation, max_attempts=3)

    assert result.success is True
    assert result.recovered is True
    assert result.attempts == 2
    assert calls == 2
    assert [r.outcome for r in result.records] == ["error", "success"]


def test_malformed_result_can_be_retried_via_validator():
    responses = iter([{"status": "partial"}, {"status": "complete"}])

    result = run_recovery_trial(
        lambda: next(responses),
        max_attempts=2,
        validator=lambda value: value.get("status") == "complete",
    )

    assert result.success is True
    assert result.recovered is True
    assert result.attempts == 2
    assert result.records[0].error_type == ResultValidationError.__name__


def test_retry_budget_exhaustion_is_reported():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise ConnectionError("synthetic provider failure")

    result = run_recovery_trial(operation, max_attempts=2)

    assert result.success is False
    assert result.retry_exhausted is True
    assert result.attempts == 2
    assert calls == 2
    assert result.terminal_error_type == "ConnectionError"


def test_non_retryable_failure_stops_immediately():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise ValueError("bad task input")

    result = run_recovery_trial(operation, max_attempts=3)

    assert result.success is False
    assert result.retry_exhausted is False
    assert result.attempts == 1
    assert calls == 1
    assert result.terminal_error_type == "ValueError"


def test_invalid_retry_budget_is_rejected_before_operation_runs():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        return "ok"

    try:
        run_recovery_trial(operation, max_attempts=0)
    except ValueError as exc:
        assert str(exc) == "max_attempts must be >= 1"
    else:
        raise AssertionError("expected ValueError")

    assert calls == 0
