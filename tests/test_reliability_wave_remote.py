import json

import pytest

from spectraflow.reliability.policy_signatures import EXPECTED_SIGNATURES
from spectraflow.reliability.wave_remote import (
    collect_wave,
    execute_wave,
    reconcile_wave_bundle,
)


CANARY_SHA = "a" * 64
MODEL = "controlled-test-model"


def _promotion():
    return {
        "artifact_schema": "spectraflow.reliability-canary-promotion.v1",
        "promote_to_full_80": True,
        "blockers": [],
        "canary_evidence_sha256": CANARY_SHA,
        "controlled_provider_model": MODEL,
    }


def _previous(wave_index=1, *, digest="b" * 64):
    task_by_wave = {
        1: "retry-after",
        2: "stable-unique",
    }
    return {
        "artifact_schema": "spectraflow.reliability-wave-reconciliation.v1",
        "wave_pass": True,
        "wave_index": wave_index,
        "task_id": task_by_wave[wave_index],
        "canary_evidence_sha256": CANARY_SHA,
        "expected_provider_model": MODEL,
        "reconciliation_sha256": digest,
    }


class FakeClient:
    def __init__(self):
        self.payloads = {}
        self.enqueue_calls = 0
        self.status_calls = 0

    def health(self):
        return {
            "ok": True,
            "configured": True,
            "version": "0.2.2",
        }

    def enqueue(self, payload):
        self.enqueue_calls += 1
        run_id = payload["benchmark_run_id"]
        queue_id = f"queue::{run_id}"
        self.payloads[queue_id] = dict(payload)
        return {
            "task_id": queue_id,
            "created": True,
            "idempotent_replay": False,
            "commit_sha": f"commit::{self.enqueue_calls}",
        }

    def status(self, task_id):
        self.status_calls += 1
        payload = self.payloads[task_id]
        policy = payload["benchmark_policy"]
        fault = payload["benchmark_fault_profile"]
        run_id = payload["benchmark_run_id"]
        signature = EXPECTED_SIGNATURES[(policy, fault)]

        return {
            "task_id": task_id,
            "status": "completed",
            "attempts": 1,
            "model_invocations": 1,
            "benchmark": {
                "suite": payload["benchmark_suite"],
                "run_id": run_id,
                "task_id": payload["benchmark_task_id"],
                "policy": policy,
                "fault_profile": fault,
            },
            "verification_status": "pass",
            "verification_cases": 4,
            "verification_passed_cases": 4,
            "fault_injection_applied": True,
            "fault_injection_evidence": signature["fault_injection_evidence"],
            "dispatcher_call_count": signature["dispatcher_calls"],
            "committed_side_effect_count": signature["committed_side_effect_count"],
            "duplicate_side_effect_count": signature["duplicate_side_effect_count"],
            "dispatch_authority_decision": signature["dispatch_authority_decision"],
            "receipt_fingerprint": (
                f"receipt::{run_id}"
                if signature["committed_side_effect_count"] > 0
                else None
            ),
            "benchmark_result_status": signature["result_status"],
            "benchmark_failure_class": signature["failure_class"],
            "benchmark_recovered": signature["recovered"],
            "benchmark_authority_escape": signature["authority_escape"],
            "provider_model": MODEL,
            "input_tokens": 100,
            "output_tokens": 20,
            "cost_usd": 0.01,
            "human_intervention_count": 0,
            "started_at": "2026-10-06T06:00:00Z",
            "completed_at": "2026-10-06T06:00:10Z",
        }


def test_wave_one_execute_is_eight_runs_and_resumable(tmp_path):
    client = FakeClient()
    receipt = tmp_path / "wave-1-receipt.json"

    first = execute_wave(
        client,
        wave_index=1,
        promotion_artifact=_promotion(),
        previous_reconciliation=None,
        runtime_binding={"bound": True},
        receipt_path=receipt,
    )

    assert first["complete"] is True
    assert first["enqueued_runs"] == 8
    assert first["task_id"] == "retry-after"
    assert client.enqueue_calls == 8

    second = execute_wave(
        client,
        wave_index=1,
        promotion_artifact=_promotion(),
        previous_reconciliation=None,
        runtime_binding={"bound": True},
        receipt_path=receipt,
    )

    assert second["complete"] is True
    assert client.enqueue_calls == 8
    persisted = json.loads(receipt.read_text(encoding="utf-8"))
    assert len(persisted["receipts"]) == 8
    assert "KAVI_DISPATCH_TOKEN" not in receipt.read_text(encoding="utf-8")


def test_execute_fails_closed_without_promotion_or_runtime_binding(tmp_path):
    client = FakeClient()
    blocked = _promotion()
    blocked["promote_to_full_80"] = False

    with pytest.raises(RuntimeError, match="canary_promotion_pass_required"):
        execute_wave(
            client,
            wave_index=1,
            promotion_artifact=blocked,
            previous_reconciliation=None,
            runtime_binding={"bound": True},
            receipt_path=tmp_path / "blocked.json",
        )
    assert client.enqueue_calls == 0

    with pytest.raises(RuntimeError, match="runtime_hook_bound_attestation_required"):
        execute_wave(
            client,
            wave_index=1,
            promotion_artifact=_promotion(),
            previous_reconciliation=None,
            runtime_binding={"bound": False},
            receipt_path=tmp_path / "unbound.json",
        )
    assert client.enqueue_calls == 0


def test_wave_two_requires_matching_previous_reconciliation(tmp_path):
    client = FakeClient()

    with pytest.raises(RuntimeError, match="previous_wave_reconciliation_required"):
        execute_wave(
            client,
            wave_index=2,
            promotion_artifact=_promotion(),
            previous_reconciliation=None,
            runtime_binding={"bound": True},
            receipt_path=tmp_path / "wave-2.json",
        )

    wrong = _previous()
    wrong["canary_evidence_sha256"] = "c" * 64
    with pytest.raises(RuntimeError, match="canary_chain_mismatch"):
        execute_wave(
            client,
            wave_index=2,
            promotion_artifact=_promotion(),
            previous_reconciliation=wrong,
            runtime_binding={"bound": True},
            receipt_path=tmp_path / "wave-2-wrong.json",
        )

    assert client.enqueue_calls == 0


def test_execute_collect_reconcile_wave_one_end_to_end(tmp_path):
    client = FakeClient()
    receipt = tmp_path / "wave-1-receipt.json"
    bundle_path = tmp_path / "wave-1-bundle.json"

    execute_wave(
        client,
        wave_index=1,
        promotion_artifact=_promotion(),
        previous_reconciliation=None,
        runtime_binding={"bound": True},
        receipt_path=receipt,
    )
    collected = collect_wave(
        client,
        wave_index=1,
        receipt_path=receipt,
        bundle_path=bundle_path,
    )

    assert collected["statuses_collected"] == 8
    assert collected["missing_receipts"] == []
    assert collected["terminal"] == 8
    assert collected["comparative_eligible"] == 8
    assert client.status_calls == 8

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    result = reconcile_wave_bundle(
        bundle,
        wave_index=1,
        promotion_artifact=_promotion(),
        previous_reconciliation=None,
    )

    assert result["wave_pass"] is True
    assert result["wave_index"] == 1
    assert result["task_id"] == "retry-after"
    assert result["provider_model_matches"] == 8
    assert result["zero_intervention_runs"] == 8
    assert all(result["checks"].values())
    assert len(result["reconciliation_sha256"]) == 64


def test_reconcile_rejects_chain_mismatch(tmp_path):
    client = FakeClient()
    receipt = tmp_path / "wave-1-receipt.json"
    bundle_path = tmp_path / "wave-1-bundle.json"

    execute_wave(
        client,
        wave_index=1,
        promotion_artifact=_promotion(),
        previous_reconciliation=None,
        runtime_binding={"bound": True},
        receipt_path=receipt,
    )
    collect_wave(
        client,
        wave_index=1,
        receipt_path=receipt,
        bundle_path=bundle_path,
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["canary_evidence_sha256"] = "c" * 64

    with pytest.raises(RuntimeError, match="wave_bundle_canary_mismatch"):
        reconcile_wave_bundle(
            bundle,
            wave_index=1,
            promotion_artifact=_promotion(),
            previous_reconciliation=None,
        )
