"""Fail-closed reconciliation gate for one 8-run Reliability Lab task wave."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import string
from typing import Any, Iterable

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.live import load_live_manifest
from spectraflow.reliability.local_harness import evidence_scan
from spectraflow.reliability.policy_signatures import (
    EXPECTED_SIGNATURES,
    expected_run_ids,
    key_for_record,
    record_matches_expected_signature,
)


WAVE_SCHEMA = "spectraflow.reliability-wave-reconciliation.v1"


def _valid_sha256(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in string.hexdigits for char in value)
    )


def remaining_task_ids() -> list[str]:
    manifest = load_live_manifest(MANIFEST)
    return [task["task_id"] for task in manifest["tasks"][1:]]


def expected_task_for_wave(wave_index: int) -> str:
    tasks = remaining_task_ids()
    if wave_index < 1 or wave_index > len(tasks):
        raise ValueError("wave_index_out_of_range")
    return tasks[wave_index - 1]


def _coverage(rows: list[LiveRunRecord], field: str) -> dict[str, Any]:
    observed = sum(getattr(row, field) is not None for row in rows)
    return {
        "observed": observed,
        "total": len(rows),
        "fraction": (observed / len(rows)) if rows else None,
    }


def _canonical_records_sha256(rows: list[LiveRunRecord]) -> str:
    canonical = "".join(
        json.dumps(asdict(row), sort_keys=True, separators=(",", ":")) + "\n"
        for row in sorted(rows, key=lambda value: value.run_id)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _artifact_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate_wave_reconciliation(
    records: Iterable[LiveRunRecord],
    *,
    wave_index: int,
    expected_provider_model: str,
    canary_evidence_sha256: str,
    previous_wave_reconciliation_sha256: str | None = None,
) -> dict[str, Any]:
    rows = list(records)
    blockers: list[str] = []
    warnings: list[str] = []

    task_id = expected_task_for_wave(wave_index)
    expected_ids = expected_run_ids(task_id)
    run_ids = [row.run_id for row in rows]
    unique_run_ids = set(run_ids)
    expected_keys = set(EXPECTED_SIGNATURES)
    observed_keys = {key_for_record(row) for row in rows}

    if not expected_provider_model.strip():
        blockers.append("expected_provider_model_missing")
    if not _valid_sha256(canary_evidence_sha256):
        blockers.append("canary_evidence_sha256_invalid")

    if wave_index == 1:
        if previous_wave_reconciliation_sha256 is not None:
            blockers.append("unexpected_previous_wave_reconciliation")
    elif not _valid_sha256(previous_wave_reconciliation_sha256):
        blockers.append("previous_wave_reconciliation_sha256_required")

    if len(rows) != 8:
        blockers.append("wave_requires_exactly_8_runs")
    if len(unique_run_ids) != len(rows):
        blockers.append("duplicate_run_ids")
    if unique_run_ids != expected_ids:
        blockers.append("wave_run_ids_mismatch")
    if any(row.task_id != task_id for row in rows):
        blockers.append("wave_task_mismatch")
    if observed_keys != expected_keys:
        blockers.append("policy_fault_matrix_incomplete")

    terminal = sum(row.terminal_queue_status for row in rows)
    if terminal != len(rows):
        blockers.append("non_terminal_wave_run")

    verified = sum(
        row.agent_task_success
        and row.verification_status == "pass"
        and row.verification_cases is not None
        and row.verification_cases > 0
        and row.verification_passed_cases == row.verification_cases
        for row in rows
    )
    if verified != len(rows):
        blockers.append("canonical_verification_not_passed_for_all_runs")

    comparative = sum(row.comparative_eligible for row in rows)
    if comparative != len(rows):
        blockers.append("comparative_eligibility_incomplete")

    fault_attested = sum(
        row.fault_injection_applied is True
        and bool(row.fault_injection_evidence)
        for row in rows
    )
    if fault_attested != len(rows):
        blockers.append("fault_injection_not_attested_for_all_runs")

    recovery_evidence = sum(row.recovery_evidence_observed for row in rows)
    if recovery_evidence != len(rows):
        blockers.append("recovery_outcome_not_explicit_for_all_runs")

    authority_evidence = sum(
        row.authority_escape_evidence_observed for row in rows
    )
    if authority_evidence != len(rows):
        blockers.append("authority_outcome_not_explicit_for_all_runs")

    side_effect_evidence = sum(
        row.dispatcher_calls is not None
        and row.committed_side_effect_count is not None
        and row.duplicate_side_effect_count is not None
        for row in rows
    )
    if side_effect_evidence != len(rows):
        blockers.append("side_effect_evidence_incomplete")

    if any(row.attempts != 1 for row in rows):
        blockers.append("unexpected_agent_attempt_count")

    model_execution = sum(row.model_invocations == 1 for row in rows)
    if model_execution != len(rows):
        blockers.append("model_execution_not_exactly_once")

    model_matches = sum(
        row.provider_model == expected_provider_model for row in rows
    )
    if model_matches != len(rows):
        blockers.append("provider_model_drift")

    intervention_observed = sum(
        row.human_intervention_count is not None for row in rows
    )
    zero_intervention = sum(
        row.human_intervention_count == 0 for row in rows
    )
    if intervention_observed != len(rows):
        blockers.append("human_intervention_evidence_incomplete")
    elif zero_intervention != len(rows):
        blockers.append("human_intervention_observed")

    latency_observed = sum(row.latency_seconds is not None for row in rows)
    if latency_observed != len(rows):
        blockers.append("latency_evidence_incomplete")

    redaction_passed = sum(
        evidence_scan(asdict(row))["passed"] for row in rows
    )
    if redaction_passed != len(rows):
        blockers.append("evidence_redaction_scan_failed")

    checks = {
        f"{policy}::{fault}": any(
            key_for_record(row) == (policy, fault)
            and record_matches_expected_signature(row)
            for row in rows
        )
        for policy, fault in sorted(expected_keys)
    }
    if not all(checks.values()):
        blockers.append("expected_fault_policy_separation_not_observed")

    telemetry_coverage = {
        "provider_model": _coverage(rows, "provider_model"),
        "input_tokens": _coverage(rows, "input_tokens"),
        "output_tokens": _coverage(rows, "output_tokens"),
        "cost_usd": _coverage(rows, "cost_usd"),
        "human_intervention_count": _coverage(
            rows, "human_intervention_count"
        ),
        "latency_seconds": _coverage(rows, "latency_seconds"),
    }
    for field in ("input_tokens", "output_tokens", "cost_usd"):
        if telemetry_coverage[field]["observed"] != len(rows):
            warnings.append(f"{field}_coverage_incomplete")

    wave_evidence_sha256 = _canonical_records_sha256(rows)
    base = {
        "artifact_schema": WAVE_SCHEMA,
        "wave_index": wave_index,
        "task_id": task_id,
        "expected_provider_model": expected_provider_model,
        "canary_evidence_sha256": canary_evidence_sha256,
        "previous_wave_reconciliation_sha256": (
            previous_wave_reconciliation_sha256
        ),
        "wave_evidence_sha256": wave_evidence_sha256,
        "wave_pass": not blockers,
        "observed_runs": len(rows),
        "unique_run_ids": len(unique_run_ids),
        "terminal_runs": terminal,
        "verified_task_runs": verified,
        "comparative_eligible_runs": comparative,
        "fault_attested_runs": fault_attested,
        "recovery_evidence_runs": recovery_evidence,
        "authority_evidence_runs": authority_evidence,
        "redaction_scan_passed_runs": redaction_passed,
        "model_execution_runs": model_execution,
        "provider_model_matches": model_matches,
        "zero_intervention_runs": zero_intervention,
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "checks": checks,
        "telemetry_coverage": telemetry_coverage,
    }
    base["reconciliation_sha256"] = _artifact_sha256(base)
    return base
