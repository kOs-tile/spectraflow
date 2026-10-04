"""Source-agnostic KAVI/Hermes runtime hooks for Reliability Lab live v1.

Existing KAVI execution code can integrate the benchmark without duplicating its
workspace, verifier, fault injector, ledger, or evidence exporter.

Normal non-benchmark tasks are untouched.
"""

from __future__ import annotations

from typing import Any

from benchmark.reliability_harness_cli import (
    dispatch_command,
    prepare_command,
    verify_command,
)
from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.kavi_adapter import RECOVERY_STRATEGIES
from spectraflow.reliability.live import build_live_plan, load_live_manifest


SUITE = "spectraflow.reliability-live.v1"


def is_reliability_lab_task(task: dict[str, Any]) -> bool:
    benchmark = task.get("benchmark")
    return isinstance(benchmark, dict) and benchmark.get("suite") == SUITE


def _run_index():
    manifest = load_live_manifest(MANIFEST)
    return {run.run_id: run for run in build_live_plan(manifest)}


def _resolve_run(task: dict[str, Any]):
    if not is_reliability_lab_task(task):
        raise ValueError("not_reliability_lab_task")

    benchmark = task["benchmark"]
    required = ("run_id", "task_id", "fault_profile", "policy")
    missing = [field for field in required if not benchmark.get(field)]
    if missing:
        raise ValueError("benchmark_identity_incomplete")

    runs = _run_index()
    run = runs.get(benchmark["run_id"])
    if run is None:
        raise ValueError("unknown_benchmark_run_id")

    if benchmark["task_id"] != run.task_id:
        raise ValueError("benchmark_task_id_mismatch")
    if benchmark["fault_profile"] != run.fault_profile:
        raise ValueError("benchmark_fault_profile_mismatch")
    if benchmark["policy"] != run.policy:
        raise ValueError("benchmark_policy_mismatch")

    return run


def validate_runtime_task(task: dict[str, Any]) -> None:
    run = _resolve_run(task)

    if task.get("target_actor") != "codex":
        raise ValueError("benchmark_target_actor_must_be_codex")
    if task.get("risk_class") != "NONE":
        raise ValueError("benchmark_risk_must_be_none")
    if task.get("execution_mode") != "AUTO":
        raise ValueError("benchmark_execution_mode_must_be_auto")
    if task.get("idempotency_key") != run.run_id:
        raise ValueError("benchmark_idempotency_mismatch")

    expected_retries = max(0, run.max_attempts - 1)
    if task.get("max_retries", 0) != expected_retries:
        raise ValueError("benchmark_retry_budget_mismatch")
    if expected_retries > 2:
        raise ValueError("benchmark_retry_budget_exceeds_2")

    expected_strategy = RECOVERY_STRATEGIES[run.policy]
    if task.get("recovery_strategy") != expected_strategy:
        raise ValueError("benchmark_recovery_strategy_mismatch")


def prepare_before_agent(
    task: dict[str, Any],
    *,
    workspace_root: str,
) -> dict[str, Any]:
    """Return a local-only launch override for benchmark tasks.

    The caller must not persist cwd/candidate_path into shared GitHub state.
    """

    if not is_reliability_lab_task(task):
        return {"handled": False}

    validate_runtime_task(task)
    run = _resolve_run(task)
    prepared = prepare_command(run.run_id, workspace_root)

    return {
        "handled": True,
        "run_id": run.run_id,
        "workspace_id": prepared["workspace_id"],
        "cwd": prepared["workspace"],
        "agent_instruction": prepared["agent_instruction"],
        "local_candidate_path": prepared["candidate_path"],
        "queue_patch": {
            "benchmark_runtime": {
                "workspace_id": prepared["workspace_id"],
                "adapter": "spectraflow-kavi-runtime-shim-v1",
            }
        },
    }


def _queue_patch_from_evidence(
    verification: dict[str, Any],
    dispatch: dict[str, Any],
    runtime_telemetry: dict[str, Any] | None,
) -> dict[str, Any]:
    runtime = runtime_telemetry or {}
    patch = {
        "verification_status": verification["status"],
        "verification_cases": verification["cases"],
        "verification_passed_cases": verification["passed_cases"],
        "fault_injection_applied": dispatch["fault_injection_applied"],
        "fault_injection_evidence": dispatch["fault_injection_evidence"],
        "dispatcher_call_count": dispatch["dispatcher_calls"],
        "committed_side_effect_count": dispatch["committed_side_effect_count"],
        "duplicate_side_effect_count": dispatch["duplicate_side_effect_count"],
        "dispatch_authority_decision": dispatch["dispatch_authority_decision"],
        "receipt_fingerprint": dispatch["receipt_fingerprint"],
        "benchmark_result_status": dispatch["result_status"],
        "benchmark_failure_class": dispatch["failure_class"],
        "benchmark_recovered": dispatch["recovered"],
        "benchmark_authority_escape": dispatch["authority_escape"],
    }

    for field in (
        "provider_model",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "human_intervention_count",
    ):
        if runtime.get(field) is not None:
            patch[field] = runtime[field]

    return patch


def finalize_after_agent(
    task: dict[str, Any],
    *,
    workspace_root: str,
    evidence_root: str,
    runtime_telemetry: dict[str, Any] | None = None,
    authority_fixture_available: bool = True,
) -> dict[str, Any]:
    """Verify, apply benchmark fault/policy, and return a queue-safe patch."""

    if not is_reliability_lab_task(task):
        return {"handled": False}

    validate_runtime_task(task)
    run = _resolve_run(task)

    verification = verify_command(
        run.run_id,
        workspace_root=workspace_root,
        evidence_root=evidence_root,
    )
    dispatch = dispatch_command(
        run.run_id,
        evidence_root=evidence_root,
        runtime_status=runtime_telemetry,
        authority_fixture_available=authority_fixture_available,
    )

    return {
        "handled": True,
        "run_id": run.run_id,
        "verification": {
            "status": verification["status"],
            "cases": verification["cases"],
            "passed_cases": verification["passed_cases"],
        },
        "benchmark_result": {
            "status": dispatch["result_status"],
            "failure_class": dispatch["failure_class"],
            "fault_injection_applied": dispatch["fault_injection_applied"],
            "recovered": dispatch["recovered"],
            "duplicate_side_effect_count": dispatch[
                "duplicate_side_effect_count"
            ],
            "authority_escape": dispatch["authority_escape"],
        },
        "queue_patch": _queue_patch_from_evidence(
            verification,
            dispatch,
            runtime_telemetry,
        ),
        "local_evidence_record": dispatch["evidence_record"],
    }
