"""Machine-readable readiness gate for Reliability Lab production canary.

The gate is intentionally conservative:
- repository artifacts are necessary but insufficient;
- bridge health must be explicit and v0.2.2+;
- the local KAVI runtime hook must be explicitly attested after installation;
- attestation is a launch precondition, not benchmark evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MIN_BRIDGE_VERSION = (0, 2, 2)


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = str(value).split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("invalid_bridge_version")
    return tuple(int(part) for part in parts)


@dataclass(frozen=True)
class CanaryReadiness:
    repository_contract_ready: bool
    bridge_health_verified: bool
    bridge_version_ready: bool
    runtime_hook_attested: bool
    canary_dispatch_ready: bool
    blockers: tuple[str, ...]
    attestation_is_benchmark_evidence: bool = False


REQUIRED_REPO_PATHS = (
    "benchmark/live_tasks_v1.json",
    "benchmark/reliability_live_plan.py",
    "benchmark/reliability_canary_remote.py",
    "benchmark/reliability_harness_cli.py",
    "spectraflow/reliability/kavi_runtime_shim.py",
    "spectraflow/reliability/remote_bridge.py",
    "spectraflow/reliability/collector.py",
    "docs/HERMES_LOCAL_RELIABILITY_ADAPTER.md",
)


def evaluate_repository_contract(root: str | Path) -> tuple[bool, list[str]]:
    base = Path(root).expanduser().resolve()
    missing = [
        path for path in REQUIRED_REPO_PATHS
        if not (base / path).is_file()
    ]
    return (not missing, missing)


def evaluate_canary_readiness(
    *,
    repository_contract_ready: bool,
    bridge_health: dict[str, Any] | None = None,
    runtime_hook_attested: bool = False,
) -> CanaryReadiness:
    blockers: list[str] = []

    if not repository_contract_ready:
        blockers.append("repository_contract_incomplete")

    bridge_health_verified = bool(
        bridge_health
        and bridge_health.get("ok") is True
        and bridge_health.get("configured") is True
    )
    if not bridge_health_verified:
        blockers.append("bridge_health_not_verified")

    bridge_version_ready = False
    if bridge_health_verified:
        version = bridge_health.get("version")
        try:
            bridge_version_ready = _version_tuple(str(version)) >= MIN_BRIDGE_VERSION
        except ValueError:
            bridge_version_ready = False
        if not bridge_version_ready:
            blockers.append("bridge_version_below_0.2.2")

    if not runtime_hook_attested:
        blockers.append("local_runtime_hook_not_attested")

    ready = (
        repository_contract_ready
        and bridge_health_verified
        and bridge_version_ready
        and runtime_hook_attested
    )

    return CanaryReadiness(
        repository_contract_ready=repository_contract_ready,
        bridge_health_verified=bridge_health_verified,
        bridge_version_ready=bridge_version_ready,
        runtime_hook_attested=runtime_hook_attested,
        canary_dispatch_ready=ready,
        blockers=tuple(blockers),
    )


def readiness_to_dict(value: CanaryReadiness) -> dict[str, Any]:
    result = asdict(value)
    result["blockers"] = list(value.blockers)
    return result
