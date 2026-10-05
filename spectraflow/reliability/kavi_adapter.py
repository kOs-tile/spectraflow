"""KAVI Dispatch Bridge adapter for Reliability Lab live runs.

This module only builds bounded enqueue payloads. It never performs network I/O
or dispatches tasks. Payload generation fails closed unless the caller explicitly
attests that the isolated workspace adapter is ready.
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
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
        f"Reliability Lab bounded candidate-generation run {run.run_id}. "
        f"Task contract: {task['objective']} "
        f"Return a candidate implementation for exactly the function {run.function}. "
        "Do not edit files, run commands, call tools, access network services, or perform external side effects. "
        "Do not simulate or narrate the benchmark fault profile; the harness injects faults at the control-plane boundary. "
        "The final answer must contain one compact JSON line beginning exactly with RLAB_CANDIDATE_JSON: "
        "and containing keys task_id, function, and candidate_source, followed by a final line RLAB_LIVE_RESULT: PASS. "
        "PASS only means a candidate was produced; the isolated verifier determines correctness."
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


_RESULT_PREFIX = "RLAB_CANDIDATE_JSON:"
_PASS_MARKER = "RLAB_LIVE_RESULT: PASS"


@dataclass(frozen=True)
class KaviCollectedResult:
    run_id: str
    task_id: str
    policy: str
    requested_fault_profile: str
    queue_status: str
    attempts: int
    model_invocations: int
    acceptance_status: str | None
    final_status: str | None
    verification_status: str | None
    verification_cases: int | None
    verification_passed_cases: int | None
    provider_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    human_intervention_count: int | None
    dispatcher_call_count: int | None
    committed_side_effect_count: int | None
    duplicate_side_effect_count: int | None
    dispatch_authority_decision: str | None
    fault_injection_applied: bool | None
    fault_injection_evidence: str | None
    receipt_fingerprint: str | None
    benchmark_result_status: str | None
    benchmark_failure_class: str | None
    benchmark_recovered: bool | None
    benchmark_authority_escape: bool | None
    candidate_source: str | None
    candidate_parse_error: str | None
    terminal_pass_marker: bool
    runtime_evidence_complete: bool


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_candidate(text: str) -> tuple[str | None, str | None]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(_RESULT_PREFIX):
            continue
        raw = stripped[len(_RESULT_PREFIX):].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"invalid_candidate_json:{exc.msg}"
        source = payload.get("candidate_source")
        if not isinstance(source, str) or not source.strip():
            return None, "missing_candidate_source"
        return source, None
    return None, "candidate_marker_not_found"


def validate_candidate_shape(
    source: str,
    *,
    expected_function: str,
) -> dict[str, Any]:
    """Reject unrelated/dynamic code before isolated verification.

    This is a preflight filter, not a sandbox. The accepted function still must
    run only inside the separate network-off verifier container.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"accepted": False, "reason": f"syntax_error:{exc.msg}"}

    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return {"accepted": False, "reason": "exactly_one_function_required"}

    fn = tree.body[0]
    if fn.name != expected_function:
        return {"accepted": False, "reason": "function_name_mismatch"}
    if fn.decorator_list:
        return {"accepted": False, "reason": "decorators_not_allowed"}

    forbidden_calls = {
        "__import__", "eval", "exec", "compile", "open", "input",
        "breakpoint", "globals", "locals", "vars",
    }
    forbidden_roots = {
        "os", "subprocess", "socket", "requests", "urllib",
        "pathlib", "shutil", "sys",
    }

    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return {"accepted": False, "reason": "imports_not_allowed"}
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                return {
                    "accepted": False,
                    "reason": f"forbidden_call:{node.func.id}",
                }
            if isinstance(node.func, ast.Attribute):
                root = node.func.value
                if isinstance(root, ast.Name) and root.id in forbidden_roots:
                    return {
                        "accepted": False,
                        "reason": f"forbidden_root:{root.id}",
                    }
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return {"accepted": False, "reason": "dunder_name_not_allowed"}

    return {"accepted": True, "reason": None}


def collect_task_status(
    status: dict[str, Any],
    *,
    expected_run: LiveRunSpec,
) -> KaviCollectedResult:
    """Normalize Dispatch Bridge get_task_status without inferring evidence."""

    benchmark = status.get("benchmark") or {}
    returned_run_id = benchmark.get("run_id")
    if returned_run_id is not None and returned_run_id != expected_run.run_id:
        raise ValueError("benchmark_run_id_mismatch")

    combined = "\n".join(
        value
        for value in (
            status.get("terminal_result"),
            status.get("result_excerpt"),
        )
        if isinstance(value, str)
    )
    candidate_source, candidate_error = _extract_candidate(combined)
    terminal_pass = _PASS_MARKER in combined

    fault_applied = status.get("fault_injection_applied")
    if fault_applied not in (True, False):
        fault_applied = None

    fault_evidence = status.get("fault_injection_evidence")
    if not isinstance(fault_evidence, str) or not fault_evidence.strip():
        fault_evidence = None

    runtime_evidence_complete = (
        str(status.get("status") or "").lower() in {"completed", "failed", "blocked"}
        and fault_applied is not None
        and (
            fault_applied is False
            or fault_evidence is not None
        )
        and status.get("benchmark_result_status") is not None
    )

    return KaviCollectedResult(
        run_id=expected_run.run_id,
        task_id=expected_run.task_id,
        policy=expected_run.policy,
        requested_fault_profile=expected_run.fault_profile,
        queue_status=str(status.get("status") or "unknown"),
        attempts=int(status.get("attempts") or 0),
        model_invocations=int(status.get("model_invocations") or 0),
        acceptance_status=status.get("acceptance_status"),
        final_status=status.get("final_status"),
        verification_status=status.get("verification_status"),
        verification_cases=_safe_int(status.get("verification_cases")),
        verification_passed_cases=_safe_int(
            status.get("verification_passed_cases")
        ),
        provider_model=status.get("provider_model"),
        input_tokens=_safe_int(status.get("input_tokens")),
        output_tokens=_safe_int(status.get("output_tokens")),
        cost_usd=_safe_float(status.get("cost_usd")),
        human_intervention_count=_safe_int(
            status.get("human_intervention_count")
        ),
        dispatcher_call_count=_safe_int(status.get("dispatcher_call_count")),
        committed_side_effect_count=_safe_int(
            status.get("committed_side_effect_count")
        ),
        duplicate_side_effect_count=_safe_int(
            status.get("duplicate_side_effect_count")
        ),
        dispatch_authority_decision=status.get(
            "dispatch_authority_decision"
        ),
        fault_injection_applied=fault_applied,
        fault_injection_evidence=fault_evidence,
        receipt_fingerprint=status.get("receipt_fingerprint"),
        benchmark_result_status=status.get("benchmark_result_status"),
        benchmark_failure_class=status.get("benchmark_failure_class"),
        benchmark_recovered=status.get("benchmark_recovered")
        if status.get("benchmark_recovered") in (True, False)
        else None,
        benchmark_authority_escape=status.get("benchmark_authority_escape")
        if status.get("benchmark_authority_escape") in (True, False)
        else None,
        candidate_source=candidate_source,
        candidate_parse_error=candidate_error,
        terminal_pass_marker=terminal_pass,
        runtime_evidence_complete=runtime_evidence_complete,
    )


def collected_result_to_dict(
    result: KaviCollectedResult,
) -> dict[str, Any]:
    return asdict(result)
