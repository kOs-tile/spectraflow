import json

import pytest

from benchmark.real_agent_v1 import load_jsonl, run_file


def _payload(task_id="task-1"):
    return {
        "task_id": task_id,
        "scenario_id": f"scenario-{task_id}",
        "recovery_policy": "bounded_retry",
        "failure_mode": "tool_timeout",
        "terminal_status": "completed",
        "verified_success": True,
        "safe_completion": True,
        "attempts": 2,
        "model_invocations": 1,
        "tool_calls": 2,
        "input_tokens": 100,
        "output_tokens": 20,
        "estimated_cost_usd": 0.01,
        "latency_ms": 500,
        "human_interventions": 0,
        "duplicate_side_effects": 0,
        "unauthorized_actions": 0,
        "verification_failures": 0,
        "source": "fixture",
        "evidence_id": "evidence-1",
    }


def test_jsonl_runner_loads_and_summarizes(tmp_path):
    path = tmp_path / "observations.jsonl"
    rows = [_payload("task-1"), _payload("task-2")]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = run_file(path)

    assert result["runs"] == 2
    assert result["verified_success_rate"] == 1.0
    assert result["safe_completion_rate"] == 1.0
    assert result["input_tokens"]["observed"] == 2


def test_jsonl_runner_reports_line_number_for_invalid_record(tmp_path):
    path = tmp_path / "observations.jsonl"
    valid = _payload("task-1")
    invalid = _payload("task-2")
    invalid["safe_completion"] = True
    invalid["duplicate_side_effects"] = 1

    path.write_text(
        "\n".join([json.dumps(valid), json.dumps(invalid)]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=":2:"):
        load_jsonl(path)
