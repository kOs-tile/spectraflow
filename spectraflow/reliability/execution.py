"""Execution-trace assessment primitives for SPECTRAFLOW reliability evidence.

These helpers are observational. They do not dispatch tools, grant authority,
or prescribe a retry policy. They assess already-recorded execution facts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionTrace:
    task_id: str
    attempts: int
    dispatcher_calls: int
    committed_side_effects: int
    expected_side_effects: int
    operation_completed: bool
    verification_passed: bool
    recovered: bool


@dataclass(frozen=True)
class ExecutionAssessment:
    task_success: bool
    safe_completion: bool
    duplicate_side_effects: int
    duplicate_side_effect_detected: bool
    intervention_required: bool


def assess_execution_trace(trace: ExecutionTrace) -> ExecutionAssessment:
    """Classify completion and duplicate-effect risk from one execution trace."""
    duplicate_side_effects = max(
        0,
        trace.committed_side_effects - trace.expected_side_effects,
    )
    task_success = trace.operation_completed and trace.verification_passed
    duplicate_detected = duplicate_side_effects > 0
    safe_completion = task_success and not duplicate_detected
    intervention_required = not safe_completion

    return ExecutionAssessment(
        task_success=task_success,
        safe_completion=safe_completion,
        duplicate_side_effects=duplicate_side_effects,
        duplicate_side_effect_detected=duplicate_detected,
        intervention_required=intervention_required,
    )
