"""Production KAVI Dispatch Bridge canary launcher for Reliability Lab.

Default behavior is dry-run. Network writes require the explicit execute
subcommand, --runtime-hook-ready, and KAVI_DISPATCH_TOKEN in the environment.
"""

from __future__ import annotations

import argparse
import json

from spectraflow.reliability.remote_bridge import (
    DEFAULT_BASE_URL,
    canary_plan_summary,
    client_from_environment,
    collect_canary,
    execute_canary,
    full_batch_plan_summary,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("plan")
    sub.add_parser("full-plan")

    execute = sub.add_parser("execute")
    execute.add_argument("--runtime-hook-ready", action="store_true")
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
        result = execute_canary(
            client,
            receipt_path=args.receipt_file,
            runtime_hook_ready=args.runtime_hook_ready,
        )
    else:
        result = collect_canary(
            client,
            receipt_path=args.receipt_file,
            bundle_path=args.bundle_file,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
