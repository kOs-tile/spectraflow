"""Authority-observability primitives for KCC-correlated executions."""

from spectraflow.authority.drift import (
    AuthorityDriftRequest,
    AuthorityDriftResult,
    AuthorityDriftViolation,
    evaluate_authority_drift,
)

__all__ = [
    "AuthorityDriftRequest",
    "AuthorityDriftResult",
    "AuthorityDriftViolation",
    "evaluate_authority_drift",
]
