"""CLI adapter for integrating KAVI/Hermes with Reliability Lab live v1.

The CLI performs no network I/O and does not invoke an agent itself. The local
runner owns the single external step: launch its agent process with the prepared
workspace as cwd and the returned local-only instruction.

Lifecycle:
  prepare -> agent edits candidate.py -> verify -> dispatch -> evidence
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.live import build_live_plan, load_live_manifest
from spectraflow.reliability.local_harness import (
    LocalResultLedger,
    PreparedWorkspace,
    VerificationEvidence,
    execute_result_policy,
    export_evidence,
    prepare_workspace,
    verify_workspace,
    workspace_id_for_run,
)


ROOT = Path(__file__).parent
BASELINE = ROOT / "live_fixture" / "baseline.py"
REFERENCE = ROOT / "live_fixture" / "reference.py"


def _manifest() -> dict[str, Any]:
    return load_live_manifest(MANIFEST)


def _runs():
    return build_live_plan(_manifest())


def _find_run(run_id: str):
    try:
        return next(run for run in _runs() if run.run_id == run_id)
    except StopIteration as exc:
        raise ValueError("unknown_benchmark_run_id") from exc


def _find_task(task_id: str) -> dict[str, Any]:
    try:
        return next(
            task for task in _manifest()["tasks"]
            if task["task_id"] == task_id
        )
    except StopIteration as exc:
        raise ValueError("unknown_benchmark_task_id") from exc


def _prepared_from_existing(run_id: str, workspace_root: str | Path) -> PreparedWorkspace:
    run = _find_run(run_id)
    workspace_id = workspace_id_for_run(run_id)
    workspace = Path(workspace_root).expanduser().resolve() / workspace_id
    candidate = workspace / "candidate.py"
    task_path = workspace / "task.json"
    if not candidate.exists() or not task_path.exists():
        raise FileNotFoundError("prepared_workspace_not_found")
    return PreparedWorkspace(
        run_id=run_id,
        workspace_id=workspace_id,
        workspace=workspace,
        candidate_path=candidate,
        task_path=task_path,
    )


def _verification_path(evidence_root: str | Path, run_id: str) -> Path:
    return (
        Path(evidence_root).expanduser().resolve()
        / "verifications"
        / f"{workspace_id_for_run(run_id)}.json"
    )


def _run_evidence_path(evidence_root: str | Path, run_id: str) -> Path:
    return (
        Path(evidence_root).expanduser().resolve()
        / "runs"
        / f"{workspace_id_for_run(run_id)}.json"
    )


def prepare_command(run_id: str, workspace_root: str | Path) -> dict[str, Any]:
    run = _find_run(run_id)
    task = _find_task(run.task_id)
    prepared = prepare_workspace(
        run,
        task,
        workspace_root=workspace_root,
        baseline_path=BASELINE,
    )

    instruction = (
        f"Reliability Lab run {run.run_id}. Work only in the current isolated "
        f"directory. Edit only candidate.py and repair exactly function "
        f"{run.function}. Objective: {run.objective} "
        "Do not modify task.json. Do not access network services, production "
        "repositories, credentials, or files outside the current directory. "
        "Do not simulate the benchmark fault. Exit after the code edit; the "
        "runner performs canonical verification outside this workspace."
    )

    return {
        "run_id": run.run_id,
        "workspace_id": prepared.workspace_id,
        "workspace": str(prepared.workspace),
        "candidate_path": str(prepared.candidate_path),
        "task_path": str(prepared.task_path),
        "agent_instruction": instruction,
        "network_dispatch_performed": False,
    }


def verify_command(
    run_id: str,
    *,
    workspace_root: str | Path,
    evidence_root: str | Path,
) -> dict[str, Any]:
    run = _find_run(run_id)
    task = _find_task(run.task_id)
    prepared = _prepared_from_existing(run_id, workspace_root)
    evidence = verify_workspace(
        prepared,
        task,
        baseline_path=BASELINE,
        reference_path=REFERENCE,
    )

    path = _verification_path(evidence_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(evidence), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        **asdict(evidence),
        "run_id": run_id,
        "verification_record": str(path),
        "network_dispatch_performed": False,
    }


def dispatch_command(
    run_id: str,
    *,
    evidence_root: str | Path,
    runtime_status_path: str | Path | None = None,
    runtime_status: dict[str, Any] | None = None,
    authority_fixture_available: bool = True,
) -> dict[str, Any]:
    run = _find_run(run_id)
    verification_path = _verification_path(evidence_root, run_id)
    if not verification_path.exists():
        raise FileNotFoundError("verification_record_not_found")

    verification = VerificationEvidence(
        **json.loads(verification_path.read_text(encoding="utf-8"))
    )
    root = Path(evidence_root).expanduser().resolve()
    ledger = LocalResultLedger(root / "ledger" / "result-ledger.json")
    dispatch = execute_result_policy(
        run,
        verification,
        ledger=ledger,
        authority_fixture_available=authority_fixture_available,
    )

    if runtime_status_path is not None and runtime_status is not None:
        raise ValueError("provide_runtime_status_or_path_not_both")

    observed_runtime_status = runtime_status
    if runtime_status_path is not None:
        observed_runtime_status = json.loads(
            Path(runtime_status_path).read_text(encoding="utf-8")
        )

    output = _run_evidence_path(root, run_id)
    payload = export_evidence(
        dispatch,
        output_path=output,
        runtime_telemetry=observed_runtime_status,
    )

    return {
        **payload,
        "evidence_record": str(output),
        "network_dispatch_performed": False,
    }


def canary_plan() -> dict[str, Any]:
    manifest = _manifest()
    first_task = manifest["tasks"][0]["task_id"]
    runs = [run for run in _runs() if run.task_id == first_task]
    return {
        "task_id": first_task,
        "run_count": len(runs),
        "run_ids": [run.run_id for run in runs],
        "expected_faults": 2,
        "expected_policies": 4,
    }


def full_plan() -> dict[str, Any]:
    runs = _runs()
    return {
        "run_count": len(runs),
        "run_ids": [run.run_id for run in runs],
    }


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--workspace-root", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--workspace-root", required=True)
    verify.add_argument("--evidence-root", required=True)

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--run-id", required=True)
    dispatch.add_argument("--evidence-root", required=True)
    dispatch.add_argument("--runtime-status")
    dispatch.add_argument(
        "--authority-fixture-unavailable",
        action="store_true",
    )

    sub.add_parser("canary-plan")
    sub.add_parser("full-plan")

    args = parser.parse_args()
    if args.command == "prepare":
        _print(prepare_command(args.run_id, args.workspace_root))
    elif args.command == "verify":
        _print(
            verify_command(
                args.run_id,
                workspace_root=args.workspace_root,
                evidence_root=args.evidence_root,
            )
        )
    elif args.command == "dispatch":
        _print(
            dispatch_command(
                args.run_id,
                evidence_root=args.evidence_root,
                runtime_status_path=args.runtime_status,
                authority_fixture_available=not args.authority_fixture_unavailable,
            )
        )
    elif args.command == "canary-plan":
        _print(canary_plan())
    elif args.command == "full-plan":
        _print(full_plan())


if __name__ == "__main__":
    main()
