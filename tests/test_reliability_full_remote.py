import json

import pytest

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.live import simulate_control_plane
from spectraflow.reliability.promotion import canary_run_specs
from spectraflow.reliability.remote_bridge import (
    canary_payloads,
    collect_full,
    execute_remaining,
    full_payloads,
    full_plan_summary,
    remaining_payloads_after_canary,
)


class FakeBridge:
    def __init__(self, *, version="0.2.2"):
        self.version = version
        self.enqueued = []
        self.statuses = {}

    def health(self):
        return {
            "ok": True,
            "configured": True,
            "version": self.version,
        }

    def enqueue(self, payload):
        self.enqueued.append(payload)
        run_id = payload["benchmark_run_id"]
        task_id = f"queue::{run_id}"
        return {
            "created": True,
            "idempotent_replay": False,
            "task_id": task_id,
            "commit_sha": f"sha-{len(self.enqueued)}",
        }

    def status(self, task_id):
        return self.statuses[task_id]


def _record_for(run):
    expected = simulate_control_plane(run)
    authority_decision = (
        "deny_revoked"
        if expected.authority_denied
        else (
            "not_checked"
            if run.fault_profile == "authority_revoked_before_dispatch"
            else "active"
        )
    )
    return LiveRunRecord(
        run_id=run.run_id,
        task_id=run.task_id,
        policy=run.policy,
        fault_profile=run.fault_profile,
        queue_status="completed",
        terminal_queue_status=True,
        verification_status="pass",
        verification_cases=3,
        verification_passed_cases=3,
        agent_task_success=True,
        fault_injection_applied=True,
        fault_injection_evidence=f"injector:{run.fault_profile}",
        attempts=expected.attempts,
        model_invocations=1,
        recovered=expected.recovered,
        recovery_evidence_observed=True,
        recovery_success=expected.recovered and expected.policy_safe,
        dispatcher_calls=expected.dispatcher_calls,
        committed_side_effect_count=expected.committed_side_effects,
        duplicate_side_effect_count=expected.duplicate_side_effects,
        dispatch_authority_decision=authority_decision,
        authority_escape=expected.authority_escape,
        authority_escape_evidence_observed=True,
        policy_safe=expected.policy_safe,
        comparative_eligible=True,
        provider_model="test-model",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.001,
        human_intervention_count=0,
        latency_seconds=10.0,
        receipt_fingerprint=(
            f"receipt::{run.run_id}"
            if expected.committed_side_effects > 0
            else None
        ),
        result_status="observed",
        failure_class=None,
    )


def _write_canary_collector(path, *, dirty=False):
    rows = [_record_for(run) for run in canary_run_specs()]
    if dirty:
        row = rows[0]
        rows[0] = LiveRunRecord(
            **{
                **row.__dict__,
                "fault_injection_applied": False,
                "fault_injection_evidence": None,
                "comparative_eligible": False,
            }
        )
    path.write_text(
        json.dumps({"records": [row.__dict__ for row in rows]}),
        encoding="utf-8",
    )


def _canary_receipts(path):
    receipts = {
        payload["benchmark_run_id"]: {
            "task_id": f"queue::{payload['benchmark_run_id']}",
            "created": True,
            "idempotent_replay": False,
            "commit_sha": "canary-sha",
        }
        for payload in canary_payloads()
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bridge_version": "0.2.2",
                "receipts": receipts,
                "complete": True,
            }
        ),
        encoding="utf-8",
    )


def _terminal_status(payload):
    run = next(
        run
        for run in canary_run_specs()
        if run.run_id == payload["benchmark_run_id"]
    ) if payload["benchmark_run_id"] in {
        run.run_id for run in canary_run_specs()
    } else None

    if run is None:
        from benchmark.reliability_live_plan import MANIFEST
        from spectraflow.reliability.live import build_live_plan, load_live_manifest

        run = next(
            item
            for item in build_live_plan(load_live_manifest(MANIFEST))
            if item.run_id == payload["benchmark_run_id"]
        )

    expected = simulate_control_plane(run)
    return {
        "task_id": f"queue::{run.run_id}",
        "status": "completed",
        "attempts": expected.attempts,
        "model_invocations": 1,
        "benchmark": {
            "suite": payload["benchmark_suite"],
            "run_id": run.run_id,
            "task_id": run.task_id,
            "fault_profile": run.fault_profile,
            "policy": run.policy,
        },
        "verification_status": "pass",
        "verification_cases": 3,
        "verification_passed_cases": 3,
        "fault_injection_applied": True,
        "fault_injection_evidence": f"injector:{run.fault_profile}",
        "dispatcher_call_count": expected.dispatcher_calls,
        "committed_side_effect_count": expected.committed_side_effects,
        "duplicate_side_effect_count": expected.duplicate_side_effects,
        "dispatch_authority_decision": (
            "deny_revoked"
            if expected.authority_denied
            else (
                "not_checked"
                if run.fault_profile == "authority_revoked_before_dispatch"
                else "active"
            )
        ),
        "receipt_fingerprint": (
            f"receipt::{run.run_id}"
            if expected.committed_side_effects > 0
            else None
        ),
        "benchmark_result_status": "observed",
        "benchmark_failure_class": None,
        "benchmark_recovered": expected.recovered,
        "benchmark_authority_escape": expected.authority_escape,
        "provider_model": "test-model",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.001,
        "human_intervention_count": 0,
        "started_at": "2026-10-04T12:00:00Z",
        "completed_at": "2026-10-04T12:00:10Z",
    }


def test_full_plan_is_eighty_and_remaining_is_seventy_two():
    full = full_payloads()
    remaining = remaining_payloads_after_canary()
    summary = full_plan_summary()

    assert len(full) == 80
    assert len({row["benchmark_run_id"] for row in full}) == 80
    assert len(remaining) == 72
    assert len({row["benchmark_run_id"] for row in remaining}) == 72
    assert summary["runs"] == 80
    assert summary["canary_runs"] == 8
    assert summary["remaining_runs"] == 72
    assert summary["unique_task_ids"] == 10
    assert summary["network_dispatch_performed"] is False


def test_remaining_execute_requires_fresh_runtime_preflight(tmp_path):
    collector = tmp_path / "canary.json"
    receipt = tmp_path / "receipts.json"
    _write_canary_collector(collector)
    _canary_receipts(receipt)

    with pytest.raises(RuntimeError, match="fresh_runtime_preflight_required"):
        execute_remaining(
            FakeBridge(),
            receipt_path=receipt,
            canary_collector_path=collector,
            runtime_preflight_ready=False,
        )


def test_dirty_canary_blocks_full_enqueue(tmp_path):
    collector = tmp_path / "canary.json"
    receipt = tmp_path / "receipts.json"
    _write_canary_collector(collector, dirty=True)
    _canary_receipts(receipt)
    bridge = FakeBridge()

    with pytest.raises(RuntimeError, match="canary_promotion_blocked"):
        execute_remaining(
            bridge,
            receipt_path=receipt,
            canary_collector_path=collector,
            runtime_preflight_ready=True,
        )

    assert bridge.enqueued == []


def test_clean_canary_enqueues_exactly_remaining_seventy_two(tmp_path):
    collector = tmp_path / "canary.json"
    receipt = tmp_path / "receipts.json"
    _write_canary_collector(collector)
    _canary_receipts(receipt)
    bridge = FakeBridge()

    result = execute_remaining(
        bridge,
        receipt_path=receipt,
        canary_collector_path=collector,
        runtime_preflight_ready=True,
    )

    assert result["canary_promoted"] is True
    assert result["remaining_runs"] == 72
    assert result["persisted_receipts"] == 80
    assert result["complete"] is True
    assert len(bridge.enqueued) == 72

    raw = receipt.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert len(payload["receipts"]) == 80
    assert payload["complete"] is True
    assert payload["phase"] == "full"
    assert "Bearer " not in raw
    assert "KAVI_DISPATCH_TOKEN" not in raw
    assert "objective" not in raw
    assert "instruction" not in raw


def test_remaining_execute_resumes_existing_full_receipts(tmp_path):
    collector = tmp_path / "canary.json"
    receipt = tmp_path / "receipts.json"
    _write_canary_collector(collector)
    _canary_receipts(receipt)

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    first_remaining = remaining_payloads_after_canary()[0]
    payload["receipts"][first_remaining["benchmark_run_id"]] = {
        "task_id": "existing-full-task",
        "created": True,
        "idempotent_replay": False,
        "commit_sha": "existing-sha",
    }
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    bridge = FakeBridge()
    result = execute_remaining(
        bridge,
        receipt_path=receipt,
        canary_collector_path=collector,
        runtime_preflight_ready=True,
    )

    assert result["complete"] is True
    assert len(bridge.enqueued) == 71


def test_full_collection_reads_all_eighty_statuses(tmp_path):
    collector = tmp_path / "canary.json"
    receipt = tmp_path / "receipts.json"
    bundle = tmp_path / "full-bundle.json"
    _write_canary_collector(collector)
    _canary_receipts(receipt)

    bridge = FakeBridge()
    execute_remaining(
        bridge,
        receipt_path=receipt,
        canary_collector_path=collector,
        runtime_preflight_ready=True,
    )

    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    by_run = {row["benchmark_run_id"]: row for row in full_payloads()}
    for run_id, entry in receipt_data["receipts"].items():
        bridge.statuses[entry["task_id"]] = _terminal_status(by_run[run_id])

    result = collect_full(
        bridge,
        receipt_path=receipt,
        bundle_path=bundle,
    )

    assert result["expected_runs"] == 80
    assert result["statuses_collected"] == 80
    assert result["missing_receipts"] == []
    assert result["terminal"] == 80
    assert result["comparative_eligible"] == 80
    assert result["complete_receipt_set"] is True
    assert bundle.exists()

    saved = json.loads(bundle.read_text(encoding="utf-8"))
    assert saved["phase"] == "full"
    assert saved["expected_runs"] == 80
    assert len(saved["queue_statuses"]) == 80
