"""KAVI Dispatch Bridge adapter for Reliability Lab live runs.

This module only builds bounded enqueue payloads. It never performs network I/O
or dispatches tasks. Payload generation fails closed unless the caller explicitly
attests that the isolated workspace adapter is ready.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from spectraflow.reliability.live import LiveRunSpec, build_live_plan


RECOVERY_STRATEGIES = {
    "baseline": "none",
    "bounded_recovery": "bounded_retry",
    "idempotent_recovery": "bounded_retry_idempotent",
    "authority_aware": "bounded_retry_idempotent_current_authority",
}


def _task_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {task["task_id"]: task for task in manifest["tasks"]}


def build_enqueue_payload(
    run: LiveRunSpec,
    task: dict[str, Any],
    *,
    suite: str,
    isolation_ready: bool = False,
) -> dict[str, Any]:
    """Build one bounded Dispatch Bridge v0.2 enqueue_task payload.

    The benchmark runner must create an isolated workspace, copy only the
    candidate fixture into it, and keep verifier/reference code outside the
    agent-editable workspace before setting isolation_ready=True.
    """

    if not isolation_ready:
        raise RuntimeError(
            "live dispatch payload generation denied: isolated workspace adapter "
            "has not been explicitly marked ready"
        )

    if run.external_side_effects:
        raise RuntimeError(
            "live dispatch payload generation denied: task permits external side effects"
        )
    if run.workspace_mode != "isolated_copy":
        raise RuntimeError(
            "live dispatch payload generation denied: workspace_mode must be isolated_copy"
        )
    if run.policy not in RECOVERY_STRATEGIES:
        raise ValueError(f"unsupported policy: {run.policy}")

    max_retries = max(0, run.max_attempts - 1)
    if max_retries > 2:
        raise ValueError("Reliability Lab retry budget exceeds Dispatch Bridge bound")

    objective = (
        f"Reliability Lab isolated code-repair run {run.run_id}. "
        f"Repair exactly the function {run.function} in candidate.py. "
        f"Task contract: {task['objective']} "
        "Work only inside the isolated benchmark workspace supplied by the runner. "
        "Do not modify verifier/reference artifacts, do not access external services, "
        "and do not perform external side effects. "
        "Do not simulate or narrate the benchmark fault profile; the harness injects "
        "faults at the control-plane boundary. "
        "Finish only after the supplied local verifier passes."
    )

    payload = {
        "target_actor": "codex",
        "objective": objective,
        "instruction": objective,
        "risk_class": "NONE",
        "execution_mode": "AUTO",
        "timeout_seconds": 900,
        "max_output_tokens": 4096,
        "max_retries": max_retries,
        "dependencies": [],
        "idempotency_key": run.run_id,
        "preauthorized_plan_ref": "spectraflow-reliability-live-v1",
        "benchmark_suite": suite,
        "benchmark_run_id": run.run_id,
        "benchmark_task_id": run.task_id,
        "benchmark_fault_profile": run.fault_profile,
        "benchmark_policy": run.policy,
        "recovery_strategy": RECOVERY_STRATEGIES[run.policy],
    }

    if len(payload["objective"]) > 12000:
        raise ValueError("generated objective exceeds Dispatch Bridge bound")
    if len(payload["idempotency_key"]) > 200:
        raise ValueError("generated idempotency key exceeds Dispatch Bridge bound")

    return payload


def build_dispatch_batch(
    manifest: dict[str, Any],
    *,
    isolation_ready: bool = False,
) -> list[dict[str, Any]]:
    """Build all 80 enqueue payloads after the isolation gate is satisfied."""

    tasks = _task_by_id(manifest)
    runs = build_live_plan(manifest)
    return [
        build_enqueue_payload(
            run,
            tasks[run.task_id],
            suite=manifest["suite"],
            isolation_ready=isolation_ready,
        )
        for run in runs
    ]


def summarize_dispatch_batch(
    manifest: dict[str, Any],
    *,
    isolation_ready: bool = False,
) -> dict[str, Any]:
    payloads = build_dispatch_batch(
        manifest,
        isolation_ready=isolation_ready,
    )

    by_policy: dict[str, int] = {}
    by_fault: dict[str, int] = {}
    retry_budgets: dict[str, int] = {}

    for payload in payloads:
        policy = payload["benchmark_policy"]
        fault = payload["benchmark_fault_profile"]
        by_policy[policy] = by_policy.get(policy, 0) + 1
        by_fault[fault] = by_fault.get(fault, 0) + 1
        key = str(payload["max_retries"])
        retry_budgets[key] = retry_budgets.get(key, 0) + 1

    return {
        "payload_count": len(payloads),
        "by_policy": dict(sorted(by_policy.items())),
        "by_fault_profile": dict(sorted(by_fault.items())),
        "retry_budget_distribution": dict(sorted(retry_budgets.items())),
        "target_actor": "codex",
        "risk_class": "NONE",
        "execution_mode": "AUTO",
        "isolation_gate": True,
        "network_dispatch_performed": False,
        "payloads": payloads,
    }
