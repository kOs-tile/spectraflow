"""Deterministic bounded-recovery harness used by reliability benchmarks.

This module does not dispatch agent tools or grant authority. Callers supply a
bounded operation and, optionally, a result validator. The harness records each
attempt and stops immediately after the first valid success.

The goal is to make retry/recovery behavior measurable without coupling
SPECTRAFLOW to a particular agent framework.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


class ResultValidationError(RuntimeError):
    """Raised when an operation returns a result that fails validation."""


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    outcome: str
    duration_ms: float
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RecoveryResult:
    success: bool
    recovered: bool
    attempts: int
    max_attempts: int
    retry_exhausted: bool
    result: Any = None
    terminal_error_type: str | None = None
    terminal_error_message: str | None = None
    records: tuple[AttemptRecord, ...] = ()


def run_recovery_trial(
    operation: Callable[[], Any],
    *,
    max_attempts: int = 3,
    validator: Callable[[Any], bool] | None = None,
    retryable_exceptions: tuple[type[BaseException], ...] = (
        TimeoutError,
        ConnectionError,
        ResultValidationError,
    ),
) -> RecoveryResult:
    """Execute an operation with bounded recovery and an inspectable trace.

    Important properties:
    - at most max_attempts operation calls;
    - no retry after the first valid success;
    - malformed results can become retryable failures via validator;
    - non-retryable exceptions terminate immediately.

    This is an evaluation primitive, not a production retry policy.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    records: list[AttemptRecord] = []

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            value = operation()
            if validator is not None and not validator(value):
                raise ResultValidationError("operation result failed validation")

            duration_ms = (time.perf_counter() - started) * 1000
            records.append(
                AttemptRecord(
                    attempt=attempt,
                    outcome="success",
                    duration_ms=duration_ms,
                )
            )
            return RecoveryResult(
                success=True,
                recovered=attempt > 1,
                attempts=attempt,
                max_attempts=max_attempts,
                retry_exhausted=False,
                result=value,
                records=tuple(records),
            )
        except BaseException as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            records.append(
                AttemptRecord(
                    attempt=attempt,
                    outcome="error",
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )

            retryable = isinstance(exc, retryable_exceptions)
            exhausted = attempt >= max_attempts
            if not retryable or exhausted:
                return RecoveryResult(
                    success=False,
                    recovered=False,
                    attempts=attempt,
                    max_attempts=max_attempts,
                    retry_exhausted=retryable and exhausted,
                    terminal_error_type=type(exc).__name__,
                    terminal_error_message=str(exc),
                    records=tuple(records),
                )

    raise AssertionError("bounded recovery loop exited unexpectedly")
