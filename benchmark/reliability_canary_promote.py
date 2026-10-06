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
    raw_records = payload.get("records")
    if raw_records is None:
        aggregate = payload.get("aggregate")
        raw_records = aggregate.get("records") if isinstance(aggregate, dict) else None
    records = [
        LiveRunRecord(**row)
        for row in (raw_records or [])
    ]
    return promotion_to_dict(evaluate_canary_promotion(records))


def _safe_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
        raise ValueError("secret_persistence_rejected")
    path.write_text(encoded, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_json", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = evaluate_collector_file(args.collector_json)
    if args.output:
        _safe_write(args.output.expanduser().resolve(), result)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["promote_to_full_80"] else 2)


if __name__ == "__main__":
    main()
