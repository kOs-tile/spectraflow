from benchmark.kavi_runtime_traces import run_benchmark


def _row(result, task_id):
    return next(row for row in result["results"] if row["task_id"] == task_id)


def test_runtime_derived_corpus_summary_is_stable():
    result = run_benchmark()

    assert result["cases"] == 8
    assert result["verified_pass"] == 2
    assert result["completed_unverified"] == 2
    assert result["explicit_failures"] == 4
    assert result["pre_model_failures"] == 2
    assert result["post_model_failures"] == 2
    assert result["timeout_failures"] == 2
    assert result["single_attempt_cases"] == 8
    assert result["durations_observed"] == 5


def test_runtime_classifier_does_not_upgrade_completed_without_acceptance_evidence():
    result = run_benchmark()
    row = _row(result, "codex-recertification-20260920-001")

    assert row["status"] == "completed"
    assert row["verified_pass"] is False
    assert row["completed_without_acceptance_evidence"] is True
    assert row["outcome_class"] == "completed_unverified"


def test_pre_model_and_post_model_failures_are_distinguished():
    result = run_benchmark()

    pre = _row(result, "codex-runner-diagnosis-001")
    post = _row(result, "relationship-graph-v0-003")

    assert pre["pre_model_failure"] is True
    assert pre["model_invocations"] == 0
    assert post["post_model_failure"] is True
    assert post["model_invocations"] == 1
    assert post["failure_phase"] == "postflight"


def test_process_timeout_is_classified_from_runtime_metadata():
    result = run_benchmark()
    row = _row(result, "work-sync-atomic-cert-20260920-006")

    assert row["process_status"] == "timed_out"
    assert row["timeout_failure"] is True
    assert row["process_exit_code"] == 1


def test_verified_pass_requires_explicit_acceptance_or_final_status():
    result = run_benchmark()

    closure = _row(result, "work-sync-final-closure-20260921-008")
    work = _row(result, "work-execution-e2e-20260921-009")

    assert closure["verified_pass"] is True
    assert work["verified_pass"] is True
