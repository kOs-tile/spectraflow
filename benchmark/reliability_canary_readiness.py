"""Offline Reliability Lab canary readiness checker.

No network calls are performed. Optionally supply a saved bridge health JSON.
The runtime-hook flag is a launch attestation only and is never treated as
benchmark evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.hook_attestation import validate_hook_attestation
from spectraflow.reliability.readiness import (
    evaluate_canary_readiness,
    evaluate_repository_contract,
    readiness_to_dict,
)


ROOT = Path(__file__).parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-health-json")
    parser.add_argument("--runtime-hook-attestation-json")
    args = parser.parse_args()

    repo_ready, missing = evaluate_repository_contract(ROOT)

    bridge_health = None
    if args.bridge_health_json:
        bridge_health = json.loads(
            Path(args.bridge_health_json).read_text(encoding="utf-8")
        )

    runtime_hook_attested = False
    attestation_checked = False
    if args.runtime_hook_attestation_json:
        attestation_checked = True
        attestation = json.loads(
            Path(args.runtime_hook_attestation_json).read_text(encoding="utf-8")
        )
        runtime_hook_attested = validate_hook_attestation(attestation)

    readiness = evaluate_canary_readiness(
        repository_contract_ready=repo_ready,
        bridge_health=bridge_health,
        runtime_hook_attested=runtime_hook_attested,
    )
    output = readiness_to_dict(readiness)
    output["missing_repository_paths"] = missing
    output["runtime_hook_attestation_checked"] = attestation_checked
    output["network_calls_performed"] = False
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
