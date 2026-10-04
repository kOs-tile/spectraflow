"""Deterministic fault/recovery benchmark for the SPECTRAFLOW reliability harness."""

from __future__ import annotations

import json

from spectraflow.reliability import run_recovery_trial


def _sequence(*steps):
    values = iter(steps)

    def operation():
        step = next(values)
        if isinstance(step, BaseException):
            raise step
        return step

    return operation


def run_benchmark():
    cases = []

    scenarios = [
        (
            "clean_success",
            _sequence({"status": "complete"}),
            3,
            lambda value: value.get("status") == "complete",
            True,
            False,
        ),
        (
            "tool_timeout_recovery",
            _sequence(TimeoutError("synthetic tool timeout"), {"status": "complete"}),
            3,
            lambda value: value.get("status") == "complete",
            True,
            True,
        ),
        (
            "provider_failure_recovery",
            _sequence(ConnectionError("synthetic provider failure"), {"status": "complete"}),
            3,
            lambda value: value.get("status") == "complete",
            True,
            True,
        ),
        (
            "malformed_result_recovery",
            _sequence({"status": "partial"}, {"status": "complete"}),
            3,
            lambda value: value.get("status") == "complete",
            True,
            True,
        ),
        (
            "retry_budget_exhausted",
            _sequence(
                TimeoutError("synthetic timeout 1"),
                TimeoutError("synthetic timeout 2"),
            ),
            2,
            None,
            False,
            False,
        ),
        (
            "non_retryable_input_failure",
            _sequence(ValueError("synthetic invalid task input")),
            3,
            None,
            False,
            False,
        ),
    ]

    for label, operation, max_attempts, validator, expected_success, expected_recovered in scenarios:
        result = run_recovery_trial(
            operation,
            max_attempts=max_attempts,
            validator=validator,
        )
        cases.append(
            {
                "label": label,
                "expected_success": expected_success,
                "actual_success": result.success,
                "expected_recovered": expected_recovered,
                "actual_recovered": result.recovered,
                "attempts": result.attempts,
                "retry_exhausted": result.retry_exhausted,
                "terminal_error_type": result.terminal_error_type,
            }
        )

    exact = sum(
        1
        for case in cases
        if case["expected_success"] == case["actual_success"]
        and case["expected_recovered"] == case["actual_recovered"]
    )
    injected_recoverable = [
        case
        for case in cases
        if case["label"] in {
            "tool_timeout_recovery",
            "provider_failure_recovery",
            "malformed_result_recovery",
        }
    ]
    recovered = sum(1 for case in injected_recoverable if case["actual_recovered"])

    return {
        "cases": len(cases),
        "exact_outcome_matches": exact,
        "exact_outcome_rate": exact / len(cases),
        "recoverable_fault_cases": len(injected_recoverable),
        "recovered_fault_cases": recovered,
        "synthetic_recovery_rate": recovered / len(injected_recoverable),
        "results": cases,
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
