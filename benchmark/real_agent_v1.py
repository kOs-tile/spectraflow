"""CLI runner for SPECTRAFLOW Real Agent Benchmark v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.benchmark_metrics import summarize_benchmark
from spectraflow.reliability.benchmark_v1 import (
    BenchmarkObservation,
    FailureMode,
    RecoveryPolicy,
)


def observation_from_mapping(payload: dict) -> BenchmarkObservation:
    return BenchmarkObservation(
        task_id=str(payload["task_id"]),
        scenario_id=str(payload["scenario_id"]),
        recovery_policy=RecoveryPolicy(payload["recovery_policy"]),
        failure_mode=FailureMode(payload["failure_mode"]),
        terminal_status=str(payload["terminal_status"]),
        verified_success=bool(payload["verified_success"]),
        safe_completion=bool(payload["safe_completion"]),
        attempts=int(payload["attempts"]),
        model_invocations=int(payload["model_invocations"]),
        tool_calls=int(payload["tool_calls"]),
        input_tokens=payload.get("input_tokens"),
        output_tokens=payload.get("output_tokens"),
        estimated_cost_usd=payload.get("estimated_cost_usd"),
        latency_ms=payload.get("latency_ms"),
        human_interventions=int(payload.get("human_interventions", 0)),
        duplicate_side_effects=int(payload.get("duplicate_side_effects", 0)),
        unauthorized_actions=int(payload.get("unauthorized_actions", 0)),
        verification_failures=int(payload.get("verification_failures", 0)),
        source=payload.get("source"),
        evidence_id=payload.get("evidence_id"),
    )


def load_jsonl(path: Path) -> list[BenchmarkObservation]:
    observations = []
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
            observation = observation_from_mapping(payload)
            observation.validate()
        except Exception as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        observations.append(observation)
    return observations


def run_file(path: Path) -> dict:
    return summarize_benchmark(load_jsonl(path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize Real Agent Benchmark v1 JSONL observations."
    )
    parser.add_argument("observations", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_file(args.observations), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
