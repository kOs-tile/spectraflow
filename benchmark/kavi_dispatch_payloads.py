"""Render KAVI Dispatch Bridge payloads for Reliability Lab v1.

This command never calls the bridge. By default it prints only the fail-closed
preflight state. Use --isolation-ready to render the 80 enqueue payloads after
the local isolation adapter has been verified.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.kavi_adapter import summarize_dispatch_batch
from spectraflow.reliability.live import load_live_manifest


MANIFEST = Path(__file__).parent / "live_tasks_v1.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--isolation-ready",
        action="store_true",
        help="Attest that the isolated workspace adapter has been verified.",
    )
    args = parser.parse_args()

    manifest = load_live_manifest(MANIFEST)

    if not args.isolation_ready:
        print(
            json.dumps(
                {
                    "ready": False,
                    "reason": (
                        "fail_closed: pass --isolation-ready only after the local "
                        "workspace adapter creates isolated copies and keeps verifier "
                        "artifacts outside the agent-editable workspace"
                    ),
                    "network_dispatch_performed": False,
                },
                indent=2,
            )
        )
        return

    print(
        json.dumps(
            summarize_dispatch_batch(
                manifest,
                isolation_ready=True,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
