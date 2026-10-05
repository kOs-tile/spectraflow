"""Evaluate whether an 8-run Reliability Lab canary may expand to 80 runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.promotion import (
    evaluate_canary_promotion,
    promotion_to_dict,
)


def evaluate_collector_file(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = [
        LiveRunRecord(**row)
        for row in (payload.get("records") or [])
    ]
    return promotion_to_dict(evaluate_canary_promotion(records))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_json", type=Path)
    args = parser.parse_args()

    result = evaluate_collector_file(args.collector_json)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["promote_to_full_80"] else 2)


if __name__ == "__main__":
    main()
