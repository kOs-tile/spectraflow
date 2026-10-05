"""Normalize one saved KAVI get_task_status response.

This command never calls KAVI. It consumes a saved JSON response and emits the
evidence-conservative collector record for a chosen planned run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.kavi_adapter import (
    collect_task_status,
    collected_result_to_dict,
)
from spectraflow.reliability.live import build_live_plan, load_live_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--status-json", required=True)
    args = parser.parse_args()

    manifest = load_live_manifest(MANIFEST)
    run = next(
        (row for row in build_live_plan(manifest) if row.run_id == args.run_id),
        None,
    )
    if run is None:
        raise SystemExit("unknown run id")

    status = json.loads(Path(args.status_json).read_text(encoding="utf-8"))
    result = collect_task_status(status, expected_run=run)
    print(json.dumps(collected_result_to_dict(result), indent=2))


if __name__ == "__main__":
    main()
