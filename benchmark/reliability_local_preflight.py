"""One-command local preflight for Reliability Lab canary readiness.

This command performs no network calls and never executes the canary. It binds
the structural hook attestation to both repair/source and deployed runner bytes,
checks the repository contract, consumes a saved bridge-health JSON, and renders
the eight-run canary plan.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from spectraflow.reliability.hook_attestation import (
    inspect_runner_source,
    validate_hook_attestation_binding,
)
from spectraflow.reliability.readiness import (
    evaluate_canary_readiness,
    evaluate_repository_contract,
    readiness_to_dict,
)
from spectraflow.reliability.remote_bridge import canary_plan_summary


ROOT = Path(__file__).parents[1]


def _safe_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
        raise ValueError("secret_persistence_rejected")
    path.write_text(encoded, encoding="utf-8")


def build_local_preflight(
    *,
    source_runner_file: str | Path,
    deployed_runner_file: str | Path,
    bridge_health: dict[str, Any] | None,
) -> dict[str, Any]:
    attestation = inspect_runner_source(source_runner_file)
    binding = validate_hook_attestation_binding(
        attestation,
        deployed_runner_path=deployed_runner_file,
        source_runner_path=source_runner_file,
    )

    repo_ready, missing = evaluate_repository_contract(ROOT)
    readiness = evaluate_canary_readiness(
        repository_contract_ready=repo_ready,
        bridge_health=bridge_health,
        runtime_hook_attested=binding["bound"],
    )

    return {
        "schema_version": 1,
        "checkpoint": "spectraflow.local-reliability-preflight.v1",
        "repository_contract_ready": repo_ready,
        "missing_repository_paths": missing,
        "hook_attestation": attestation,
        "runtime_binding": binding,
        "readiness": readiness_to_dict(readiness),
        "canary_plan": canary_plan_summary(),
        "network_calls_performed": False,
        "canary_execution_performed": False,
        "local_path_exported": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-runner-file", required=True)
    parser.add_argument("--deployed-runner-file", required=True)
    parser.add_argument("--bridge-health-json", required=True)
    parser.add_argument("--attestation-output")
    parser.add_argument("--preflight-output")
    args = parser.parse_args()

    bridge_health = json.loads(
        Path(args.bridge_health_json).read_text(encoding="utf-8")
    )
    result = build_local_preflight(
        source_runner_file=args.source_runner_file,
        deployed_runner_file=args.deployed_runner_file,
        bridge_health=bridge_health,
    )

    if args.attestation_output:
        _safe_write(Path(args.attestation_output), result["hook_attestation"])
    if args.preflight_output:
        _safe_write(Path(args.preflight_output), result)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
