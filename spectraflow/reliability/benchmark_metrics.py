"""Metric aggregation for Real Agent Benchmark v1."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Iterable

from spectraflow.reliability.benchmark_v1 import BenchmarkObservation


def _optional_mean(values):
    observed = [value for value in values if value is not None]
    return {
        "observed": len(observed),
        "mean": mean(observed) if observed else None,
    }


def summarize_benchmark(
    observations: Iterable[BenchmarkObservation],
) -> dict:
    rows = list(observations)
    for row in rows:
        row.validate()

    total = len(rows)
    verified_successes = sum(row.verified_success for row in rows)
    safe_completions = sum(row.safe_completion for row in rows)
    duplicate_observed = [
        row for row in rows if row.duplicate_side_effects is not None
    ]
    unauthorized_observed = [
        row for row in rows if row.unauthorized_actions is not None
    ]
    verification_observed = [
        row for row in rows if row.verification_failures is not None
    ]
    intervention_observed = [
        row for row in rows if row.human_interventions is not None
    ]

    duplicate_runs = sum((row.duplicate_side_effects or 0) > 0 for row in duplicate_observed)
    unauthorized_runs = sum((row.unauthorized_actions or 0) > 0 for row in unauthorized_observed)
    verification_failure_runs = sum(
        (row.verification_failures or 0) > 0 for row in verification_observed
    )
    intervention_runs = sum(
        (row.human_interventions or 0) > 0 for row in intervention_observed
    )

    by_failure: dict[str, list[BenchmarkObservation]] = defaultdict(list)
    for row in rows:
        by_failure[row.failure_mode.value].append(row)

    recovery_by_failure = {}
    for failure_mode, group in sorted(by_failure.items()):
        injected = [row for row in group if failure_mode != "none"]
        denominator = len(injected)
        recovery_by_failure[failure_mode] = {
            "runs": len(group),
            "recovery_denominator": denominator,
            "verified_recoveries": sum(row.verified_success for row in injected),
            "verified_recovery_rate": (
                sum(row.verified_success for row in injected) / denominator
                if denominator
                else None
            ),
            "safe_recoveries": sum(
                row.verified_success and row.safe_completion for row in injected
            ),
            "safe_recovery_rate": (
                sum(
                    row.verified_success and row.safe_completion
                    for row in injected
                )
                / denominator
                if denominator
                else None
            ),
        }

    policies = Counter(row.recovery_policy.value for row in rows)

    return {
        "runs": total,
        "verified_successes": verified_successes,
        "verified_success_rate": verified_successes / total if total else None,
        "safe_completions": safe_completions,
        "safe_completion_rate": safe_completions / total if total else None,
        "duplicate_side_effect_runs": duplicate_runs,
        "duplicate_side_effect_observed": len(duplicate_observed),
        "duplicate_side_effect_rate": (
            duplicate_runs / len(duplicate_observed) if duplicate_observed else None
        ),
        "unauthorized_action_runs": unauthorized_runs,
        "unauthorized_action_observed": len(unauthorized_observed),
        "unauthorized_action_rate": (
            unauthorized_runs / len(unauthorized_observed) if unauthorized_observed else None
        ),
        "verification_failure_runs": verification_failure_runs,
        "verification_failure_observed": len(verification_observed),
        "verification_failure_rate": (
            verification_failure_runs / len(verification_observed)
            if verification_observed
            else None
        ),
        "human_intervention_runs": intervention_runs,
        "human_intervention_observed": len(intervention_observed),
        "human_intervention_rate": (
            intervention_runs / len(intervention_observed)
            if intervention_observed
            else None
        ),
        "mean_attempts": mean(row.attempts for row in rows) if rows else None,
        "mean_model_invocations": (
            mean(row.model_invocations for row in rows) if rows else None
        ),
        "mean_tool_calls": mean(row.tool_calls for row in rows) if rows else None,
        "input_tokens": _optional_mean(row.input_tokens for row in rows),
        "output_tokens": _optional_mean(row.output_tokens for row in rows),
        "estimated_cost_usd": _optional_mean(
            row.estimated_cost_usd for row in rows
        ),
        "latency_ms": _optional_mean(row.latency_ms for row in rows),
        "policy_counts": dict(sorted(policies.items())),
        "recovery_by_failure_mode": recovery_by_failure,
    }
