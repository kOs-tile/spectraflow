"""Export Reliability Lab collector records as Benchmark v1 evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from spectraflow.reliability.benchmark_metrics import summarize_benchmark
from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.live_benchmark_adapter import (
    live_records_to_observations,
)


def export_collector_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_records = payload.get("records") or []
    records = [LiveRunRecord(**row) for row in raw_records]
    observations = live_records_to_observations(records)
    summary = summarize_benchmark(observations)

    expected_runs = payload.get("expected_runs")
    missing_runs = payload.get("missing_runs")
    complete = payload.get("complete")

    return {
        "schema_version": 1,
        "benchmark": "spectraflow.real-agent-benchmark.v1",
        "source": "reliability_live_collector",
        "expected_runs": expected_runs,
        "observed_runs": len(observations),
        "missing_runs": missing_runs if isinstance(missing_runs, list) else None,
        "collector_complete": complete if isinstance(complete, bool) else None,
        "summary": summary,
        "observations": [row.to_dict() for row in observations],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if "KAVI_DISPATCH_TOKEN" in encoded or "Bearer " in encoded:
        raise ValueError("secret_persistence_rejected")
    path.write_text(encoded, encoding="utf-8")


def _write_jsonl(path: Path, observations: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in observations
    )
    if "KAVI_DISPATCH_TOKEN" in encoded or "Bearer " in encoded:
        raise ValueError("secret_persistence_rejected")
    path.write_text(encoded, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_json", type=Path)
    parser.add_argument("--jsonl-output", type=Path)
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.collector_json.read_text(encoding="utf-8"))
    exported = export_collector_payload(payload)

    if args.jsonl_output:
        _write_jsonl(args.jsonl_output, exported["observations"])
    if args.summary_output:
        summary_payload = {
            key: value
            for key, value in exported.items()
            if key != "observations"
        }
        _write_json(args.summary_output, summary_payload)

    print(json.dumps(exported, indent=2))


if __name__ == "__main__":
    main()
