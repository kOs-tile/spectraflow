"""Remote KAVI Dispatch Bridge client for Reliability Lab canary runs.

The client is narrow by design:
- stable production alias by default;
- bearer token supplied only at runtime;
- exact enqueue_task / get_task_status tools only;
- no arbitrary GitHub writes;
- no secret persistence;
- no automatic canary execution.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.collector import (
    aggregate_live_records,
    collect_live_run,
    harness_evidence_from_queue_status,
)
from spectraflow.reliability.kavi_adapter import build_dispatch_batch
from spectraflow.reliability.live import build_live_plan, load_live_manifest


DEFAULT_BASE_URL = "https://kavi-dispatch-bridge.vercel.app"
MIN_BRIDGE_VERSION = (0, 2, 2)


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = str(value).split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("invalid_bridge_version")
    return tuple(int(part) for part in parts)


def _json_request(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"bridge_http_{exc.code}:{body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"bridge_transport_error:{exc.reason}") from exc

    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError("bridge_invalid_json") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("bridge_unexpected_response")
    return payload


@dataclass
class BridgeClient:
    token: str
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 20.0
    request_fn: Callable[..., dict[str, Any]] = _json_request

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("bridge_url_must_use_https")
        if not self.token:
            raise ValueError("missing_dispatch_token")

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/api/mcp"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}/api/health"

    def health(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.health_url,
            method="GET",
            headers={"Accept": "application/json"},
        )
        payload = self.request_fn(request, timeout=self.timeout_seconds)
        if payload.get("ok") is not True or payload.get("configured") is not True:
            raise RuntimeError("bridge_health_not_ready")
        version = payload.get("version")
        if _version_tuple(str(version)) < MIN_BRIDGE_VERSION:
            raise RuntimeError("bridge_version_too_old")
        return payload

    def _tool_call(
        self,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool,
                    "arguments": arguments,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.mcp_url,
            method="POST",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": "2025-11-25",
            },
        )
        payload = self.request_fn(request, timeout=self.timeout_seconds)
        if payload.get("error"):
            raise RuntimeError("bridge_rpc_error")

        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("bridge_missing_result")
        if result.get("isError") is True:
            structured = result.get("structuredContent")
            raise RuntimeError(
                "bridge_tool_error:"
                + json.dumps(structured, sort_keys=True)[:800]
            )
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise RuntimeError("bridge_missing_structured_content")
        return structured

    def enqueue(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._tool_call("enqueue_task", arguments)

    def status(self, task_id: str) -> dict[str, Any]:
        return self._tool_call(
            "get_task_status",
            {"task_id": task_id},
        )


def _manifest_and_runs():
    manifest = load_live_manifest(MANIFEST)
    runs = build_live_plan(manifest)
    return manifest, runs


def canary_payloads() -> list[dict[str, Any]]:
    manifest, runs = _manifest_and_runs()
    first_task_id = manifest["tasks"][0]["task_id"]
    payloads = build_dispatch_batch(manifest, isolation_ready=True)
    by_run = {payload["benchmark_run_id"]: payload for payload in payloads}

    selected = [
        by_run[run.run_id]
        for run in runs
        if run.task_id == first_task_id
    ]
    if len(selected) != 8:
        raise RuntimeError("canary_plan_must_have_eight_runs")
    return selected


def canary_plan_summary() -> dict[str, Any]:
    payloads = canary_payloads()
    return {
        "suite": payloads[0]["benchmark_suite"],
        "task_id": payloads[0]["benchmark_task_id"],
        "runs": len(payloads),
        "run_ids": [payload["benchmark_run_id"] for payload in payloads],
        "policies": sorted({payload["benchmark_policy"] for payload in payloads}),
        "fault_profiles": sorted(
            {payload["benchmark_fault_profile"] for payload in payloads}
        ),
        "bridge_base_url": DEFAULT_BASE_URL,
        "network_dispatch_performed": False,
        "requires_runtime_hook_ready_attestation": True,
    }


def _safe_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
        raise ValueError("secret_persistence_rejected")
    target.write_text(encoded, encoding="utf-8")


def execute_canary(
    client: BridgeClient,
    *,
    receipt_path: str | Path,
    runtime_hook_ready: bool,
) -> dict[str, Any]:
    if not runtime_hook_ready:
        raise RuntimeError("runtime_hook_ready_attestation_required")

    health = client.health()
    receipts: dict[str, Any] = {}
    receipt_file = Path(receipt_path).expanduser().resolve()
    if receipt_file.exists():
        existing = json.loads(receipt_file.read_text(encoding="utf-8"))
        receipts.update(existing.get("receipts") or {})

    for payload in canary_payloads():
        run_id = payload["benchmark_run_id"]
        if run_id in receipts and receipts[run_id].get("task_id"):
            continue

        try:
            result = client.enqueue(payload)
        except Exception:
            _safe_write_json(
                receipt_file,
                {
                    "schema_version": 1,
                    "bridge_version": health["version"],
                    "receipts": receipts,
                    "complete": False,
                },
            )
            raise

        task_id = result.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError("bridge_enqueue_missing_task_id")

        receipts[run_id] = {
            "task_id": task_id,
            "created": result.get("created"),
            "idempotent_replay": result.get("idempotent_replay"),
            "commit_sha": result.get("commit_sha"),
        }
        _safe_write_json(
            receipt_file,
            {
                "schema_version": 1,
                "bridge_version": health["version"],
                "receipts": receipts,
                "complete": len(receipts) == 8,
            },
        )

    return {
        "bridge_version": health["version"],
        "enqueued_runs": len(receipts),
        "complete": len(receipts) == 8,
        "receipt_path": str(receipt_file),
        "token_persisted": False,
    }


def collect_canary(
    client: BridgeClient,
    *,
    receipt_path: str | Path,
    bundle_path: str | Path,
) -> dict[str, Any]:
    health = client.health()
    receipt_file = Path(receipt_path).expanduser().resolve()
    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    receipts = receipt_data.get("receipts") or {}

    manifest, runs = _manifest_and_runs()
    first_task_id = manifest["tasks"][0]["task_id"]
    canary_runs = [
        run for run in runs
        if run.task_id == first_task_id
    ]

    queue_statuses: dict[str, dict[str, Any]] = {}
    harness_evidence: dict[str, dict[str, Any]] = {}
    missing_receipts: list[str] = []

    for run in canary_runs:
        receipt = receipts.get(run.run_id)
        if not isinstance(receipt, dict) or not receipt.get("task_id"):
            missing_receipts.append(run.run_id)
            continue
        status = client.status(receipt["task_id"])
        queue_statuses[run.run_id] = status
        harness_evidence[run.run_id] = harness_evidence_from_queue_status(
            run,
            status,
        )

    bundle = {
        "schema_version": 1,
        "suite": manifest["suite"],
        "bridge_version": health["version"],
        "queue_statuses": queue_statuses,
        "harness_evidence": harness_evidence,
        "missing_receipts": missing_receipts,
    }
    _safe_write_json(bundle_path, bundle)

    records = []
    for run in canary_runs:
        if run.run_id not in queue_statuses:
            continue
        records.append(
            collect_live_run(
                run,
                queue_status=queue_statuses[run.run_id],
                harness_evidence=harness_evidence[run.run_id],
            )
        )
    aggregate = aggregate_live_records(records)

    return {
        "bridge_version": health["version"],
        "statuses_collected": len(queue_statuses),
        "missing_receipts": missing_receipts,
        "terminal": aggregate["terminal"],
        "comparative_eligible": aggregate["comparative_eligible"],
        "bundle_path": str(Path(bundle_path).expanduser().resolve()),
        "aggregate": aggregate,
    }


def client_from_environment(
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> BridgeClient:
    token = os.environ.get("KAVI_DISPATCH_TOKEN", "")
    return BridgeClient(token=token, base_url=base_url)
