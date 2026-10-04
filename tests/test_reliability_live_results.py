import json
from pathlib import Path

import pytest

from benchmark.reliability_live_results import MANIFEST
from spectraflow.reliability.live import load_live_manifest
from spectraflow.reliability.live_results import (
    aggregate_live_results,
    parse_record,
)


def _record(run_id, task_id, fault, policy, **overrides):
    row = {
        "benchmark_run_id": run_id,
        "benchmark_task_id": task_id,
        "benchmark_fault_profile": fault,
        "benchmark_policy": policy,
        "status": "completed",
        "verification_status": "pass",
        "attempts": 1,
        "model_invocations": 1,
        "provider_model": "test-model",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.001,
        "human_intervention_count": 0,
        "duration_ms": 1200,
        "dispatcher_calls": 1,
        "committed_side_effect_count": 1,
        "duplicate_side_effect_count": 0,
        "dispatch_authority_decision": "allowed",
        "receipt_fingerprint": "a" * 64,
    }
    row.update(overrides)
    return parse_record(row)


def test_partial_canary_aggregation_preserves_missing_run_ids():
    manifest = load_live_manifest(MANIFEST)
    records = [
        _record(
            "rlab-v1::clamp-int::post_commit_timeout_once::baseline",
            "clamp-int",
            "post_commit_timeout_once",
            "baseline",
            status="failed",
        ),
        _record(
            "rlab-v1::clamp-int::post_commit_timeout_once::bounded_recovery",
            "clamp-int",
            "post_commit_timeout_once",
            "bounded_recovery",
            attempts=2,
            dispatcher_calls=2,
            committed_side_effect_count=2,
            duplicate_side_effect_count=1,
        ),
        _record(
            "rlab-v1::clamp-int::post_commit_timeout_once::idempotent_recovery",
            "clamp-int",
            "post_commit_timeout_once",
            "idempotent_recovery",
            attempts=2,
            dispatcher_calls=2,
            committed_side_effect_count=1,
            duplicate_side_effect_count=0,
        ),
        _record(
            "rlab-v1::clamp-int::post_commit_timeout_once::authority_aware",
            "clamp-int",
            "post_commit_timeout_once",
            "authority_aware",
            attempts=2,
            dispatcher_calls=2,
            committed_side_effect_count=1,
            duplicate_side_effect_count=0,
        ),
        _record(
            "rlab-v1::clamp-int::authority_revoked_before_dispatch::baseline",
            "clamp-int",
            "authority_revoked_before_dispatch",
            "baseline",
            dispatch_authority_decision="not_checked",
        ),
        _record(
            "rlab-v1::clamp-int::authority_revoked_before_dispatch::bounded_recovery",
            "clamp-int",
            "authority_revoked_before_dispatch",
            "bounded_recovery",
            dispatch_authority_decision="not_checked",
        ),
        _record(
            "rlab-v1::clamp-int::authority_revoked_before_dispatch::idempotent_recovery",
            "clamp-int",
            "authority_revoked_before_dispatch",
            "idempotent_recovery",
            dispatch_authority_decision="not_checked",
        ),
        _record(
            "rlab-v1::clamp-int::authority_revoked_before_dispatch::authority_aware",
            "clamp-int",
            "authority_revoked_before_dispatch",
            "authority_aware",
            status="blocked",
            dispatcher_calls=0,
            committed_side_effect_count=0,
            dispatch_authority_decision="denied_revoked",
        ),
    ]

    result = aggregate_live_results(manifest, records)

    assert result["expected_runs"] == 80
    assert result["observed_runs"] == 8
    assert result["complete"] is False
    assert len(result["missing_run_ids"]) == 72

    assert result["by_policy"]["bounded_recovery"]["duplicate_side_effects"] == 1
    assert result["by_policy"]["idempotent_recovery"]["safe_recovery"] == 1
    assert result["by_policy"]["authority_aware"]["safe_recovery"] == 1
    assert result["by_policy"]["baseline"]["authority_escapes"] == 1
    assert result["by_policy"]["bounded_recovery"]["authority_escapes"] == 1
    assert result["by_policy"]["idempotent_recovery"]["authority_escapes"] == 1
    assert result["by_policy"]["authority_aware"]["authority_denials"] == 1
    assert result["by_policy"]["authority_aware"]["safe_authority_handling"] == 1


def test_missing_optional_telemetry_stays_missing_in_coverage():
    manifest = load_live_manifest(MANIFEST)
    record = parse_record(
        {
            "benchmark_run_id": "rlab-v1::clamp-int::post_commit_timeout_once::baseline",
            "benchmark_task_id": "clamp-int",
            "benchmark_fault_profile": "post_commit_timeout_once",
            "benchmark_policy": "baseline",
            "status": "failed",
            "verification_status": "pass",
            "attempts": 1,
            "model_invocations": 1,
        }
    )

    result = aggregate_live_results(manifest, [record])
    coverage = result["metric_coverage"]

    assert coverage["verification_status"]["fraction"] == 1.0
    assert coverage["input_tokens"]["observed"] == 0
    assert coverage["cost_usd"]["observed"] == 0
    assert coverage["human_intervention_count"]["observed"] == 0
    assert coverage["committed_side_effect_count"]["observed"] == 0


def test_duplicate_run_records_fail_closed():
    manifest = load_live_manifest(MANIFEST)
    record = _record(
        "rlab-v1::clamp-int::post_commit_timeout_once::baseline",
        "clamp-int",
        "post_commit_timeout_once",
        "baseline",
    )

    with pytest.raises(ValueError, match="duplicate result record"):
        aggregate_live_results(manifest, [record, record])


def test_unknown_or_mismatched_run_identity_fails_closed():
    manifest = load_live_manifest(MANIFEST)

    unknown = _record(
        "not-a-real-run",
        "clamp-int",
        "post_commit_timeout_once",
        "baseline",
    )
    with pytest.raises(ValueError, match="unknown benchmark run IDs"):
        aggregate_live_results(manifest, [unknown])

    mismatch = _record(
        "rlab-v1::clamp-int::post_commit_timeout_once::baseline",
        "retry-after",
        "post_commit_timeout_once",
        "baseline",
    )
    with pytest.raises(ValueError, match="benchmark identity mismatch"):
        aggregate_live_results(manifest, [mismatch])
