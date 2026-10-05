import json

from benchmark.reliability_live_benchmark_v1 import (
    export_collector_payload,
    _write_json,
    _write_jsonl,
)
from spectraflow.reliability.collector import LiveRunRecord


def _record():
    return LiveRunRecord(
        run_id="rlab-v1::clamp-int::post_commit_timeout_once::idempotent_recovery",
        task_id="clamp-int",
        policy="idempotent_recovery",
        fault_profile="post_commit_timeout_once",
        queue_status="completed",
        terminal_queue_status=True,
        verification_status="pass",
        verification_cases=3,
        verification_passed_cases=3,
        agent_task_success=True,
        fault_injection_applied=True,
        fault_injection_evidence="injector:event-001",
        attempts=2,
        model_invocations=2,
        recovered=True,
        recovery_evidence_observed=True,
        recovery_success=True,
        dispatcher_calls=2,
        committed_side_effect_count=1,
        duplicate_side_effect_count=0,
        dispatch_authority_decision="active",
        authority_escape=False,
        authority_escape_evidence_observed=True,
        policy_safe=True,
        comparative_eligible=True,
        provider_model="example-model",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.01,
        human_intervention_count=0,
        latency_seconds=1.25,
        receipt_fingerprint="sha256:receipt",
        result_status="recovered",
        failure_class=None,
    )


def test_collector_payload_exports_standardized_observation():
    payload = {
        "expected_runs": 8,
        "missing_runs": [],
        "complete": True,
        "records": [_record().__dict__],
    }

    result = export_collector_payload(payload)

    assert result["benchmark"] == "spectraflow.real-agent-benchmark.v1"
    assert result["expected_runs"] == 8
    assert result["observed_runs"] == 1
    assert result["collector_complete"] is True
    assert result["summary"]["verified_success_rate"] == 1.0
    assert result["summary"]["safe_completion_rate"] == 1.0

    row = result["observations"][0]
    assert row["scenario_id"].startswith("rlab-v1::")
    assert row["recovery_policy"] == "idempotent_recovery"
    assert row["failure_mode"] == "post_commit_timeout"
    assert row["dispatcher_calls"] == 2
    assert row["tool_calls"] is None


def test_export_preserves_sparse_evidence():
    record = _record().__dict__.copy()
    record["duplicate_side_effect_count"] = None
    record["authority_escape_evidence_observed"] = False
    record["human_intervention_count"] = None
    record["policy_safe"] = False

    result = export_collector_payload({"records": [record]})
    row = result["observations"][0]

    assert row["duplicate_side_effects"] is None
    assert row["unauthorized_actions"] is None
    assert row["human_interventions"] is None
    assert result["summary"]["duplicate_side_effect_observed"] == 0
    assert result["summary"]["unauthorized_action_observed"] == 0


def test_export_writers_emit_json_and_jsonl(tmp_path):
    payload = export_collector_payload({"records": [_record().__dict__]})
    json_path = tmp_path / "summary.json"
    jsonl_path = tmp_path / "observations.jsonl"

    _write_json(
        json_path,
        {key: value for key, value in payload.items() if key != "observations"},
    )
    _write_jsonl(jsonl_path, payload["observations"])

    summary = json.loads(json_path.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
    ]

    assert summary["observed_runs"] == 1
    assert len(rows) == 1
    assert rows[0]["task_id"] == "clamp-int"
