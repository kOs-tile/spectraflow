"""Analyze a sanitized Reliability Lab live collector result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.analysis import analyze_live_records
from spectraflow.reliability.collector import LiveRunRecord


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_json", type=Path)
    parser.add_argument("--expected-runs", type=int, default=80)
    args = parser.parse_args()

    payload = json.loads(args.collector_json.read_text(encoding="utf-8"))
    raw_records = payload.get("records") or []
    records = [LiveRunRecord(**row) for row in raw_records]

    result = analyze_live_records(
        records,
        expected_runs=args.expected_runs,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
