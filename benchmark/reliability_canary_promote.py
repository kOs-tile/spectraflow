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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_json", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.collector_json.read_text(encoding="utf-8"))
    records = [
        LiveRunRecord(**row)
        for row in (payload.get("records") or [])
    ]
    result = evaluate_canary_promotion(records)
    print(json.dumps(promotion_to_dict(result), indent=2))


if __name__ == "__main__":
    main()
