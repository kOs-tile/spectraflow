from benchmark.execution_traces import run_benchmark


def _row(result, label):
    return next(row for row in result["results"] if row["task_id"] == label)


def test_execution_trace_benchmark_exposes_blind_retry_duplicate():
    result = run_benchmark()
    naive = _row(result, "post_commit_timeout_naive")

    assert naive["operation_completed"] is True
    assert naive["recovered"] is True
    assert naive["dispatcher_calls"] == 2
    assert naive["committed_side_effects"] == 2
    assert naive["duplicate_side_effect_detected"] is True
    assert naive["duplicate_side_effects"] == 1
    assert naive["safe_completion"] is False
    assert naive["intervention_required"] is True


def test_idempotent_retry_prevents_duplicate_side_effect():
    result = run_benchmark()
    idempotent = _row(result, "post_commit_timeout_idempotent")

    assert idempotent["operation_completed"] is True
    assert idempotent["recovered"] is True
    assert idempotent["dispatcher_calls"] == 2
    assert idempotent["committed_side_effects"] == 1
    assert idempotent["duplicate_side_effect_detected"] is False
    assert idempotent["safe_completion"] is True


def test_verification_failure_requires_intervention():
    result = run_benchmark()
    verification = _row(result, "verification_failure")

    assert verification["operation_completed"] is True
    assert verification["verification_passed"] is False
    assert verification["task_success"] is False
    assert verification["intervention_required"] is True


def test_terminal_failure_never_dispatches():
    result = run_benchmark()
    terminal = _row(result, "terminal_failure")

    assert terminal["operation_completed"] is False
    assert terminal["dispatcher_calls"] == 0
    assert terminal["committed_side_effects"] == 0
    assert terminal["intervention_required"] is True


def test_execution_trace_corpus_summary_is_stable():
    result = run_benchmark()

    assert result["cases"] == 6
    assert result["safe_completions"] == 3
    assert result["unsafe_or_incomplete"] == 3
    assert result["duplicate_side_effects"] == 1
    assert result["intervention_required"] == 3
