"""Host-side isolated verifier command builder for Reliability Lab.

This module does not invoke Docker automatically. It prepares a fail-closed,
network-off, read-only container command and parses its JSON result.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spectraflow.reliability.kavi_adapter import validate_candidate_shape


DEFAULT_IMAGE = "spectraflow-reliability-verifier:v1"


@dataclass(frozen=True)
class VerifierSpec:
    task_id: str
    candidate_dir: Path
    reference_dir: Path
    image: str = DEFAULT_IMAGE


def _resolved(path: Path) -> Path:
    value = path.expanduser().resolve()
    if not value.exists():
        raise FileNotFoundError(str(value))
    return value


def build_docker_verify_command(spec: VerifierSpec) -> list[str]:
    candidate_dir = _resolved(spec.candidate_dir)
    reference_dir = _resolved(spec.reference_dir)

    candidate = candidate_dir / "candidate.py"
    reference = reference_dir / "reference.py"
    manifest = reference_dir / "live_tasks_v1.json"

    for required in (candidate, reference, manifest):
        if not required.is_file():
            raise FileNotFoundError(str(required))

    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "128m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--mount",
        f"type=bind,src={candidate_dir},dst=/workspace,readonly",
        "--mount",
        f"type=bind,src={reference_dir},dst=/reference,readonly",
        spec.image,
        "--candidate",
        "/workspace/candidate.py",
        "--reference",
        "/reference/reference.py",
        "--manifest",
        "/reference/live_tasks_v1.json",
        "--task-id",
        spec.task_id,
    ]


def render_shell_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def parse_verifier_output(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("verifier_output_must_be_single_json_line")
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_verifier_json:{exc.msg}") from exc

    required = {
        "task_id",
        "verification_status",
        "verification_cases",
        "verification_passed_cases",
        "passed",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError("missing_verifier_fields:" + ",".join(missing))

    if payload["verification_status"] not in {"pass", "fail"}:
        raise ValueError("invalid_verification_status")
    if bool(payload["passed"]) != (
        payload["verification_status"] == "pass"
    ):
        raise ValueError("verifier_status_boolean_mismatch")

    return payload


SAFE_CANDIDATE_PRELUDE = """from datetime import datetime
import hashlib
import json
import re

"""


def materialize_candidate(
    source: str,
    *,
    expected_function: str,
    destination_dir: str | Path,
) -> Path:
    """Write one preflight-approved candidate into an isolated input directory."""

    gate = validate_candidate_shape(
        source,
        expected_function=expected_function,
    )
    if not gate["accepted"]:
        raise ValueError("candidate_rejected:" + str(gate["reason"]))

    destination = Path(destination_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    candidate = destination / "candidate.py"
    candidate.write_text(
        SAFE_CANDIDATE_PRELUDE + source.rstrip() + "\n",
        encoding="utf-8",
    )
    return candidate
