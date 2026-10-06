"""Remote executor for post-canary Reliability Lab task waves.

Plan and reconcile are offline. Execute and collect use the authenticated
production KAVI Dispatch Bridge.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.hook_attestation import inspect_runner_source
from spectraflow.reliability.remote_bridge import (
    DEFAULT_BASE_URL,
    client_from_environment,
)
from spectraflow.reliability.wave_remote import (
    collect_wave,
    execute_wave,
    reconcile_wave_bundle,
    validate_runtime_binding_files,
    wave_plan,
)


def _load(path: str | Path | None):
    if path is None:
        return None
    return json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))


def _safe_write(path: str | Path, payload: dict) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
        raise ValueError("secret_persistence_rejected")
    target.write_text(encoded, encoding="utf-8")


def _plan_summary(wave_index: int) -> dict:
    wave = wave_plan(wave_index)
    return {
        "wave_index": wave["wave"],
        "task_id": wave["task_id"],
        "runs": wave["runs"],
        "run_ids": wave["run_ids"],
        "network_dispatch_performed": False,
        "requires_canary_promotion_pass": True,
        "requires_runtime_hook_binding": True,
        "requires_previous_wave_reconciliation": wave_index > 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--wave-index", type=int, required=True)

    execute = sub.add_parser("execute")
    execute.add_argument("--wave-index", type=int, required=True)
    execute.add_argument("--promotion-artifact", required=True)
    execute.add_argument("--previous-reconciliation")
    execute.add_argument("--runtime-hook-attestation-json", required=True)
    execute.add_argument("--deployed-runner-file", required=True)
    execute.add_argument("--source-runner-file", required=True)
    execute.add_argument("--receipt-file", required=True)
    execute.add_argument("--base-url", default=DEFAULT_BASE_URL)

    collect = sub.add_parser("collect")
    collect.add_argument("--wave-index", type=int, required=True)
    collect.add_argument("--receipt-file", required=True)
    collect.add_argument("--bundle-file", required=True)
    collect.add_argument("--base-url", default=DEFAULT_BASE_URL)

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--wave-index", type=int, required=True)
    reconcile.add_argument("--bundle-file", required=True)
    reconcile.add_argument("--promotion-artifact", required=True)
    reconcile.add_argument("--previous-reconciliation")
    reconcile.add_argument("--output")

    args = parser.parse_args()

    if args.command == "plan":
        print(json.dumps(_plan_summary(args.wave_index), indent=2, sort_keys=True))
        return

    if args.command == "execute":
        promotion = _load(args.promotion_artifact)
        previous = _load(args.previous_reconciliation)
        attestation = _load(args.runtime_hook_attestation_json)
        binding = validate_runtime_binding_files(
            attestation_payload=attestation,
            deployed_runner_path=args.deployed_runner_file,
            source_runner_path=args.source_runner_file,
        )
        client = client_from_environment(base_url=args.base_url)
        result = execute_wave(
            client,
            wave_index=args.wave_index,
            promotion_artifact=promotion,
            previous_reconciliation=previous,
            runtime_binding=binding,
            receipt_path=args.receipt_file,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    if args.command == "collect":
        client = client_from_environment(base_url=args.base_url)
        result = collect_wave(
            client,
            wave_index=args.wave_index,
            receipt_path=args.receipt_file,
            bundle_path=args.bundle_file,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    bundle = _load(args.bundle_file)
    promotion = _load(args.promotion_artifact)
    previous = _load(args.previous_reconciliation)
    result = reconcile_wave_bundle(
        bundle,
        wave_index=args.wave_index,
        promotion_artifact=promotion,
        previous_reconciliation=previous,
    )
    if args.output:
        _safe_write(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["wave_pass"] else 2)


if __name__ == "__main__":
    main()
