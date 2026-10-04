"""Reproducible execution-trace benchmark for SPECTRAFLOW reliability evidence.

The scenarios execute real Python callables against an in-memory side-effect
ledger. They remain synthetic/reproducible and are not production traces.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from spectraflow.reliability import (
    ExecutionTrace,
    assess_execution_trace,
    run_recovery_trial,
)


class LedgerDispatcher:
    def __init__(self) -> None:
        self.dispatcher_calls = 0
        self.committed_side_effects = 0
        self._receipts: dict[str, dict] = {}

    def dispatch(self, *, idempotency_key: str | None = None) -> dict:
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


def _trace(
    *,
    task_id: str,
    result,
    dispatcher: LedgerDispatcher,
    expected_side_effects: int,
    verification_passed: bool,
) -> ExecutionTrace:
    return ExecutionTrace(
        task_id=task_id,
        attempts=result.attempts,
        dispatcher_calls=dispatcher.dispatcher_calls,
        committed_side_effects=dispatcher.committed_side_effects,
        expected_side_effects=expected_side_effects,
        operation_completed=result.success,
        verification_passed=verification_passed,
        recovered=result.recovered,
    )


def run_benchmark() -> dict:
    rows = []

    # 1. Clean side-effecting success.
    dispatcher = LedgerDispatcher()
    clean = run_recovery_trial(dispatcher.dispatch, max_attempts=3)
    trace = _trace(
        task_id="clean_success",
        result=clean,
        dispatcher=dispatcher,
        expected_side_effects=1,
        verification_passed=clean.success,
    )
    rows.append((trace, assess_execution_trace(trace)))

    # 2. Retryable failure occurs before dispatch; only one side effect commits.
    dispatcher = LedgerDispatcher()
    attempts = 0

    def pre_dispatch_timeout():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("synthetic timeout before dispatch")
        return dispatcher.dispatch()

    recovered = run_recovery_trial(pre_dispatch_timeout, max_attempts=3)
    trace = _trace(
        task_id="pre_dispatch_timeout_recovery",
        result=recovered,
        dispatcher=dispatcher,
        expected_side_effects=1,
        verification_passed=recovered.success,
    )
    rows.append((trace, assess_execution_trace(trace)))

    # 3. Side effect commits, response is lost, blind retry commits it again.
    dispatcher = LedgerDispatcher()
    attempts = 0

    def post_commit_timeout_naive():
        nonlocal attempts
        attempts += 1
        result = dispatcher.dispatch()
        if attempts == 1:
            raise TimeoutError("synthetic timeout after commit")
        return result

    naive = run_recovery_trial(post_commit_timeout_naive, max_attempts=3)
    trace = _trace(
        task_id="post_commit_timeout_naive",
        result=naive,
        dispatcher=dispatcher,
        expected_side_effects=1,
        verification_passed=naive.success,
    )
    rows.append((trace, assess_execution_trace(trace)))

    # 4. Same post-commit timeout with a stable idempotency key.
    dispatcher = LedgerDispatcher()
    attempts = 0
    stable_key = "task-004"

    def post_commit_timeout_idempotent():
        nonlocal attempts
        attempts += 1
        result = dispatcher.dispatch(idempotency_key=stable_key)
        if attempts == 1:
            raise TimeoutError("synthetic timeout after commit")
        return result

    idempotent = run_recovery_trial(
        post_commit_timeout_idempotent,
        max_attempts=3,
    )
    trace = _trace(
        task_id="post_commit_timeout_idempotent",
        result=idempotent,
        dispatcher=dispatcher,
        expected_side_effects=1,
        verification_passed=idempotent.success,
    )
    rows.append((trace, assess_execution_trace(trace)))

    # 5. Successful dispatch whose final verification fails.
    dispatcher = LedgerDispatcher()
    dispatched = run_recovery_trial(dispatcher.dispatch, max_attempts=1)
    trace = _trace(
        task_id="verification_failure",
        result=dispatched,
        dispatcher=dispatcher,
        expected_side_effects=1,
        verification_passed=False,
    )
    rows.append((trace, assess_execution_trace(trace)))

    # 6. Provider/tool failure never reaches dispatcher.
    dispatcher = LedgerDispatcher()

    def terminal_failure():
        raise ConnectionError("synthetic provider unavailable")

    failed = run_recovery_trial(terminal_failure, max_attempts=2)
    trace = _trace(
        task_id="terminal_failure",
        result=failed,
        dispatcher=dispatcher,
        expected_side_effects=0,
        verification_passed=False,
    )
    rows.append((trace, assess_execution_trace(trace)))

    results = []
    for trace, assessment in rows:
        row = asdict(trace)
        row.update(asdict(assessment))
        results.append(row)

    safe = sum(1 for row in results if row["safe_completion"])
    duplicates = sum(
        row["duplicate_side_effects"]
        for row in results
    )
    interventions = sum(
        1 for row in results if row["intervention_required"]
    )

    return {
        "cases": len(results),
        "safe_completions": safe,
        "unsafe_or_incomplete": len(results) - safe,
        "duplicate_side_effects": duplicates,
        "intervention_required": interventions,
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
