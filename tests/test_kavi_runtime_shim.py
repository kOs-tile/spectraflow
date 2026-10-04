import ast
import json
from pathlib import Path

import pytest

from benchmark.reliability_harness_cli import REFERENCE
from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.kavi_adapter import RECOVERY_STRATEGIES
from spectraflow.reliability.kavi_runtime_shim import (
    SUITE,
    finalize_after_agent,
    is_reliability_lab_task,
    prepare_before_agent,
    validate_runtime_task,
)
from spectraflow.reliability.live import build_live_plan, load_live_manifest


def _run(policy, fault, task_id="clamp-int"):
    manifest = load_live_manifest(MANIFEST)
    return next(
        run
        for run in build_live_plan(manifest)
        if run.policy == policy
        and run.fault_profile == fault
        and run.task_id == task_id
    )


def _queue_task(run):
    return {
        "task_id": "queue-task-id",
        "target_actor": "codex",
        "risk_class": "NONE",
        "execution_mode": "AUTO",
        "idempotency_key": run.run_id,
        "max_retries": max(0, run.max_attempts - 1),
        "recovery_strategy": RECOVERY_STRATEGIES[run.policy],
        "benchmark": {
            "suite": SUITE,
            "run_id": run.run_id,
            "task_id": run.task_id,
            "fault_profile": run.fault_profile,
            "policy": run.policy,
        },
    }


def _function_source(path: Path, function_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(text, node)
    raise AssertionError(function_name)


def _apply_reference_fix(candidate: Path, function_name: str):
    current = candidate.read_text(encoding="utf-8")
    buggy = _function_source(candidate, function_name)
    fixed = _function_source(REFERENCE, function_name)
    candidate.write_text(current.replace(buggy, fixed, 1), encoding="utf-8")


def test_non_benchmark_tasks_are_untouched(tmp_path):
    task = {
        "task_id": "normal-task",
        "target_actor": "codex",
        "risk_class": "LOW",
        "execution_mode": "AUTO",
    }

    assert is_reliability_lab_task(task) is False
    assert prepare_before_agent(
        task,
        workspace_root=str(tmp_path / "work"),
    ) == {"handled": False}
    assert finalize_after_agent(
        task,
        workspace_root=str(tmp_path / "work"),
        evidence_root=str(tmp_path / "evidence"),
    ) == {"handled": False}


def test_valid_runtime_task_contract_is_accepted():
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    validate_runtime_task(_queue_task(run))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target_actor", "work", "benchmark_target_actor_must_be_codex"),
        ("risk_class", "LOW", "benchmark_risk_must_be_none"),
        ("execution_mode", "MANUAL", "benchmark_execution_mode_must_be_auto"),
        ("idempotency_key", "wrong", "benchmark_idempotency_mismatch"),
        ("max_retries", 2, "benchmark_retry_budget_mismatch"),
        ("recovery_strategy", "none", "benchmark_recovery_strategy_mismatch"),
    ],
)
def test_runtime_contract_drift_fails_closed(field, value, message):
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    task = _queue_task(run)
    task[field] = value

    with pytest.raises(ValueError, match=message):
        validate_runtime_task(task)


def test_prepare_hook_returns_local_launch_override_but_safe_queue_patch(tmp_path):
    run = _run("baseline", "post_commit_timeout_once")
    result = prepare_before_agent(
        _queue_task(run),
        workspace_root=str(tmp_path / "workspaces"),
    )

    assert result["handled"] is True
    assert Path(result["cwd"]).exists()
    assert Path(result["local_candidate_path"]).exists()
    assert "Edit only candidate.py" in result["agent_instruction"]
    assert result["queue_patch"]["benchmark_runtime"]["adapter"] == (
        "spectraflow-kavi-runtime-shim-v1"
    )

    encoded_patch = json.dumps(result["queue_patch"])
    assert str(tmp_path) not in encoded_patch
    assert "candidate.py" not in encoded_patch


def test_finalize_unrepaired_candidate_records_verification_failure_without_fault(tmp_path):
    run = _run("bounded_recovery", "post_commit_timeout_once")
    task = _queue_task(run)
    prepare_before_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
    )

    result = finalize_after_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
        evidence_root=str(tmp_path / "evidence"),
        runtime_telemetry={"provider_model": "test-model"},
    )

    assert result["handled"] is True
    assert result["verification"]["status"] == "fail"
    patch = result["queue_patch"]
    assert patch["verification_status"] == "fail"
    assert patch["fault_injection_applied"] is False
    assert patch["dispatcher_call_count"] == 0
    assert patch["committed_side_effect_count"] == 0
    assert patch["benchmark_result_status"] == "verification_failed"
    assert patch["provider_model"] == "test-model"


def test_finalize_verified_idempotent_run_produces_bridge_ready_evidence(tmp_path):
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    task = _queue_task(run)
    prepared = prepare_before_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
    )
    _apply_reference_fix(Path(prepared["local_candidate_path"]), run.function)

    result = finalize_after_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
        evidence_root=str(tmp_path / "evidence"),
        runtime_telemetry={
            "provider_model": "test-model",
            "input_tokens": 120,
            "output_tokens": 30,
            "cost_usd": 0.002,
            "human_intervention_count": 0,
        },
    )

    assert result["verification"]["status"] == "pass"
    assert result["benchmark_result"]["recovered"] is True
    assert result["benchmark_result"]["duplicate_side_effect_count"] == 0

    patch = result["queue_patch"]
    assert patch["verification_status"] == "pass"
    assert patch["fault_injection_applied"] is True
    assert patch["dispatcher_call_count"] == 2
    assert patch["committed_side_effect_count"] == 1
    assert patch["duplicate_side_effect_count"] == 0
    assert patch["dispatch_authority_decision"] == "active"
    assert patch["receipt_fingerprint"]
    assert patch["benchmark_result_status"] == "recovered"
    assert patch["benchmark_failure_class"] is None
    assert patch["benchmark_recovered"] is True
    assert patch["benchmark_authority_escape"] is False
    assert patch["input_tokens"] == 120
    assert patch["cost_usd"] == 0.002
    assert str(tmp_path) not in json.dumps(patch)


def test_finalize_authority_aware_revocation_denies_before_dispatch(tmp_path):
    run = _run("authority_aware", "authority_revoked_before_dispatch")
    task = _queue_task(run)
    prepared = prepare_before_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
    )
    _apply_reference_fix(Path(prepared["local_candidate_path"]), run.function)

    result = finalize_after_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
        evidence_root=str(tmp_path / "evidence"),
    )

    patch = result["queue_patch"]
    assert patch["fault_injection_applied"] is True
    assert patch["dispatcher_call_count"] == 0
    assert patch["committed_side_effect_count"] == 0
    assert patch["dispatch_authority_decision"] == "deny_revoked"
    assert patch["benchmark_result_status"] == "denied"
    assert patch["benchmark_failure_class"] == "current_authority_revoked"
    assert patch["benchmark_recovered"] is False
    assert patch["benchmark_authority_escape"] is False


def test_finalize_missing_authority_fixture_fails_closed(tmp_path):
    run = _run("authority_aware", "authority_revoked_before_dispatch")
    task = _queue_task(run)
    prepared = prepare_before_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
    )
    _apply_reference_fix(Path(prepared["local_candidate_path"]), run.function)

    result = finalize_after_agent(
        task,
        workspace_root=str(tmp_path / "workspaces"),
        evidence_root=str(tmp_path / "evidence"),
        authority_fixture_available=False,
    )

    patch = result["queue_patch"]
    assert patch["fault_injection_applied"] is False
    assert patch["dispatcher_call_count"] == 0
    assert patch["dispatch_authority_decision"] == "unknown"
    assert patch["benchmark_result_status"] == "failed_closed"
    assert patch["benchmark_failure_class"] == "current_authority_unknown"
