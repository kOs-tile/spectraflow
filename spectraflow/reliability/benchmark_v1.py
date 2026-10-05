"""Core schema for the SPECTRAFLOW Real Agent Benchmark v1.

The schema intentionally separates observed runtime facts from derived benchmark
judgements. Missing evidence stays missing; it is never silently upgraded to a
successful or safe outcome.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class FailureMode(str, Enum):
    NONE = "none"
    TOOL_TIMEOUT = "tool_timeout"
    PROVIDER_FAILURE = "provider_failure"
    MALFORMED_RESULT = "malformed_result"
    POST_COMMIT_TIMEOUT = "post_commit_timeout"
    VERIFICATION_FAILURE = "verification_failure"
    AUTHORITY_REVOKED = "authority_revoked"
    CONTEXT_CORRUPTION = "context_corruption"
    DUPLICATE_EXECUTION = "duplicate_execution"
    UNKNOWN = "unknown"


class RecoveryPolicy(str, Enum):
    BASELINE = "baseline"
    BOUNDED_RETRY = "bounded_retry"
    IDEMPOTENT_RECOVERY = "idempotent_recovery"
    AUTHORITY_AWARE = "authority_aware"


@dataclass(frozen=True)
class BenchmarkObservation:
    task_id: str
    scenario_id: str
    recovery_policy: RecoveryPolicy
    failure_mode: FailureMode

    terminal_status: str
    verified_success: bool
    safe_completion: bool

    attempts: int
    model_invocations: int
    tool_calls: int

    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: float | None = None

    human_interventions: int = 0
    duplicate_side_effects: int = 0
    unauthorized_actions: int = 0
    verification_failures: int = 0

    source: str | None = None
    evidence_id: str | None = None

    def validate(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must be non-empty")
        if not self.scenario_id.strip():
            raise ValueError("scenario_id must be non-empty")
        if self.attempts < 0:
            raise ValueError("attempts must be >= 0")
        if self.model_invocations < 0:
            raise ValueError("model_invocations must be >= 0")
        if self.tool_calls < 0:
            raise ValueError("tool_calls must be >= 0")
        if self.human_interventions < 0:
            raise ValueError("human_interventions must be >= 0")
        if self.duplicate_side_effects < 0:
            raise ValueError("duplicate_side_effects must be >= 0")
        if self.unauthorized_actions < 0:
            raise ValueError("unauthorized_actions must be >= 0")
        if self.verification_failures < 0:
            raise ValueError("verification_failures must be >= 0")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be >= 0")
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("input_tokens must be >= 0")
        if self.output_tokens is not None and self.output_tokens < 0:
            raise ValueError("output_tokens must be >= 0")

        if self.verified_success and self.verification_failures:
            raise ValueError(
                "verified_success cannot be true when verification_failures > 0"
            )
        if self.safe_completion and (
            self.duplicate_side_effects > 0 or self.unauthorized_actions > 0
        ):
            raise ValueError(
                "safe_completion cannot be true with duplicate or unauthorized actions"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["recovery_policy"] = self.recovery_policy.value
        payload["failure_mode"] = self.failure_mode.value
        return payload
