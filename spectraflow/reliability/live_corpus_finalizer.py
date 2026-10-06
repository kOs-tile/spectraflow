"""Deterministic finalization for the Reliability Lab live v1 result corpus."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Iterable

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.benchmark_metrics import summarize_benchmark
from spectraflow.reliability.benchmark_v1 import BenchmarkObservation
from spectraflow.reliability.live import build_live_plan, load_live_manifest
from spectraflow.reliability.local_harness import evidence_scan


TERMINAL_STATUSES = {
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "superseded",
}

_POLICY_MAP = {
    "baseline": "baseline",
    "bounded_recovery": "bounded_retry",
    "idempotent_recovery": "idempotent_recovery",
    "authority_aware": "authority_aware",
}

_FAILURE_MAP = {
    "post_commit_timeout_once": "post_commit_timeout",
    "authority_revoked_before_dispatch": "authority_revoked",
}


def _expected_rows():
    manifest = load_live_manifest(MANIFEST)
    return {
        run.run_id: {
            "task_id": run.task_id,
            "recovery_policy": _POLICY_MAP[run.policy],
            "failure_mode": _FAILURE_MAP[run.fault_profile],
        }
        for run in build_live_plan(manifest)
    }


def _canonical_digest(rows: list[BenchmarkObservation]) -> str:
    canonical = "".join(
        json.dumps(
            row.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in sorted(rows, key=lambda value: value.scenario_id)
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def finalize_live_corpus(
    observations: Iterable[BenchmarkObservation],
) -> dict:
    rows = list(observations)
    blockers: list[str] = []

    for row in rows:
        row.validate()

    expected = _expected_rows()
    expected_ids = set(expected)
    scenario_ids = [row.scenario_id for row in rows]
    observed_ids = set(scenario_ids)

    if len(rows) != 80:
        blockers.append("live_corpus_requires_exactly_80_rows")
    if len(observed_ids) != len(rows):
        blockers.append("duplicate_scenario_ids")
    if observed_ids != expected_ids:
        blockers.append("live_corpus_run_ids_do_not_match_manifest")

    terminal = sum(
        row.terminal_status.lower() in TERMINAL_STATUSES
        for row in rows
    )
    if terminal != len(rows):
        blockers.append("non_terminal_live_corpus_row")

    identity_matches = 0
    for row in rows:
        contract = expected.get(row.scenario_id)
        if contract is None:
            continue
        matches = (
            row.task_id == contract["task_id"]
            and row.recovery_policy.value == contract["recovery_policy"]
            and row.failure_mode.value == contract["failure_mode"]
        )
        identity_matches += int(matches)
    if identity_matches != len(rows):
        blockers.append("live_corpus_identity_contract_mismatch")

    source_matches = sum(
        row.source == "kavi_reliability_live"
        and isinstance(row.evidence_id, str)
        and bool(row.evidence_id.strip())
        for row in rows
    )
    if source_matches != len(rows):
        blockers.append("live_corpus_source_evidence_incomplete")

    redaction_passed = sum(
        evidence_scan(row.to_dict())["passed"]
        for row in rows
    )
    if redaction_passed != len(rows):
        blockers.append("live_corpus_redaction_scan_failed")

    if blockers:
        raise ValueError(
            "corpus_finalization_blocked:" + ",".join(sorted(set(blockers)))
        )

    task_counts = Counter(row.task_id for row in rows)
    source_counts = Counter(row.source for row in rows)
    summary = summarize_benchmark(rows)

    return {
        "schema_version": 1,
        "benchmark": "spectraflow.reliability-live.v1",
        "observation_schema": "spectraflow.real-agent-benchmark.v1",
        "expected_runs": 80,
        "observed_runs": len(rows),
        "unique_run_ids": len(observed_ids),
        "terminal_runs": terminal,
        "identity_contract_matches": identity_matches,
        "redaction_scan_passed_runs": redaction_passed,
        "task_count": len(task_counts),
        "task_run_counts": dict(sorted(task_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "canonical_corpus_sha256": _canonical_digest(rows),
        "summary": summary,
        "claim_boundary": {
            "controlled_live_benchmark_not_production_rate": True,
            "failed_runs_preserved": True,
            "missing_telemetry_not_zero_filled": True,
            "corpus_order_does_not_change_digest": True,
        },
    }
