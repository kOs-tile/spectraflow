"""Production KAVI Dispatch Bridge canary launcher for Reliability Lab.

Default behavior is dry-run. Network writes require the explicit execute
subcommand, a current source/runtime-bound hook attestation, and
KAVI_DISPATCH_TOKEN in the environment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.hook_attestation import (
    validate_hook_attestation_binding,
)
from spectraflow.reliability.remote_bridge import (
    DEFAULT_BASE_URL,
    canary_plan_summary,
    client_from_environment,
    collect_canary,
    execute_canary,
    full_batch_plan_summary,
)


def validate_execute_precondition(
    *,
    attestation_path: str | Path,
    deployed_runner_path: str | Path,
    source_runner_path: str | Path,
) -> dict:
    """Fail closed unless the current deployed/source runner matches attestation."""

    attestation = json.loads(
        Path(attestation_path).expanduser().resolve().read_text(encoding="utf-8")
    )
    binding = validate_hook_attestation_binding(
        attestation,
        deployed_runner_path=deployed_runner_path,
        source_runner_path=source_runner_path,
    )
    if binding.get("bound") is not True:
        raise RuntimeError("runtime_hook_bound_attestation_required")
    return binding


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("plan")
    sub.add_parser("full-plan")

    execute = sub.add_parser("execute")
    execute.add_argument("--runtime-hook-attestation-json", required=True)
    execute.add_argument("--deployed-runner-file", required=True)
    execute.add_argument("--source-runner-file", required=True)
    execute.add_argument("--receipt-file", required=True)
    execute.add_argument("--base-url", default=DEFAULT_BASE_URL)

    collect = sub.add_parser("collect")
    collect.add_argument("--receipt-file", required=True)
    collect.add_argument("--bundle-file", required=True)
    collect.add_argument("--base-url", default=DEFAULT_BASE_URL)

    args = parser.parse_args()
    command = args.command or "plan"

    if command == "plan":
        print(json.dumps(canary_plan_summary(), indent=2))
        return
    if command == "full-plan":
        print(json.dumps(full_batch_plan_summary(), indent=2))
        return

    client = client_from_environment(base_url=args.base_url)
    if command == "execute":
        binding = validate_execute_precondition(
            attestation_path=args.runtime_hook_attestation_json,
            deployed_runner_path=args.deployed_runner_file,
            source_runner_path=args.source_runner_file,
        )
        result = execute_canary(
            client,
            receipt_path=args.receipt_file,
            runtime_hook_ready=True,
        )
        result["runtime_hook_binding"] = binding
    else:
        result = collect_canary(
            client,
            receipt_path=args.receipt_file,
            bundle_path=args.bundle_file,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
