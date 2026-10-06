"""Offline collector for Reliability Lab live result bundles.

Input JSON schema:
{
  "queue_statuses": {"<run_id>": {...}},
  "harness_evidence": {"<run_id>": {...}}
}

This command performs no network calls.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.collector import (
    aggregate_live_records,
    collect_live_run,
)
from spectraflow.reliability.live import build_live_plan, load_live_manifest


def collect_bundle(bundle: dict) -> dict:
    manifest = load_live_manifest(MANIFEST)
    runs = {run.run_id: run for run in build_live_plan(manifest)}

    queue = bundle.get("queue_statuses") or {}
    harness = bundle.get("harness_evidence") or {}

    records = []
    missing = []
    for run_id, run in runs.items():
        if run_id not in queue or run_id not in harness:
            missing.append(run_id)
            continue
        records.append(
            collect_live_run(
                run,
                queue_status=queue[run_id],
                harness_evidence=harness[run_id],
            )
        )

    result = aggregate_live_records(records)
    result["expected_runs"] = len(runs)
    result["missing_runs"] = missing
    result["complete"] = not missing
    return result


def _safe_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
        raise ValueError("secret_persistence_rejected")
    path.write_text(encoded, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    data = json.loads(args.bundle.read_text(encoding="utf-8"))
    result = collect_bundle(data)
    if args.output:
        _safe_write(args.output.expanduser().resolve(), result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
