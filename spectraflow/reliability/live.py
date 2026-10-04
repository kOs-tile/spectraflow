"""Live Reliability Lab plan and deterministic control-plane fault policies.

This module prepares the 80-run experiment and simulates only the control-plane
fault/recovery layer. Agent task correctness, model usage, cost, and latency must
come from actual live executions.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spectraflow.reliability.execution import ExecutionTrace, assess_execution_trace
from spectraflow.reliability.recovery import run_recovery_trial


@dataclass(frozen=True)
class LivePolicy:
    name: str
    max_attempts: int
    stable_idempotency: bool
    enforce_current_authority: bool


@dataclass(frozen=True)
class LiveRunSpec:
    run_id: str
    task_id: str
    function: str
    objective: str
    fault_profile: str
    policy: str
    max_attempts: int
    stable_idempotency: bool
    enforce_current_authority: bool
    external_side_effects: bool = False
    workspace_mode: str = "isolated_copy"


@dataclass(frozen=True)
class LivePolicyOutcome:
    run_id: str
    task_id: str
    fault_profile: str
    policy: str
    attempts: int
    dispatcher_calls: int
    committed_side_effects: int
    duplicate_side_effects: int
    task_completed: bool
    recovered: bool
    authority_denied: bool
    authority_escape: bool
    policy_safe: bool


POLICIES = (
    LivePolicy(
        name="baseline",
        max_attempts=1,
        stable_idempotency=False,
        enforce_current_authority=False,
    ),
    LivePolicy(
        name="bounded_recovery",
        max_attempts=2,
        stable_idempotency=False,
        enforce_current_authority=False,
    ),
    LivePolicy(
        name="idempotent_recovery",
        max_attempts=2,
        stable_idempotency=True,
        enforce_current_authority=False,
    ),
    LivePolicy(
        name="authority_aware",
        max_attempts=2,
        stable_idempotency=True,
        enforce_current_authority=True,
    ),
)

FAULT_PROFILES = (
    "post_commit_timeout_once",
    "authority_revoked_before_dispatch",
)


class _LedgerDispatcher:
    def __init__(self) -> None:
        self.dispatcher_calls = 0
        self.committed_side_effects = 0
        self._receipts: dict[str, dict[str, Any]] = {}

    def dispatch(self, *, idempotency_key: str | None = None) -> dict[str, Any]:
        self.dispatcher_calls += 1
        if idempotency_key and idempotency_key in self._receipts:
            return self._receipts[idempotency_key]

        self.committed_side_effects += 1
        result = {
            "status": "complete",
            "effect_number": self.committed_side_effects,
        }
        if idempotency_key:
            self._receipts[idempotency_key] = result
        return result


def load_live_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_live_plan(manifest: dict[str, Any]) -> list[LiveRunSpec]:
    tasks = manifest["tasks"]
    workspace = manifest["workspace_contract"]
    runs: list[LiveRunSpec] = []

    for task in tasks:
        for fault_profile in FAULT_PROFILES:
            for policy in POLICIES:
                run_id = (
                    f"rlab-v1::{task['task_id']}::{fault_profile}::{policy.name}"
                )
                runs.append(
                    LiveRunSpec(
                        run_id=run_id,
                        task_id=task["task_id"],
                        function=task["function"],
                        objective=task["objective"],
                        fault_profile=fault_profile,
                        policy=policy.name,
                        max_attempts=policy.max_attempts,
                        stable_idempotency=policy.stable_idempotency,
                        enforce_current_authority=policy.enforce_current_authority,
                        external_side_effects=bool(
                            workspace.get("external_side_effects", False)
                        ),
                        workspace_mode=str(
                            workspace.get("workspace_mode", "isolated_copy")
                        ),
                    )
                )

    return runs


def _policy(name: str) -> LivePolicy:
    return next(policy for policy in POLICIES if policy.name == name)


def simulate_control_plane(run: LiveRunSpec) -> LivePolicyOutcome:
    """Simulate deterministic dispatch behavior for one run spec.

    This does not execute the agent coding task. It proves the expected policy
    behavior around an already-verified task result.
    """

    policy = _policy(run.policy)
    dispatcher = _LedgerDispatcher()
    stable_key = run.run_id if policy.stable_idempotency else None

    if run.fault_profile == "post_commit_timeout_once":
        call_count = 0

        def operation():
            nonlocal call_count
            call_count += 1
            result = dispatcher.dispatch(idempotency_key=stable_key)
            if call_count == 1:
                raise TimeoutError("injected timeout after commit")
            return result

        result = run_recovery_trial(
            operation,
            max_attempts=policy.max_attempts,
        )
        trace = ExecutionTrace(
            task_id=run.run_id,
            attempts=result.attempts,
            dispatcher_calls=dispatcher.dispatcher_calls,
            committed_side_effects=dispatcher.committed_side_effects,
            expected_side_effects=1,
            operation_completed=result.success,
            verification_passed=result.success,
            recovered=result.recovered,
        )
        assessment = assess_execution_trace(trace)

        return LivePolicyOutcome(
            run_id=run.run_id,
            task_id=run.task_id,
            fault_profile=run.fault_profile,
            policy=run.policy,
            attempts=result.attempts,
            dispatcher_calls=dispatcher.dispatcher_calls,
            committed_side_effects=dispatcher.committed_side_effects,
            duplicate_side_effects=assessment.duplicate_side_effects,
            task_completed=result.success,
            recovered=result.recovered,
            authority_denied=False,
            authority_escape=False,
            policy_safe=assessment.safe_completion,
        )

    if run.fault_profile == "authority_revoked_before_dispatch":
        if policy.enforce_current_authority:
            return LivePolicyOutcome(
                run_id=run.run_id,
                task_id=run.task_id,
                fault_profile=run.fault_profile,
                policy=run.policy,
                attempts=1,
                dispatcher_calls=0,
                committed_side_effects=0,
                duplicate_side_effects=0,
                task_completed=False,
                recovered=False,
                authority_denied=True,
                authority_escape=False,
                policy_safe=True,
            )

        dispatcher.dispatch(idempotency_key=stable_key)
        return LivePolicyOutcome(
            run_id=run.run_id,
            task_id=run.task_id,
            fault_profile=run.fault_profile,
            policy=run.policy,
            attempts=1,
            dispatcher_calls=dispatcher.dispatcher_calls,
            committed_side_effects=dispatcher.committed_side_effects,
            duplicate_side_effects=0,
            task_completed=True,
            recovered=False,
            authority_denied=False,
            authority_escape=True,
            policy_safe=False,
        )

    raise ValueError(f"unknown fault profile: {run.fault_profile}")


def summarize_control_plane(
    runs: list[LiveRunSpec],
) -> dict[str, Any]:
    outcomes = [simulate_control_plane(run) for run in runs]
    by_policy: dict[str, dict[str, int]] = {}

    for policy in POLICIES:
        rows = [row for row in outcomes if row.policy == policy.name]
        by_policy[policy.name] = {
            "runs": len(rows),
            "policy_safe": sum(row.policy_safe for row in rows),
            "recovered": sum(row.recovered for row in rows),
            "duplicate_side_effects": sum(
                row.duplicate_side_effects for row in rows
            ),
            "authority_escapes": sum(row.authority_escape for row in rows),
            "authority_denials": sum(row.authority_denied for row in rows),
        }

    return {
        "runs": len(outcomes),
        "by_policy": by_policy,
        "outcomes": [asdict(row) for row in outcomes],
    }


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(fn, args):
    try:
        return ("value", fn(*args))
    except Exception as exc:
        return ("error", type(exc).__name__)


def verify_task(
    task: dict[str, Any],
    *,
    candidate_path: str | Path,
    reference_path: str | Path,
) -> dict[str, Any]:
    candidate = _load_module(Path(candidate_path), "rlab_candidate")
    reference = _load_module(Path(reference_path), "rlab_reference")

    candidate_fn = getattr(candidate, task["function"])
    reference_fn = getattr(reference, task["function"])

    cases = []
    passed = 0
    for case in task["cases"]:
        expected = _call(reference_fn, case.get("args", []))
        actual = _call(candidate_fn, case.get("args", []))
        ok = actual == expected
        passed += int(ok)
        cases.append(
            {
                "args": case.get("args", []),
                "expected": expected,
                "actual": actual,
                "passed": ok,
            }
        )

    return {
        "task_id": task["task_id"],
        "cases": len(cases),
        "passed_cases": passed,
        "passed": passed == len(cases),
        "results": cases,
    }
