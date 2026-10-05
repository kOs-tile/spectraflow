"""Adapter from live Reliability Lab records to Benchmark v1 observations."""

from __future__ import annotations

from spectraflow.reliability.benchmark_v1 import (
    BenchmarkObservation,
    FailureMode,
    RecoveryPolicy,
)
from spectraflow.reliability.collector import LiveRunRecord


_POLICY_MAP = {
    "baseline": RecoveryPolicy.BASELINE,
    "bounded_recovery": RecoveryPolicy.BOUNDED_RETRY,
    "idempotent_recovery": RecoveryPolicy.IDEMPOTENT_RECOVERY,
    "authority_aware": RecoveryPolicy.AUTHORITY_AWARE,
}

_FAILURE_MAP = {
    "post_commit_timeout_once": FailureMode.POST_COMMIT_TIMEOUT,
    "authority_revoked_before_dispatch": FailureMode.AUTHORITY_REVOKED,
}


def live_record_to_observation(record: LiveRunRecord) -> BenchmarkObservation:
    """Convert one collected live record without inventing missing evidence."""

    policy = _POLICY_MAP.get(record.policy)
    if policy is None:
        raise ValueError(f"unsupported live policy: {record.policy}")

    failure_mode = _FAILURE_MAP.get(record.fault_profile, FailureMode.UNKNOWN)

    if record.verification_status == "pass":
        verification_failures = 0
    elif record.verification_status in {"fail", "error"}:
        verification_failures = 1
    else:
        verification_failures = None

    if record.authority_escape_evidence_observed:
        unauthorized_actions = 1 if record.authority_escape else 0
    else:
        unauthorized_actions = None

    observation = BenchmarkObservation(
        task_id=record.task_id,
        scenario_id=record.run_id,
        recovery_policy=policy,
        failure_mode=failure_mode,
        terminal_status=record.queue_status,
        verified_success=record.agent_task_success,
        safe_completion=record.policy_safe,
        attempts=record.attempts,
        model_invocations=record.model_invocations,
        tool_calls=None,
        dispatcher_calls=record.dispatcher_calls,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        estimated_cost_usd=record.cost_usd,
        latency_ms=(
            record.latency_seconds * 1000
            if record.latency_seconds is not None
            else None
        ),
        human_interventions=record.human_intervention_count,
        duplicate_side_effects=record.duplicate_side_effect_count,
        unauthorized_actions=unauthorized_actions,
        verification_failures=verification_failures,
        source="kavi_reliability_live",
        evidence_id=record.receipt_fingerprint or record.run_id,
    )
    observation.validate()
    return observation


def live_records_to_observations(
    records: list[LiveRunRecord],
) -> list[BenchmarkObservation]:
    return [live_record_to_observation(record) for record in records]
