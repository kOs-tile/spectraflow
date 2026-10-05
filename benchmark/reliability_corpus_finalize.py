"""Finalize an 80-row Reliability Lab Benchmark v1 JSONL corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.real_agent_v1 import load_jsonl
from spectraflow.reliability.live_corpus_finalizer import finalize_live_corpus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("observations_jsonl", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = finalize_live_corpus(load_jsonl(args.observations_jsonl))
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"

    if args.output:
        target = args.output.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
            raise ValueError("secret_persistence_rejected")
        target.write_text(encoded, encoding="utf-8")

    print(encoded, end="")


if __name__ == "__main__":
    main()
