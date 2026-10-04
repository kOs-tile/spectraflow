from pathlib import Path

import pytest

from benchmark.kavi_dispatch_payloads import MANIFEST
from spectraflow.reliability.kavi_adapter import (
    RECOVERY_STRATEGIES,
    build_dispatch_batch,
    summarize_dispatch_batch,
)
from spectraflow.reliability.live import load_live_manifest


def test_dispatch_batch_fails_closed_without_isolation_attestation():
    manifest = load_live_manifest(MANIFEST)

    with pytest.raises(RuntimeError, match="isolated workspace adapter"):
        build_dispatch_batch(manifest)


def test_dispatch_batch_maps_all_eighty_runs_to_bounded_bridge_payloads():
    manifest = load_live_manifest(MANIFEST)
    result = summarize_dispatch_batch(
        manifest,
        isolation_ready=True,
    )

    assert result["payload_count"] == 80
    assert result["network_dispatch_performed"] is False
    assert result["by_policy"] == {
        "authority_aware": 20,
        "baseline": 20,
        "bounded_recovery": 20,
        "idempotent_recovery": 20,
    }
    assert result["by_fault_profile"] == {
        "authority_revoked_before_dispatch": 40,
        "post_commit_timeout_once": 40,
    }
    assert result["retry_budget_distribution"] == {"0": 20, "1": 60}

    payloads = result["payloads"]
    assert len({row["idempotency_key"] for row in payloads}) == 80
    assert all(row["target_actor"] == "codex" for row in payloads)
    assert all(row["risk_class"] == "NONE" for row in payloads)
    assert all(row["execution_mode"] == "AUTO" for row in payloads)
    assert all(row["max_retries"] in {0, 1} for row in payloads)
    assert all(
        row["preauthorized_plan_ref"] == "spectraflow-reliability-live-v1"
        for row in payloads
    )


def test_dispatch_payloads_preserve_policy_and_fault_identity():
    manifest = load_live_manifest(MANIFEST)
    payloads = build_dispatch_batch(
        manifest,
        isolation_ready=True,
    )

    for row in payloads:
        assert row["benchmark_suite"] == "spectraflow.reliability-live.v1"
        assert row["benchmark_run_id"] == row["idempotency_key"]
        assert row["benchmark_policy"] in RECOVERY_STRATEGIES
        assert row["recovery_strategy"] == RECOVERY_STRATEGIES[
            row["benchmark_policy"]
        ]
        assert row["benchmark_fault_profile"] in {
            "post_commit_timeout_once",
            "authority_revoked_before_dispatch",
        }
        assert "do not perform external side effects" in row["objective"].lower()
        assert "harness injects" in row["objective"].lower()
