"""Observe runtime calls against a claimed KCC capsule.

This module is deliberately observational. It does not grant authority and is not
an enforcement replacement for KCC's authorize_call(). Its purpose is to make
runtime authority drift visible even when another enforcement layer is bypassed,
misconfigured, or absent.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Literal

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
    violations: list[AuthorityDriftViolation] = Field(default_factory=list)


def _parameter_violations(
    constraints: dict[str, Any],
    parameters: dict[str, Any],
) -> list[AuthorityDriftViolation]:
    violations: list[AuthorityDriftViolation] = []

    for key, rule in (constraints.get("parameters") or {}).items():
        if isinstance(rule, dict) and rule.get("required") is True and key not in parameters:
            violations.append(
                AuthorityDriftViolation(
                    code="AUTH-PARAM-REQUIRED",
                    severity="high",
                    field=key,
                    message=f"Required parameter {key!r} was absent from the observed call.",
                )
            )
            continue

        if key not in parameters:
            continue
        value = parameters[key]

        if not isinstance(rule, dict):
            if value != rule:
                violations.append(
                    AuthorityDriftViolation(
                        code="AUTH-PARAM-MISMATCH",
                        severity="high",
                        field=key,
                        message=f"Observed value for {key!r} differs from the capsule constraint.",
                    )
                )
            continue

        if "type" in rule:
            kinds: dict[str, Any] = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            expected = kinds.get(rule["type"])
            if expected is None:
                violations.append(
                    AuthorityDriftViolation(
                        code="AUTH-CONSTRAINT-UNSUPPORTED",
                        severity="medium",
                        field=key,
                        message=f"Unsupported parameter type constraint for {key!r}.",
                    )
                )
                continue
            type_matches = isinstance(value, expected)
            if rule["type"] in {"integer", "number"} and isinstance(value, bool):
                type_matches = False
            if not type_matches:
                violations.append(
                    AuthorityDriftViolation(
                        code="AUTH-PARAM-TYPE",
                        severity="high",
                        field=key,
                        message=f"Observed parameter {key!r} violates its type constraint.",
                    )
                )
                continue

        try:
            if "max" in rule and value > rule["max"]:
                violations.append(
                    AuthorityDriftViolation(
                        code="AUTH-PARAM-ABOVE-MAX",
                        severity="high",
                        field=key,
                        message=f"Observed parameter {key!r} exceeds the capsule maximum.",
                    )
                )
            if "min" in rule and value < rule["min"]:
                violations.append(
                    AuthorityDriftViolation(
                        code="AUTH-PARAM-BELOW-MIN",
                        severity="high",
                        field=key,
                        message=f"Observed parameter {key!r} is below the capsule minimum.",
                    )
                )
        except TypeError:
            violations.append(
                AuthorityDriftViolation(
                    code="AUTH-PARAM-COMPARISON",
                    severity="high",
                    field=key,
                    message=f"Observed parameter {key!r} cannot satisfy the numeric constraint.",
                )
            )

        if "enum" in rule and value not in rule["enum"]:
            violations.append(
                AuthorityDriftViolation(
                    code="AUTH-PARAM-ENUM",
                    severity="high",
                    field=key,
                    message=f"Observed parameter {key!r} is outside the allowed values.",
                )
            )

        if "max_length" in rule:
            try:
                too_long = len(value) > rule["max_length"]
            except TypeError:
                too_long = True
            if too_long:
                violations.append(
                    AuthorityDriftViolation(
                        code="AUTH-PARAM-TOO-LONG",
                        severity="high",
                        field=key,
                        message=f"Observed parameter {key!r} exceeds the allowed length.",
                    )
                )

        if "pattern" in rule and not re.fullmatch(rule["pattern"], str(value)):
            violations.append(
                AuthorityDriftViolation(
                    code="AUTH-PARAM-PATTERN",
                    severity="high",
                    field=key,
                    message=f"Observed parameter {key!r} violates the allowed pattern.",
                )
            )

    return violations


def evaluate_authority_drift(request: AuthorityDriftRequest) -> AuthorityDriftResult:
    """Compare one observed runtime call with the authority claimed by a KCC capsule."""
    capsule = request.capsule
    violations: list[AuthorityDriftViolation] = []

    body = dict(capsule)
    claimed_capsule_id = body.pop("capsule_id", None)
    integrity_valid = bool(claimed_capsule_id) and claimed_capsule_id == _digest(body)

    if not integrity_valid:
        violations.append(
            AuthorityDriftViolation(
                code="AUTH-CAPSULE-INTEGRITY",
                severity="critical",
                message="Claimed KCC capsule failed canonical integrity verification.",
            )
        )

    if capsule.get("version") != "kcc.capsule.v0":
        violations.append(
            AuthorityDriftViolation(
                code="AUTH-CAPSULE-VERSION",
                severity="high",
                message="Observed authority artifact is not a supported kcc.capsule.v0 capsule.",
            )
        )

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

    observed_at = int(request.observed_at or time.time())
    expires_at = int(capsule.get("expires_at", 0) or 0)
    if observed_at >= expires_at:
        violations.append(
            AuthorityDriftViolation(
                code="AUTH-CAPSULE-EXPIRED",
                severity="critical",
                message="Runtime call was observed after the capsule expiry boundary.",
            )
        )

    grants = {
        entry.get("id"): entry
        for entry in capsule.get("grants", [])
        if isinstance(entry, dict) and entry.get("id")
    }
    entry = grants.get(request.capability_id)

    if entry is None:
        violations.append(
            AuthorityDriftViolation(
                code="AUTH-CAPABILITY-NOT-GRANTED",
                severity="critical",
                field="capability_id",
                message="Observed capability is absent from the capsule grants.",
            )
        )
    else:
        constraints = entry.get("constraints") or {}
        operations = constraints.get("operations")
        if operations and request.operation not in operations:
            violations.append(
                AuthorityDriftViolation(
                    code="AUTH-OPERATION-NOT-GRANTED",
                    severity="critical",
                    field="operation",
                    message="Observed operation is outside the capsule operation constraint.",
                )
            )
        violations.extend(_parameter_violations(constraints, request.parameters))

    observation = {
        "capsule_id": claimed_capsule_id,
        "observed_capsule_id": request.observed_capsule_id,
        "capability_id": request.capability_id,
        "operation": request.operation,
        "parameters": request.parameters,
        "observed_at": observed_at,
    }

    return AuthorityDriftResult(
        drift_detected=bool(violations),
        within_claimed_authority=not violations,
        capsule_integrity_valid=integrity_valid,
        capsule_id=claimed_capsule_id,
        capability_id=request.capability_id,
        operation=request.operation,
        observation_fingerprint=_digest(observation),
        violations=violations,
    )
