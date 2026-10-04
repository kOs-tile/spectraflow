"""Aggregate sanitized live Reliability Lab JSONL results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.live import load_live_manifest
from spectraflow.reliability.live_results import (
    aggregate_live_results,
    load_jsonl,
)


MANIFEST = Path(__file__).parent / "live_tasks_v1.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results_jsonl")
    args = parser.parse_args()

    manifest = load_live_manifest(MANIFEST)
    records = load_jsonl(args.results_jsonl)
    print(
        json.dumps(
            aggregate_live_results(manifest, records),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
