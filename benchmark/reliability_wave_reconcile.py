"""Evaluate one 8-run Reliability Lab task wave."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.collector import LiveRunRecord
from spectraflow.reliability.wave_reconciliation import (
    evaluate_wave_reconciliation,
)


def _records(payload: dict) -> list[LiveRunRecord]:
    raw = payload.get("records")
    if raw is None:
        aggregate = payload.get("aggregate")
        raw = aggregate.get("records") if isinstance(aggregate, dict) else None
    return [LiveRunRecord(**row) for row in (raw or [])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_json", type=Path)
    parser.add_argument("--wave-index", type=int, required=True)
    parser.add_argument("--expected-provider-model", required=True)
    parser.add_argument("--canary-evidence-sha256", required=True)
    parser.add_argument("--previous-wave-reconciliation-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.collector_json.read_text(encoding="utf-8"))
    result = evaluate_wave_reconciliation(
        _records(payload),
        wave_index=args.wave_index,
        expected_provider_model=args.expected_provider_model,
        canary_evidence_sha256=args.canary_evidence_sha256,
        previous_wave_reconciliation_sha256=(
            args.previous_wave_reconciliation_sha256
        ),
    )
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        target = args.output.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if "Bearer " in encoded or "KAVI_DISPATCH_TOKEN" in encoded:
            raise ValueError("secret_persistence_rejected")
        target.write_text(encoded, encoding="utf-8")

    print(encoded, end="")
    raise SystemExit(0 if result["wave_pass"] else 2)


if __name__ == "__main__":
    main()
