from benchmark.reliability_lab_v1 import run_benchmark


def test_reliability_lab_v1_real_corpus_size_and_provenance():
    result = run_benchmark()

    assert result["corpus"]["real_historical_agent_sessions"] == 85
    assert result["corpus"]["real_execution_control_tasks"] == 26
    assert result["corpus"]["real_records_total"] == 111
    assert result["corpus"]["history_provenance"]["source_record_count"] == 85
    assert result["corpus"]["execution_provenance"]["source_record_count"] == 26


def test_reliability_lab_v1_history_checkpoint():
    result = run_benchmark()
    history = result["historical_sessions"]

    assert history["status_distribution"] == {
        "active_or_unknown": 60,
        "blocked": 9,
        "completed": 10,
        "unknown": 6,
    }
    assert history["total_tool_calls_recorded"] == 4937
    assert history["total_messages_recorded"] == 1991


def test_reliability_lab_v1_execution_checkpoint():
    result = run_benchmark()
    execution = result["execution_control"]

    assert execution["status_distribution"] == {
        "blocked": 1,
        "completed": 8,
        "failed": 15,
        "ready": 2,
    }
    assert execution["actor_distribution"] == {"codex": 25, "work": 1}
    assert execution["risk_distribution"] == {"LOW": 26}
    assert execution["total_attempts"] == 23
    assert execution["observed_retries"] == 0
    assert execution["total_model_invocations"] == 17
    assert execution["timeout_failures"] == 8


def test_reliability_lab_v1_reports_missing_observability_instead_of_inventing_it():
    result = run_benchmark()
    coverage = result["metric_coverage"]

    assert coverage["execution_status"]["fraction"] == 1.0
    assert coverage["model_invocations"]["fraction"] == 1.0
    assert coverage["task_success"]["observed"] == 21
    assert coverage["latency"]["observed"] == 12
    assert coverage["verification_evidence"]["observed"] == 8
    assert coverage["actual_token_usage"]["observed"] == 0
    assert coverage["actual_cost"]["observed"] == 0
    assert coverage["human_intervention"]["observed"] == 0
    assert coverage["committed_side_effects"]["observed"] == 0
    assert coverage["unauthorized_action"]["observed"] == 0


def test_reliability_lab_v1_controlled_retry_pair_exposes_idempotency_difference():
    result = run_benchmark()
    pair = result["controlled_policy_pair"]

    naive = pair["post_commit_timeout_naive"]
    idempotent = pair["post_commit_timeout_idempotent"]

    assert naive["attempts"] == 2
    assert naive["duplicate_side_effects"] == 1
    assert naive["safe_completion"] is False

    assert idempotent["attempts"] == 2
    assert idempotent["duplicate_side_effects"] == 0
    assert idempotent["safe_completion"] is True


def test_reliability_lab_v1_does_not_claim_real_recovery_rate():
    result = run_benchmark()

    assert result["claim_boundary"]["real_recovery_rate_available"] is False
    assert "no attempts>1 recovery event" in result["claim_boundary"]["reason"]
