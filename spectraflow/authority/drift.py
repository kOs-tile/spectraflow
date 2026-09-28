"""Observe runtime calls against a claimed KCC capsule.

This module is deliberately observational. It does not grant authority and is not
an enforcement replacement for KCC's Guard. KCC remains the authority engine;
SPECTRAFLOW translates KCC decisions into drift telemetry without dispatching.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Literal

from kavi_capability_compiler import CAPSULE_VERSION, authorize_call
from pydantic import BaseModel, Field


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class AuthorityDriftRequest(BaseModel):
    capsule: dict[str, Any]
    capability_id: str = Field(min_length=1)
    operation: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    observed_capsule_id: str | None = None
    observed_at: int | None = None


class AuthorityDriftViolation(BaseModel):
    code: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str
    field: str | None = None


class AuthorityDriftResult(BaseModel):
    drift_detected: bool
    within_claimed_authority: bool
    capsule_integrity_valid: bool
    capsule_id: str | None = None
    capability_id: str
    operation: str | None = None
    observation_fingerprint: str
    evaluation_fingerprint: str = ""
    violations: list[AuthorityDriftViolation] = Field(default_factory=list)


def _field_from_reason(reason: str) -> str | None:
    if ":" not in reason:
        return None
    return reason.split(":", 1)[1] or None


def _violation_from_kcc_reason(reason: str) -> AuthorityDriftViolation:
    field = _field_from_reason(reason)

    fixed = {
        "invalid_capsule_integrity": (
            "AUTH-CAPSULE-INTEGRITY",
            "critical",
            "Claimed KCC capsule failed KCC integrity verification.",
            None,
        ),
        "unsupported_capsule_version": (
            "AUTH-CAPSULE-VERSION",
            "high",
            f"Observed authority artifact is not the supported {CAPSULE_VERSION} capsule.",
            None,
        ),
        "fail_closed_required": (
            "AUTH-CAPSULE-FAIL-CLOSED",
            "critical",
            "Observed capsule does not require fail-closed authorization.",
            None,
        ),
        "expired": (
            "AUTH-CAPSULE-EXPIRED",
            "critical",
            "Runtime call was observed after the capsule expiry boundary.",
            None,
        ),
        "approval_required": (
            "AUTH-APPROVAL-REQUIRED",
            "high",
            "Observed call requires external approval and is not an automatic grant.",
            None,
        ),
        "capability_denied": (
            "AUTH-CAPABILITY-DENIED",
            "critical",
            "Observed capability is explicitly denied by the capsule.",
            "capability_id",
        ),
        "capability_not_granted": (
            "AUTH-CAPABILITY-NOT-GRANTED",
            "critical",
            "Observed capability is absent from the capsule grants.",
            "capability_id",
        ),
        "operation_not_granted": (
            "AUTH-OPERATION-NOT-GRANTED",
            "critical",
            "Observed operation is outside the capsule operation constraint.",
            "operation",
        ),
    }
    if reason in fixed:
        code, severity, message, fixed_field = fixed[reason]
        return AuthorityDriftViolation(
            code=code,
            severity=severity,
            message=message,
            field=fixed_field,
        )

    parameter_prefixes = {
        "required_parameter_missing": (
            "AUTH-PARAM-REQUIRED",
            "Observed call omitted a required parameter.",
        ),
        "parameter_not_granted": (
            "AUTH-PARAM-NOT-GRANTED",
            "Observed call supplied a parameter outside the capsule constraint.",
        ),
        "parameter_type_mismatch": (
            "AUTH-PARAM-TYPE",
            "Observed parameter violates its KCC type constraint.",
        ),
        "parameter_above_max": (
            "AUTH-PARAM-ABOVE-MAX",
            "Observed parameter exceeds the capsule maximum.",
        ),
        "parameter_below_min": (
            "AUTH-PARAM-BELOW-MIN",
            "Observed parameter is below the capsule minimum.",
        ),
        "parameter_not_allowed": (
            "AUTH-PARAM-ENUM",
            "Observed parameter is outside the allowed values.",
        ),
        "parameter_too_long": (
            "AUTH-PARAM-TOO-LONG",
            "Observed parameter exceeds the allowed length.",
        ),
        "parameter_pattern_mismatch": (
            "AUTH-PARAM-PATTERN",
            "Observed parameter violates the allowed pattern.",
        ),
        "parameter_mismatch": (
            "AUTH-PARAM-MISMATCH",
            "Observed parameter differs from the exact capsule constraint.",
        ),
        "unsupported_parameter_type_rule": (
            "AUTH-CONSTRAINT-UNSUPPORTED",
            "KCC rejected an unsupported parameter type rule.",
        ),
    }
    prefix = reason.split(":", 1)[0]
    if prefix in parameter_prefixes:
        code, message = parameter_prefixes[prefix]
        return AuthorityDriftViolation(
            code=code,
            severity="high",
            message=message,
            field=field,
        )

    if reason in {
        "invalid_parameters",
        "invalid_parameter_constraints",
        "invalid_capsule_shape",
        "invalid_capsule",
        "invalid_inventory_binding",
        "unsupported_inventory_version",
        "invalid_inventory_integrity",
        "inventory_drift",
    }:
        return AuthorityDriftViolation(
            code="AUTH-KCC-INVALID",
            severity="critical",
            message=f"KCC rejected the observed authority artifact or call: {reason}.",
            field=field,
        )

    return AuthorityDriftViolation(
        code="AUTH-KCC-DENIED",
        severity="high",
        message=f"KCC denied the observed call: {reason}.",
        field=field,
    )


def _integrity_valid(decision: dict[str, Any]) -> bool:
    verification = decision.get("verification")
    if isinstance(verification, dict):
        for check in verification.get("checks", []):
            if isinstance(check, dict) and check.get("name") == "integrity":
                return check.get("ok") is True
    return decision.get("reason") not in {
        "invalid_capsule_integrity",
        "invalid_capsule_shape",
        "invalid_capsule",
    }


def evaluate_authority_drift(request: AuthorityDriftRequest) -> AuthorityDriftResult:
    """Observe one call against KCC authority without dispatching anything."""
    capsule = request.capsule
    observed_at = int(request.observed_at or time.time())

    decision = authorize_call(
        capsule,
        request.capability_id,
        operation=request.operation,
        parameters=request.parameters,
        now=observed_at,
    )

    violations: list[AuthorityDriftViolation] = []
    if not decision.get("allowed", False):
        violations.append(
            _violation_from_kcc_reason(str(decision.get("reason", "kcc_denied")))
        )

    claimed_capsule_id = capsule.get("capsule_id")
    if not isinstance(claimed_capsule_id, str):
        claimed_capsule_id = None

    if (
        request.observed_capsule_id
        and claimed_capsule_id
        and request.observed_capsule_id != claimed_capsule_id
    ):
        violations.append(
            AuthorityDriftViolation(
                code="AUTH-CAPSULE-ID-MISMATCH",
                severity="critical",
                message="Runtime correlation capsule ID does not match the supplied capsule.",
            )
        )

    observation = {
        "capsule_id": claimed_capsule_id,
        "observed_capsule_id": request.observed_capsule_id,
        "capability_id": request.capability_id,
        "operation": request.operation,
        "parameters": request.parameters,
        "observed_at": observed_at,
    }

    result = AuthorityDriftResult(
        drift_detected=bool(violations),
        within_claimed_authority=not violations,
        capsule_integrity_valid=_integrity_valid(decision),
        capsule_id=claimed_capsule_id,
        capability_id=request.capability_id,
        operation=request.operation,
        observation_fingerprint=_digest(observation),
        violations=violations,
    )
    result.evaluation_fingerprint = _digest(
        result.model_dump(exclude={"evaluation_fingerprint"})
    )
    return result
