import copy

from spectraflow.authority.drift import (
    AuthorityDriftRequest,
    evaluate_authority_drift,
)


def _canonical(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    import hashlib
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def make_capsule(*, now=100, ttl=100):
    cap = {
        "version": "kcc.capsule.v0",
        "status": "ready",
        "issued_at": now,
        "expires_at": now + ttl,
        "inventory_digest": "inventory",
        "intent_digest": "intent",
        "policy_digest": "policy",
        "task": "bounded browser operation",
        "constraints": {},
        "grants": [
            {
                "id": "mcp:browser:browser_tabs",
                "fingerprint": "tool-fingerprint",
                "effect": "unknown",
                "risk_flags": ["context_dependent"],
                "constraints": {
                    "operations": ["list"],
                    "parameters": {
                        "tab_index": {
                            "type": "integer",
                            "min": 0,
                            "max": 3,
                        }
                    },
                },
            }
        ],
        "approvals": [],
        "denials": [],
        "fail_closed": True,
    }
    cap["capsule_id"] = _digest(cap)
    return cap


def evaluate(cap, **kwargs):
    request = AuthorityDriftRequest(
        capsule=cap,
        capability_id=kwargs.pop("capability_id", "mcp:browser:browser_tabs"),
        operation=kwargs.pop("operation", "list"),
        parameters=kwargs.pop("parameters", {"tab_index": 2}),
        observed_capsule_id=kwargs.pop("observed_capsule_id", cap.get("capsule_id")),
        observed_at=kwargs.pop("observed_at", 101),
        **kwargs,
    )
    return evaluate_authority_drift(request)


def test_call_within_capsule_has_no_authority_drift():
    result = evaluate(make_capsule())

    assert result.drift_detected is False
    assert result.within_claimed_authority is True
    assert result.capsule_integrity_valid is True
    assert result.violations == []


def test_ungranted_capability_is_critical_drift():
    result = evaluate(
        make_capsule(),
        capability_id="mcp:browser:browser_type",
    )

    assert result.drift_detected is True
    assert result.within_claimed_authority is False
    assert any(v.code == "AUTH-CAPABILITY-NOT-GRANTED" for v in result.violations)


def test_operation_constraint_violation_is_detected():
    result = evaluate(make_capsule(), operation="close")

    assert any(v.code == "AUTH-OPERATION-NOT-GRANTED" for v in result.violations)


def test_parameter_boundary_violation_is_detected():
    result = evaluate(make_capsule(), parameters={"tab_index": 9})

    assert any(v.code == "AUTH-PARAM-ABOVE-MAX" for v in result.violations)


def test_expired_capsule_is_detected():
    result = evaluate(make_capsule(now=100, ttl=1), observed_at=102)

    assert any(v.code == "AUTH-CAPSULE-EXPIRED" for v in result.violations)


def test_capsule_tampering_is_detected():
    cap = make_capsule()
    tampered = copy.deepcopy(cap)
    tampered["grants"][0]["constraints"]["operations"] = ["list", "close"]

    result = evaluate(tampered)

    assert result.capsule_integrity_valid is False
    assert any(v.code == "AUTH-CAPSULE-INTEGRITY" for v in result.violations)


def test_runtime_capsule_id_mismatch_is_detected():
    result = evaluate(make_capsule(), observed_capsule_id="wrong-capsule-id")

    assert any(v.code == "AUTH-CAPSULE-ID-MISMATCH" for v in result.violations)


def test_observation_fingerprint_is_deterministic():
    cap = make_capsule()
    first = evaluate(cap)
    second = evaluate(cap)

    assert first.observation_fingerprint == second.observation_fingerprint


def test_boolean_does_not_satisfy_integer_constraint():
    result = evaluate(make_capsule(), parameters={"tab_index": True})

    assert any(v.code == "AUTH-PARAM-TYPE" for v in result.violations)
