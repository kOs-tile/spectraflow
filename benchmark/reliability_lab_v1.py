"""SPECTRAFLOW Reliability Lab v1.

Evidence layers:
1. 85 sanitized historical KAVI agent sessions for corpus scale/status/tool density.
2. 26 sanitized Shared Execution Control tasks for richer runtime telemetry.
3. Existing deterministic side-effect corpus for duplicate/idempotency experiments.

Real-corpus metrics are reported only where source instrumentation exists.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from benchmark.execution_traces import run_benchmark as run_side_effect_benchmark
from spectraflow.reliability.corpus import (
    assess_execution_control_task,
    coverage,
    numeric_summary,
)


FIXTURES = Path(__file__).parent / "fixtures"
HISTORY_FIXTURE = FIXTURES / "kavi_agent_history_v1.json"
EXECUTION_FIXTURE = FIXTURES / "kavi_execution_control_v1.json"


def _count_by(rows, key):
    counts = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def run_benchmark() -> dict:
    history_payload = json.loads(HISTORY_FIXTURE.read_text(encoding="utf-8"))
    execution_payload = json.loads(EXECUTION_FIXTURE.read_text(encoding="utf-8"))

    sessions = history_payload["sessions"]
    tasks = execution_payload["tasks"]
    assessments = [assess_execution_control_task(row) for row in tasks]

    durations = [
        row.duration_seconds
        for row in assessments
        if row.duration_seconds is not None
    ]
    tool_counts = [
        int(row["tool_count"])
        for row in sessions
        if row.get("tool_count") is not None
    ]
    message_counts = [
        int(row["message_count"])
        for row in sessions
        if row.get("message_count") is not None
    ]

    verified_pass = sum(row.verified_pass for row in assessments)
    completed_unverified = sum(row.completed_unverified for row in assessments)
    explicit_failures = sum(row.explicit_failure for row in assessments)
    blocked = sum(row.blocked for row in assessments)
    unresolved = sum(row.unresolved for row in assessments)
    pre_model_failures = sum(row.pre_model_failure for row in assessments)
    post_model_failures = sum(row.post_model_failure for row in assessments)
    timeout_failures = sum(row.timeout_failure for row in assessments)
    retries = sum(row.retry_count for row in assessments)
    model_invocations = sum(row.model_invocations for row in assessments)
    total_attempts = sum(row.attempts for row in assessments)

    verification_observed = sum(
        bool(row.get("acceptance_status") or row.get("final_status"))
        for row in tasks
    )
    task_success_evaluable = sum(
        row.task_success_evaluable for row in assessments
    )
    idempotency_identity_observed = sum(
        row.get("idempotency_matches_task_id") is not None for row in tasks
    )

    metric_coverage = {
        "historical_session_status": asdict(
            coverage(
                len(sessions),
                len(sessions),
                "Status labels are historical session metadata; they are not semantic PASS verdicts.",
            )
        ),
        "tool_call_count": asdict(
            coverage(
                len(tool_counts),
                len(sessions),
                "Historical session records expose aggregate tool_count.",
            )
        ),
        "execution_status": asdict(
            coverage(
                len(tasks),
                len(tasks),
                "Shared Execution Control status is present for every task.",
            )
        ),
        "task_success": asdict(
            coverage(
                task_success_evaluable,
                len(tasks),
                "Evaluable for explicit failed/blocked terminal states or completed tasks with acceptance/final-status evidence.",
            )
        ),
        "retries_and_attempts": asdict(
            coverage(
                len(tasks),
                len(tasks),
                "attempts/max_retries are present, but this corpus contains no attempts>1 recovery event.",
            )
        ),
        "model_invocations": asdict(
            coverage(
                len(tasks),
                len(tasks),
                "Per-task model_invocations are present.",
            )
        ),
        "latency": asdict(
            coverage(
                len(durations),
                len(tasks),
                "Requires both start and terminal timestamps.",
            )
        ),
        "verification_evidence": asdict(
            coverage(
                verification_observed,
                len(tasks),
                "Explicit acceptance_status or final_status only.",
            )
        ),
        "idempotency_identity": asdict(
            coverage(
                idempotency_identity_observed,
                len(tasks),
                "Corpus records whether idempotency_key matched task_id; this does not prove side-effect deduplication.",
            )
        ),
        "human_intervention": asdict(
            coverage(
                0,
                len(tasks),
                "No explicit human_intervention field exists in the current source schema.",
            )
        ),
        "actual_token_usage": asdict(
            coverage(
                0,
                len(tasks),
                "max_output_tokens is a budget, not observed token usage.",
            )
        ),
        "actual_cost": asdict(
            coverage(
                0,
                len(tasks),
                "No normalized per-task cost field exists.",
            )
        ),
        "committed_side_effects": asdict(
            coverage(
                0,
                len(tasks),
                "Real queue snapshot does not expose committed-side-effect counts; use the separate controlled execution-trace corpus.",
            )
        ),
        "unauthorized_action": asdict(
            coverage(
                0,
                len(tasks),
                "Current queue records do not expose a normalized dispatch-time authority decision.",
            )
        ),
    }

    controlled = run_side_effect_benchmark()
    naive = next(
        row for row in controlled["results"]
        if row["task_id"] == "post_commit_timeout_naive"
    )
    idempotent = next(
        row for row in controlled["results"]
        if row["task_id"] == "post_commit_timeout_idempotent"
    )

    return {
        "schema_version": 1,
        "corpus": {
            "real_historical_agent_sessions": len(sessions),
            "real_execution_control_tasks": len(tasks),
            "real_records_total": len(sessions) + len(tasks),
            "history_provenance": history_payload["provenance"],
            "execution_provenance": execution_payload["provenance"],
        },
        "historical_sessions": {
            "status_distribution": _count_by(sessions, "status"),
            "tool_count": numeric_summary(tool_counts),
            "message_count": numeric_summary(message_counts),
            "total_tool_calls_recorded": sum(tool_counts),
            "total_messages_recorded": sum(message_counts),
        },
        "execution_control": {
            "status_distribution": _count_by(tasks, "status"),
            "actor_distribution": _count_by(tasks, "target_actor"),
            "risk_distribution": _count_by(tasks, "risk_class"),
            "verified_pass": verified_pass,
            "completed_unverified": completed_unverified,
            "explicit_failures": explicit_failures,
            "blocked": blocked,
            "unresolved": unresolved,
            "pre_model_failures": pre_model_failures,
            "post_model_failures": post_model_failures,
            "timeout_failures": timeout_failures,
            "total_attempts": total_attempts,
            "observed_retries": retries,
            "total_model_invocations": model_invocations,
            "duration_seconds": numeric_summary(durations),
            "assessments": [asdict(row) for row in assessments],
        },
        "metric_coverage": metric_coverage,
        "controlled_policy_pair": {
            "post_commit_timeout_naive": {
                "attempts": naive["attempts"],
                "committed_side_effects": naive["committed_side_effects"],
                "duplicate_side_effects": naive["duplicate_side_effects"],
                "safe_completion": naive["safe_completion"],
            },
            "post_commit_timeout_idempotent": {
                "attempts": idempotent["attempts"],
                "committed_side_effects": idempotent["committed_side_effects"],
                "duplicate_side_effects": idempotent["duplicate_side_effects"],
                "safe_completion": idempotent["safe_completion"],
            },
        },
        "claim_boundary": {
            "real_recovery_rate_available": False,
            "reason": "The 26-task real execution-control snapshot contains no attempts>1 recovery event.",
            "next_instrumentation": [
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "human_intervention_count",
                "committed_side_effect_count",
                "dispatch_authority_decision",
                "provider_model",
                "recovery_strategy",
            ],
        },
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
