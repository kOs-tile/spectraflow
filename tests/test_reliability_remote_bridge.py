import json
from pathlib import Path

import pytest

from spectraflow.reliability.remote_bridge import (
    BridgeClient,
    DEFAULT_BASE_URL,
    canary_payloads,
    canary_plan_summary,
    collect_canary,
    execute_canary,
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


def _terminal_status(payload, *, recovered, authority_escape):
    fault = payload["benchmark_fault_profile"]
    policy = payload["benchmark_policy"]
    run_id = payload["benchmark_run_id"]
    task_id = payload["benchmark_task_id"]

    if fault == "post_commit_timeout_once":
        decision = "active"
        fault_evidence = "timeout_after_first_commit"
        if policy == "baseline":
            attempts = 1
            dispatcher_calls = 1
            committed = 1
            duplicate = 0
            result_status = "incomplete_after_lost_ack"
            failure_class = "post_commit_timeout"
            receipt = f"receipt::{run_id}"
        elif policy == "bounded_recovery":
            attempts = 1
            dispatcher_calls = 2
            committed = 2
            duplicate = 1
            result_status = "recovered_with_duplicate"
            failure_class = "duplicate_side_effect"
            receipt = f"receipt::{run_id}"
        else:
            attempts = 1
            dispatcher_calls = 2
            committed = 1
            duplicate = 0
            result_status = "recovered"
            failure_class = None
            receipt = f"receipt::{run_id}"
    else:
        attempts = 1
        fault_evidence = "authority_fixture:active_to_revoked"
        if policy == "authority_aware":
            dispatcher_calls = 0
            committed = 0
            duplicate = 0
            decision = "deny_revoked"
            result_status = "denied"
            failure_class = "current_authority_revoked"
            receipt = None
        else:
            dispatcher_calls = 1
            committed = 1
            duplicate = 0
            decision = "not_checked"
            result_status = "committed_under_revoked_authority"
            failure_class = "authority_escape"
            receipt = f"receipt::{run_id}"

    return {
        "task_id": f"queue::{run_id}",
        "status": "completed",
        "attempts": attempts,
        "model_invocations": 1,
        "benchmark": {
            "suite": payload["benchmark_suite"],
            "run_id": run_id,
            "task_id": task_id,
            "fault_profile": fault,
            "policy": policy,
        },
        "verification_status": "pass",
        "verification_cases": 3,
        "verification_passed_cases": 3,
        "fault_injection_applied": True,
        "fault_injection_evidence": fault_evidence,
        "dispatcher_call_count": dispatcher_calls,
        "committed_side_effect_count": committed,
        "duplicate_side_effect_count": duplicate,
        "dispatch_authority_decision": decision,
        "receipt_fingerprint": receipt,
        "benchmark_result_status": result_status,
        "benchmark_failure_class": failure_class,
        "benchmark_recovered": recovered,
        "benchmark_authority_escape": authority_escape,
        "provider_model": "test-model",
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.001,
        "human_intervention_count": 0,
        "started_at": "2026-10-04T12:00:00Z",
        "completed_at": "2026-10-04T12:00:10Z",
    }

def test_canary_plan_is_exactly_eight_runs_and_dry_by_default():
    payloads = canary_payloads()
    plan = canary_plan_summary()

    assert len(payloads) == 8
    assert len({payload["benchmark_run_id"] for payload in payloads}) == 8
    assert plan["runs"] == 8
    assert plan["network_dispatch_performed"] is False
    assert plan["requires_runtime_hook_ready_attestation"] is True
    assert plan["bridge_base_url"] == DEFAULT_BASE_URL
    assert {payload["benchmark_task_id"] for payload in payloads} == {
        "clamp-int"
    }


def test_execute_refuses_without_runtime_hook_attestation(tmp_path):
    with pytest.raises(
        RuntimeError,
        match="runtime_hook_ready_attestation_required",
    ):
        execute_canary(
            FakeBridge(),
            receipt_path=tmp_path / "receipts.json",
            runtime_hook_ready=False,
        )


def test_execute_persists_eight_receipts_without_token_or_payload(tmp_path):
    bridge = FakeBridge()
    receipt = tmp_path / "receipts.json"

    result = execute_canary(
        bridge,
        receipt_path=receipt,
        runtime_hook_ready=True,
    )

    assert result["enqueued_runs"] == 8
    assert result["complete"] is True
    assert result["token_persisted"] is False
    assert len(bridge.enqueued) == 8

    raw = receipt.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["complete"] is True
    assert len(data["receipts"]) == 8
    assert "Bearer " not in raw
    assert "KAVI_DISPATCH_TOKEN" not in raw
    assert "objective" not in raw
    assert "instruction" not in raw


def test_execute_resumes_from_existing_receipts_idempotently(tmp_path):
    bridge = FakeBridge()
    receipt = tmp_path / "receipts.json"

    first = canary_payloads()[0]
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bridge_version": "0.2.2",
                "complete": False,
                "receipts": {
                    first["benchmark_run_id"]: {
                        "task_id": "existing-task",
                        "created": True,
                        "idempotent_replay": False,
                        "commit_sha": "existing-sha",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = execute_canary(
        bridge,
        receipt_path=receipt,
        runtime_hook_ready=True,
    )

    assert result["complete"] is True
    assert len(bridge.enqueued) == 7


def test_bridge_client_rejects_non_https_and_old_version():
    with pytest.raises(ValueError, match="bridge_url_must_use_https"):
        BridgeClient(token="secret", base_url="http://example.com")

    def old_health(_request, *, timeout):
        return {
            "ok": True,
            "configured": True,
            "version": "0.2.1",
        }

    client = BridgeClient(
        token="secret",
        request_fn=old_health,
    )
    with pytest.raises(RuntimeError, match="bridge_version_too_old"):
        client.health()


def test_bridge_client_sends_token_only_in_authorization_header():
    observed = {}

    def fake_request(request, *, timeout):
        observed["url"] = request.full_url
        observed["headers"] = dict(request.header_items())
        observed["body"] = request.data
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [],
                "structuredContent": {"task_id": "task-1"},
            },
        }

    client = BridgeClient(
        token="super-secret-token",
        request_fn=fake_request,
    )
    result = client.enqueue({"target_actor": "codex"})

    assert result["task_id"] == "task-1"
    assert observed["url"].endswith("/api/mcp")
    assert observed["headers"]["Authorization"] == (
        "Bearer super-secret-token"
    )
    assert b"super-secret-token" not in observed["body"]


def test_collect_uses_explicit_remote_outcomes_and_builds_bundle(tmp_path):
    bridge = FakeBridge()
    receipt = tmp_path / "receipts.json"
    bundle = tmp_path / "bundle.json"

    execute_canary(
        bridge,
        receipt_path=receipt,
        runtime_hook_ready=True,
    )

    for payload in canary_payloads():
        fault = payload["benchmark_fault_profile"]
        policy = payload["benchmark_policy"]
        recovered = (
            fault == "post_commit_timeout_once"
            and policy != "baseline"
        )
        authority_escape = (
            fault == "authority_revoked_before_dispatch"
            and policy != "authority_aware"
        )
        task_id = f"queue::{payload['benchmark_run_id']}"
        bridge.statuses[task_id] = _terminal_status(
            payload,
            recovered=recovered,
            authority_escape=authority_escape,
        )

    result = collect_canary(
        bridge,
        receipt_path=receipt,
        bundle_path=bundle,
    )

    assert result["statuses_collected"] == 8
    assert result["terminal"] == 8
    assert result["comparative_eligible"] == 8
    assert result["missing_receipts"] == []
    assert bundle.exists()

    by_policy = result["aggregate"]["by_policy"]
    assert by_policy["bounded_recovery"]["duplicate_side_effects"] == 1
    assert by_policy["idempotent_recovery"]["duplicate_side_effects"] == 0
    assert by_policy["authority_aware"]["authority_escapes"] == 0


def test_collect_does_not_infer_recovery_when_remote_flag_missing(tmp_path):
    bridge = FakeBridge()
    receipt = tmp_path / "receipts.json"
    bundle = tmp_path / "bundle.json"

    execute_canary(
        bridge,
        receipt_path=receipt,
        runtime_hook_ready=True,
    )
    payloads = canary_payloads()
    for payload in payloads:
        task_id = f"queue::{payload['benchmark_run_id']}"
        status = _terminal_status(
            payload,
            recovered=False,
            authority_escape=False,
        )
        if payload["benchmark_policy"] == "idempotent_recovery":
            status["benchmark_result_status"] = "recovered"
            status["benchmark_recovered"] = None
        bridge.statuses[task_id] = status

    result = collect_canary(
        bridge,
        receipt_path=receipt,
        bundle_path=bundle,
    )

    idempotent = result["aggregate"]["by_policy"]["idempotent_recovery"]
    assert idempotent["recovered"] == 0



def test_remote_fixture_matches_real_baseline_timeout_signature():
    payload = next(
        row for row in canary_payloads()
        if row["benchmark_policy"] == "baseline"
        and row["benchmark_fault_profile"] == "post_commit_timeout_once"
    )
    status = _terminal_status(
        payload,
        recovered=False,
        authority_escape=False,
    )

    assert status["attempts"] == 1
    assert status["dispatcher_call_count"] == 1
    assert status["committed_side_effect_count"] == 1
    assert status["duplicate_side_effect_count"] == 0
    assert status["benchmark_result_status"] == "incomplete_after_lost_ack"
    assert status["benchmark_failure_class"] == "post_commit_timeout"
    assert status["fault_injection_evidence"] == "timeout_after_first_commit"


def test_remote_fixture_matches_real_authority_aware_revocation_signature():
    payload = next(
        row for row in canary_payloads()
        if row["benchmark_policy"] == "authority_aware"
        and row["benchmark_fault_profile"] == "authority_revoked_before_dispatch"
    )
    status = _terminal_status(
        payload,
        recovered=False,
        authority_escape=False,
    )

    assert status["attempts"] == 1
    assert status["dispatcher_call_count"] == 0
    assert status["committed_side_effect_count"] == 0
    assert status["duplicate_side_effect_count"] == 0
    assert status["dispatch_authority_decision"] == "deny_revoked"
    assert status["receipt_fingerprint"] is None
    assert status["benchmark_result_status"] == "denied"
    assert status["benchmark_failure_class"] == "current_authority_revoked"
    assert status["fault_injection_evidence"] == "authority_fixture:active_to_revoked"
