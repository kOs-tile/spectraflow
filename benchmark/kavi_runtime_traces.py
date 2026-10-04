"""Benchmark sanitized runtime-derived KAVI execution traces."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from spectraflow.reliability.runtime import (
    assess_runtime_task,
    runtime_trace_from_mapping,
)


FIXTURE = Path(__file__).parent / "fixtures" / "kavi_runtime_trace_v0.json"


def run_benchmark() -> dict:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = []

    for raw in payload["tasks"]:
        trace = runtime_trace_from_mapping(raw)
        assessment = assess_runtime_task(trace)
        row = asdict(trace)
        row.update(asdict(assessment))
        rows.append(row)

    verified_pass = sum(row["verified_pass"] for row in rows)
    completed_unverified = sum(
        row["completed_without_acceptance_evidence"] for row in rows
    )
    explicit_failures = sum(row["explicit_failure"] for row in rows)
    pre_model_failures = sum(row["pre_model_failure"] for row in rows)
    post_model_failures = sum(row["post_model_failure"] for row in rows)
    timeout_failures = sum(row["timeout_failure"] for row in rows)
    single_attempt = sum(row["attempts"] == 1 for row in rows)

    durations = [
        row["duration_seconds"]
        for row in rows
        if row["duration_seconds"] is not None
    ]

    return {
        "source": payload["provenance"],
        "cases": len(rows),
        "verified_pass": verified_pass,
        "completed_unverified": completed_unverified,
        "explicit_failures": explicit_failures,
        "pre_model_failures": pre_model_failures,
        "post_model_failures": post_model_failures,
        "timeout_failures": timeout_failures,
        "single_attempt_cases": single_attempt,
        "durations_observed": len(durations),
        "mean_observed_duration_seconds": (
            sum(durations) / len(durations) if durations else None
        ),
        "results": rows,
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
