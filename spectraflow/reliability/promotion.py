"""Fail-closed promotion gate from the 8-run canary to the remaining live batch."""

from __future__ import annotations

from typing import Any, Iterable

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.live import (
    build_live_plan,
    load_live_manifest,
    simulate_control_plane,
)


def canary_run_specs():
    manifest = load_live_manifest(MANIFEST)
    runs = build_live_plan(manifest)
    first_task_id = manifest["tasks"][0]["task_id"]
    selected = [run for run in runs if run.task_id == first_task_id]
    if len(selected) != 8:
        raise RuntimeError("canary_plan_must_have_eight_runs")
    return selected


def _coverage(rows: list[LiveRunRecord], field: str) -> dict[str, Any]:
    observed = sum(getattr(row, field) is not None for row in rows)
    return {
        "observed": observed,
        "total": len(rows),
        "fraction": observed / len(rows) if rows else None,
    }


def evaluate_canary_promotion(
    records: Iterable[LiveRunRecord],
) -> dict[str, Any]:
    """Require complete, verified, attested, expected canary behavior."""

    rows = list(records)
    expected_specs = {run.run_id: run for run in canary_run_specs()}
    expected_ids = set(expected_specs)
    observed_ids = [row.run_id for row in rows]
    observed_set = set(observed_ids)

    blockers: list[str] = []
    mismatches: dict[str, list[str]] = {}

    if len(rows) != 8:
        blockers.append("canary_record_count_not_8")
    if len(observed_set) != len(observed_ids):
        blockers.append("duplicate_canary_run_id")
    if observed_set != expected_ids:
        blockers.append("canary_run_identity_set_mismatch")

    by_id = {row.run_id: row for row in rows}

    for run_id, spec in expected_specs.items():
        row = by_id.get(run_id)
        if row is None:
            continue

        reasons: list[str] = []
        expected = simulate_control_plane(spec)

        if not row.terminal_queue_status:
            reasons.append("queue_not_terminal")
        if row.verification_status != "pass" or not row.agent_task_success:
            reasons.append("canonical_verification_not_pass")
        if not row.fault_injection_applied or not row.fault_injection_evidence:
            reasons.append("fault_injection_not_attested")
        if not row.comparative_eligible:
            reasons.append("record_not_comparative_eligible")

        if not row.recovery_evidence_observed:
            reasons.append("recovery_evidence_missing")
        if not row.authority_escape_evidence_observed:
            reasons.append("authority_evidence_missing")
        if row.duplicate_side_effect_count is None:
            reasons.append("duplicate_effect_evidence_missing")
        if row.committed_side_effect_count is None:
            reasons.append("committed_effect_evidence_missing")
        if row.dispatcher_calls is None:
            reasons.append("dispatcher_call_evidence_missing")

        if row.attempts != expected.attempts:
            reasons.append("attempt_count_mismatch")
        if row.recovered != expected.recovered:
            reasons.append("recovery_outcome_mismatch")
        if row.dispatcher_calls is not None and row.dispatcher_calls != expected.dispatcher_calls:
            reasons.append("dispatcher_call_count_mismatch")
        if (
            row.committed_side_effect_count is not None
            and row.committed_side_effect_count != expected.committed_side_effects
        ):
            reasons.append("committed_effect_count_mismatch")
        if (
            row.duplicate_side_effect_count is not None
            and row.duplicate_side_effect_count != expected.duplicate_side_effects
        ):
            reasons.append("duplicate_effect_count_mismatch")
        if row.authority_escape != expected.authority_escape:
            reasons.append("authority_escape_mismatch")
        if row.policy_safe != expected.policy_safe:
            reasons.append("policy_safety_mismatch")

        if expected.authority_denied:
            if row.dispatch_authority_decision != "deny_revoked":
                reasons.append("authority_denial_decision_mismatch")
        elif spec.fault_profile == "authority_revoked_before_dispatch":
            if not row.authority_escape:
                reasons.append("expected_authority_escape_not_observed")

        if (row.committed_side_effect_count or 0) > 0 and not row.receipt_fingerprint:
            reasons.append("receipt_fingerprint_missing")

        if reasons:
            mismatches[run_id] = reasons

    if mismatches:
        blockers.append("canary_expected_behavior_mismatch")

    telemetry = {
        field: _coverage(rows, field)
        for field in (
            "provider_model",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "human_intervention_count",
            "latency_seconds",
        )
    }

    promoted = not blockers

    return {
        "schema_version": 1,
        "checkpoint": "spectraflow.canary-promotion.v1",
        "expected_runs": 8,
        "observed_runs": len(rows),
        "unique_run_ids": len(observed_set),
        "exact_expected_run_set": observed_set == expected_ids,
        "promoted": promoted,
        "blockers": blockers,
        "mismatches": mismatches,
        "telemetry_coverage": telemetry,
        "claim_boundary": {
            "promotion_is_integration_gate_not_production_reliability_claim": True,
            "sparse_cost_or_intervention_telemetry_is_reported_not_invented": True,
        },
    }
