from dataclasses import replace

import pytest

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.benchmark_v1 import (
    BenchmarkObservation,
    FailureMode,
    RecoveryPolicy,
)
from spectraflow.reliability.live import build_live_plan, load_live_manifest
from spectraflow.reliability.live_corpus_finalizer import finalize_live_corpus


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


def _rows():
    manifest = load_live_manifest(MANIFEST)
    rows = []
    for run in build_live_plan(manifest):
        rows.append(
            BenchmarkObservation(
                task_id=run.task_id,
                scenario_id=run.run_id,
                recovery_policy=_POLICY_MAP[run.policy],
                failure_mode=_FAILURE_MAP[run.fault_profile],
                terminal_status="completed",
                verified_success=False,
                safe_completion=False,
                attempts=1,
                model_invocations=1,
                tool_calls=None,
                dispatcher_calls=1,
                input_tokens=None,
                output_tokens=None,
                estimated_cost_usd=None,
                latency_ms=1000.0,
                human_interventions=0,
                duplicate_side_effects=0,
                unauthorized_actions=0,
                verification_failures=1,
                source="kavi_reliability_live",
                evidence_id=f"evidence::{run.run_id}",
            )
        )
    return rows


def test_complete_80_run_corpus_finalizes():
    result = finalize_live_corpus(_rows())

    assert result["observed_runs"] == 80
    assert result["unique_run_ids"] == 80
    assert result["terminal_runs"] == 80
    assert result["identity_contract_matches"] == 80
    assert result["redaction_scan_passed_runs"] == 80
    assert result["task_count"] == 10
    assert set(result["task_run_counts"].values()) == {8}
    assert result["source_counts"] == {"kavi_reliability_live": 80}
    assert len(result["canonical_corpus_sha256"]) == 64


def test_corpus_digest_is_independent_of_row_order():
    rows = _rows()

    forward = finalize_live_corpus(rows)
    reverse = finalize_live_corpus(list(reversed(rows)))

    assert (
        forward["canonical_corpus_sha256"]
        == reverse["canonical_corpus_sha256"]
    )


def test_missing_run_fails_closed():
    with pytest.raises(ValueError, match="exactly_80"):
        finalize_live_corpus(_rows()[:-1])


def test_duplicate_run_identity_fails_closed():
    rows = _rows()
    rows[-1] = replace(
        rows[-1],
        scenario_id=rows[0].scenario_id,
        task_id=rows[0].task_id,
        recovery_policy=rows[0].recovery_policy,
        failure_mode=rows[0].failure_mode,
    )

    with pytest.raises(ValueError, match="duplicate_scenario_ids"):
        finalize_live_corpus(rows)


def test_non_terminal_row_fails_closed():
    rows = _rows()
    rows[0] = replace(rows[0], terminal_status="running")

    with pytest.raises(ValueError, match="non_terminal_live_corpus_row"):
        finalize_live_corpus(rows)


def test_policy_or_fault_identity_drift_fails_closed():
    rows = _rows()
    rows[0] = replace(
        rows[0],
        recovery_policy=RecoveryPolicy.AUTHORITY_AWARE,
    )

    with pytest.raises(ValueError, match="identity_contract_mismatch"):
        finalize_live_corpus(rows)


def test_source_evidence_is_required():
    rows = _rows()
    rows[0] = replace(rows[0], source=None, evidence_id=None)

    with pytest.raises(ValueError, match="source_evidence_incomplete"):
        finalize_live_corpus(rows)


def test_secret_or_local_path_evidence_fails_redaction_scan():
    rows = _rows()
    rows[0] = replace(rows[0], evidence_id="C:\\secret\\runtime")

    with pytest.raises(ValueError, match="redaction_scan_failed"):
        finalize_live_corpus(rows)
