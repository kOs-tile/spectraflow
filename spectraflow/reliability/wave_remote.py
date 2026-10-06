"""Gated remote execution for the nine post-canary Reliability Lab waves."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import string
from typing import Any

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.collector import (
    LiveRunRecord,
    aggregate_live_records,
    collect_live_run,
    harness_evidence_from_queue_status,
)
from spectraflow.reliability.hook_attestation import (
    validate_hook_attestation_binding,
)
from spectraflow.reliability.live import build_live_plan, load_live_manifest
from spectraflow.reliability.remote_bridge import (
    BridgeClient,
    remaining_waves,
)
from spectraflow.reliability.wave_reconciliation import (
    WAVE_SCHEMA,
    evaluate_wave_reconciliation,
    expected_task_for_wave,
)


PROMOTION_SCHEMA = "spectraflow.reliability-canary-promotion.v1"
WAVE_RECEIPT_SCHEMA = "spectraflow.reliability-wave-receipt.v1"
WAVE_BUNDLE_SCHEMA = "spectraflow.reliability-wave-bundle.v1"


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in string.hexdigits for char in value)
    )


def _safe_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
        raise ValueError("secret_persistence_rejected")
    target.write_text(encoded, encoding="utf-8")


def validate_promotion_artifact(payload: dict[str, Any]) -> dict[str, str]:
    if payload.get("artifact_schema") != PROMOTION_SCHEMA:
        raise RuntimeError("canary_promotion_schema_invalid")
    if payload.get("promote_to_full_80") is not True:
        raise RuntimeError("canary_promotion_pass_required")
    if payload.get("blockers") not in ([], (), None):
        raise RuntimeError("canary_promotion_has_blockers")

    canary_sha = payload.get("canary_evidence_sha256")
    if not _valid_sha256(canary_sha):
        raise RuntimeError("canary_evidence_sha256_invalid")

    model = payload.get("controlled_provider_model")
    if not isinstance(model, str) or not model.strip():
        raise RuntimeError("controlled_provider_model_missing")

    return {
        "canary_evidence_sha256": canary_sha,
        "controlled_provider_model": model,
    }


def validate_previous_reconciliation(
    payload: dict[str, Any] | None,
    *,
    wave_index: int,
    canary_evidence_sha256: str,
    controlled_provider_model: str,
) -> str | None:
    if wave_index == 1:
        if payload is not None:
            raise RuntimeError("unexpected_previous_wave_reconciliation")
        return None

    if not isinstance(payload, dict):
        raise RuntimeError("previous_wave_reconciliation_required")
    if payload.get("artifact_schema") != WAVE_SCHEMA:
        raise RuntimeError("previous_wave_reconciliation_schema_invalid")
    if payload.get("wave_pass") is not True:
        raise RuntimeError("previous_wave_must_pass")
    if payload.get("wave_index") != wave_index - 1:
        raise RuntimeError("previous_wave_index_mismatch")
    if payload.get("task_id") != expected_task_for_wave(wave_index - 1):
        raise RuntimeError("previous_wave_task_mismatch")
    if payload.get("canary_evidence_sha256") != canary_evidence_sha256:
        raise RuntimeError("canary_chain_mismatch")
    if payload.get("expected_provider_model") != controlled_provider_model:
        raise RuntimeError("provider_model_chain_mismatch")

    digest = payload.get("reconciliation_sha256")
    if not _valid_sha256(digest):
        raise RuntimeError("previous_wave_reconciliation_sha256_invalid")
    return digest


def validate_runtime_binding_files(
    *,
    attestation_payload: dict[str, Any],
    deployed_runner_path: str | Path,
    source_runner_path: str | Path,
) -> dict[str, Any]:
    binding = validate_hook_attestation_binding(
        attestation_payload,
        deployed_runner_path=deployed_runner_path,
        source_runner_path=source_runner_path,
    )
    if binding.get("bound") is not True:
        raise RuntimeError("runtime_hook_bound_attestation_required")
    return binding


def wave_plan(wave_index: int) -> dict[str, Any]:
    waves = remaining_waves()
    if wave_index < 1 or wave_index > len(waves):
        raise ValueError("wave_index_out_of_range")
    return waves[wave_index - 1]


def _runs_for_wave(wave_index: int):
    task_id = expected_task_for_wave(wave_index)
    manifest = load_live_manifest(MANIFEST)
    return [
        run for run in build_live_plan(manifest)
        if run.task_id == task_id
    ]


def _validate_receipt_identity(
    payload: dict[str, Any],
    *,
    wave_index: int,
    task_id: str,
    canary_evidence_sha256: str,
    previous_reconciliation_sha256: str | None,
) -> None:
    if payload.get("schema") != WAVE_RECEIPT_SCHEMA:
        raise RuntimeError("wave_receipt_schema_mismatch")
    if payload.get("wave_index") != wave_index:
        raise RuntimeError("wave_receipt_index_mismatch")
    if payload.get("task_id") != task_id:
        raise RuntimeError("wave_receipt_task_mismatch")
    if payload.get("canary_evidence_sha256") != canary_evidence_sha256:
        raise RuntimeError("wave_receipt_canary_mismatch")
    if payload.get("previous_wave_reconciliation_sha256") != previous_reconciliation_sha256:
        raise RuntimeError("wave_receipt_chain_mismatch")


def execute_wave(
    client: BridgeClient,
    *,
    wave_index: int,
    promotion_artifact: dict[str, Any],
    previous_reconciliation: dict[str, Any] | None,
    runtime_binding: dict[str, Any],
    receipt_path: str | Path,
) -> dict[str, Any]:
    if runtime_binding.get("bound") is not True:
        raise RuntimeError("runtime_hook_bound_attestation_required")

    promotion = validate_promotion_artifact(promotion_artifact)
    previous_sha = validate_previous_reconciliation(
        previous_reconciliation,
        wave_index=wave_index,
        canary_evidence_sha256=promotion["canary_evidence_sha256"],
        controlled_provider_model=promotion["controlled_provider_model"],
    )
    wave = wave_plan(wave_index)
    health = client.health()

    receipt_file = Path(receipt_path).expanduser().resolve()
    receipts: dict[str, Any] = {}
    if receipt_file.exists():
        existing = json.loads(receipt_file.read_text(encoding="utf-8"))
        _validate_receipt_identity(
            existing,
            wave_index=wave_index,
            task_id=wave["task_id"],
            canary_evidence_sha256=promotion["canary_evidence_sha256"],
            previous_reconciliation_sha256=previous_sha,
        )
        receipts.update(existing.get("receipts") or {})

    for payload in wave["payloads"]:
        run_id = payload["benchmark_run_id"]
        if run_id in receipts and receipts[run_id].get("task_id"):
            continue

        try:
            result = client.enqueue(payload)
        except Exception:
            _safe_write_json(
                receipt_file,
                {
                    "schema": WAVE_RECEIPT_SCHEMA,
                    "wave_index": wave_index,
                    "task_id": wave["task_id"],
                    "bridge_version": health["version"],
                    "canary_evidence_sha256": promotion["canary_evidence_sha256"],
                    "previous_wave_reconciliation_sha256": previous_sha,
                    "controlled_provider_model": promotion["controlled_provider_model"],
                    "receipts": receipts,
                    "complete": False,
                },
            )
            raise

        queue_task_id = result.get("task_id")
        if not isinstance(queue_task_id, str) or not queue_task_id:
            raise RuntimeError("bridge_enqueue_missing_task_id")

        receipts[run_id] = {
            "task_id": queue_task_id,
            "created": result.get("created"),
            "idempotent_replay": result.get("idempotent_replay"),
            "commit_sha": result.get("commit_sha"),
        }
        _safe_write_json(
            receipt_file,
            {
                "schema": WAVE_RECEIPT_SCHEMA,
                "wave_index": wave_index,
                "task_id": wave["task_id"],
                "bridge_version": health["version"],
                "canary_evidence_sha256": promotion["canary_evidence_sha256"],
                "previous_wave_reconciliation_sha256": previous_sha,
                "controlled_provider_model": promotion["controlled_provider_model"],
                "receipts": receipts,
                "complete": len(receipts) == 8,
            },
        )

    return {
        "wave_index": wave_index,
        "task_id": wave["task_id"],
        "bridge_version": health["version"],
        "enqueued_runs": len(receipts),
        "complete": len(receipts) == 8,
        "receipt_path": str(receipt_file),
        "canary_evidence_sha256": promotion["canary_evidence_sha256"],
        "previous_wave_reconciliation_sha256": previous_sha,
        "controlled_provider_model": promotion["controlled_provider_model"],
        "runtime_hook_bound": True,
        "token_persisted": False,
    }


def collect_wave(
    client: BridgeClient,
    *,
    wave_index: int,
    receipt_path: str | Path,
    bundle_path: str | Path,
) -> dict[str, Any]:
    wave = wave_plan(wave_index)
    receipt_file = Path(receipt_path).expanduser().resolve()
    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))

    _validate_receipt_identity(
        receipt_data,
        wave_index=wave_index,
        task_id=wave["task_id"],
        canary_evidence_sha256=receipt_data.get("canary_evidence_sha256"),
        previous_reconciliation_sha256=receipt_data.get(
            "previous_wave_reconciliation_sha256"
        ),
    )

    expected_run_ids = set(wave["run_ids"])
    receipts = receipt_data.get("receipts") or {}
    if not set(receipts).issubset(expected_run_ids):
        raise RuntimeError("wave_receipt_contains_unknown_run")

    health = client.health()
    runs = {run.run_id: run for run in _runs_for_wave(wave_index)}
    records: list[LiveRunRecord] = []
    missing_receipts: list[str] = []

    for run_id in wave["run_ids"]:
        receipt = receipts.get(run_id)
        if not isinstance(receipt, dict) or not receipt.get("task_id"):
            missing_receipts.append(run_id)
            continue
        run = runs.get(run_id)
        if run is None:
            raise RuntimeError("wave_run_not_found")

        status = client.status(receipt["task_id"])
        harness = harness_evidence_from_queue_status(run, status)
        records.append(
            collect_live_run(
                run,
                queue_status=status,
                harness_evidence=harness,
            )
        )

    aggregate = aggregate_live_records(records)
    bundle = {
        "schema": WAVE_BUNDLE_SCHEMA,
        "wave_index": wave_index,
        "task_id": wave["task_id"],
        "bridge_version": health["version"],
        "canary_evidence_sha256": receipt_data.get("canary_evidence_sha256"),
        "previous_wave_reconciliation_sha256": receipt_data.get(
            "previous_wave_reconciliation_sha256"
        ),
        "controlled_provider_model": receipt_data.get("controlled_provider_model"),
        "missing_receipts": missing_receipts,
        "records": [asdict(record) for record in records],
        "aggregate": aggregate,
    }
    _safe_write_json(bundle_path, bundle)

    return {
        "wave_index": wave_index,
        "task_id": wave["task_id"],
        "statuses_collected": len(records),
        "missing_receipts": missing_receipts,
        "terminal": aggregate["terminal"],
        "comparative_eligible": aggregate["comparative_eligible"],
        "bundle_path": str(Path(bundle_path).expanduser().resolve()),
    }


def reconcile_wave_bundle(
    bundle: dict[str, Any],
    *,
    wave_index: int,
    promotion_artifact: dict[str, Any],
    previous_reconciliation: dict[str, Any] | None,
) -> dict[str, Any]:
    if bundle.get("schema") != WAVE_BUNDLE_SCHEMA:
        raise RuntimeError("wave_bundle_schema_invalid")
    if bundle.get("wave_index") != wave_index:
        raise RuntimeError("wave_bundle_index_mismatch")
    if bundle.get("task_id") != expected_task_for_wave(wave_index):
        raise RuntimeError("wave_bundle_task_mismatch")

    promotion = validate_promotion_artifact(promotion_artifact)
    previous_sha = validate_previous_reconciliation(
        previous_reconciliation,
        wave_index=wave_index,
        canary_evidence_sha256=promotion["canary_evidence_sha256"],
        controlled_provider_model=promotion["controlled_provider_model"],
    )

    if bundle.get("canary_evidence_sha256") != promotion["canary_evidence_sha256"]:
        raise RuntimeError("wave_bundle_canary_mismatch")
    if bundle.get("previous_wave_reconciliation_sha256") != previous_sha:
        raise RuntimeError("wave_bundle_chain_mismatch")
    if bundle.get("controlled_provider_model") != promotion["controlled_provider_model"]:
        raise RuntimeError("wave_bundle_provider_model_mismatch")

    rows = [LiveRunRecord(**row) for row in (bundle.get("records") or [])]
    return evaluate_wave_reconciliation(
        rows,
        wave_index=wave_index,
        expected_provider_model=promotion["controlled_provider_model"],
        canary_evidence_sha256=promotion["canary_evidence_sha256"],
        previous_wave_reconciliation_sha256=previous_sha,
    )
