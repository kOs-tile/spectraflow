import copy

import kavi_capability_compiler as kcc

from spectraflow.authority.drift import (
    AuthorityDriftRequest,
    evaluate_authority_drift,
)


def make_context(*, now=100, ttl=100):
    manifest = kcc.adapt_capabilities(
        "generic",
        {
            "tools": [
                {
                    "name": "browser_tabs",
                    "description": "List browser tabs",
                    "input_schema": {
                        "type": "object",
                        "properties": {"tab_index": {"type": "integer"}},
                    },
                }
            ]
        },
        namespace="browser",
    )
    inventory = kcc.scan_manifest(manifest)
    cid = inventory["capabilities"][0]["id"]
    capsule = kcc.compile_capsule(
        inventory,
        {
            "task": "bounded browser operation",
            "capabilities": [cid],
            "ttl_seconds": ttl,
            "capability_constraints": {
                cid: {
                    "operations": ["list"],
                    "parameters": {
                        "tab_index": {
                            "type": "integer",
                            "min": 0,
                            "max": 3,
                        }
                    },
                }
            },
        },
        {"default": "deny", "allow": [{"capabilities": [cid]}]},
        now=now,
    )
    assert capsule["version"] == kcc.CAPSULE_VERSION == "kcc.capsule.v1"
    return {"capsule": capsule, "capability_id": cid}


def evaluate(ctx=None, **kwargs):
    ctx = ctx or make_context()
    cap = ctx["capsule"]
    request = AuthorityDriftRequest(
        capsule=cap,
        capability_id=kwargs.pop("capability_id", ctx["capability_id"]),
        operation=kwargs.pop("operation", "list"),
        parameters=kwargs.pop("parameters", {"tab_index": 2}),
        observed_capsule_id=kwargs.pop("observed_capsule_id", cap.get("capsule_id")),
        observed_at=kwargs.pop("observed_at", 101),
        **kwargs,
    )
    return evaluate_authority_drift(request)


def test_current_v1_call_within_capsule_has_no_authority_drift():
    result = evaluate()

    assert result.drift_detected is False
    assert result.within_claimed_authority is True
    assert result.capsule_integrity_valid is True
    assert result.violations == []


def test_ungranted_capability_is_critical_drift():
    result = evaluate(capability_id="kcc:browser:browser_type")

    assert result.drift_detected is True
    assert result.within_claimed_authority is False
    assert any(v.code == "AUTH-CAPABILITY-NOT-GRANTED" for v in result.violations)


def test_operation_constraint_violation_is_detected():
    result = evaluate(operation="close")

    assert any(v.code == "AUTH-OPERATION-NOT-GRANTED" for v in result.violations)


def test_parameter_boundary_violation_is_detected():
    result = evaluate(parameters={"tab_index": 9})

    assert any(v.code == "AUTH-PARAM-ABOVE-MAX" for v in result.violations)


def test_expired_capsule_is_detected():
    ctx = make_context(now=100, ttl=1)
    result = evaluate(ctx, observed_at=102)

    assert any(v.code == "AUTH-CAPSULE-EXPIRED" for v in result.violations)


def test_capsule_tampering_is_detected():
    ctx = make_context()
    tampered = copy.deepcopy(ctx["capsule"])
    tampered["grants"][0]["constraints"]["operations"] = ["list", "close"]

    result = evaluate(
        {"capsule": tampered, "capability_id": ctx["capability_id"]},
        observed_capsule_id=tampered.get("capsule_id"),
    )

    assert result.capsule_integrity_valid is False
    assert any(v.code == "AUTH-CAPSULE-INTEGRITY" for v in result.violations)


def test_runtime_capsule_id_mismatch_is_detected():
    result = evaluate(observed_capsule_id="wrong-capsule-id")

    assert any(v.code == "AUTH-CAPSULE-ID-MISMATCH" for v in result.violations)


def test_boolean_does_not_satisfy_integer_constraint():
    result = evaluate(parameters={"tab_index": True})

    assert any(v.code == "AUTH-PARAM-TYPE" for v in result.violations)


def test_observation_fingerprint_is_deterministic():
    ctx = make_context()
    first = evaluate(ctx)
    second = evaluate(ctx)

    assert first.observation_fingerprint == second.observation_fingerprint


def test_evaluation_fingerprint_is_deterministic():
    ctx = make_context()
    first = evaluate(ctx)
    second = evaluate(ctx)

    assert len(first.evaluation_fingerprint) == 64
    assert first.evaluation_fingerprint == second.evaluation_fingerprint


def test_evaluation_fingerprint_binds_decision():
    ctx = make_context()
    clean = evaluate(ctx)
    violating = evaluate(ctx, operation="close")

    assert clean.evaluation_fingerprint != violating.evaluation_fingerprint


def _literal_tenant_context():
    """A patched KCC capsule: tenant ID is a literal mandatory grant."""
    inventory = kcc.scan_manifest(
        kcc.adapt_capabilities(
            "generic",
            {"tools": [{
                "name": "read_record",
                "description": "Read a synthetic customer record",
                "input_schema": {
                    "type": "object",
                    "properties": {"customer_id": {"type": "string"}},
                    "required": ["customer_id"],
                },
            }]},
            namespace="spectraflow-security-regression",
        )
    )
    cid = inventory["capabilities"][0]["id"]
    capsule = kcc.compile_capsule(
        inventory,
        {
            "task": "Read only tenant-a-001",
            "capabilities": [cid],
            "ttl_seconds": 300,
            "capability_constraints": {
                cid: {
                    "operations": ["read"],
                    "parameters": {"customer_id": "tenant-a-001"},
                }
            },
        },
        {"default": "allow"},
        now=100,
    )
    return {"capsule": capsule, "capability_id": cid}


def test_literal_tenant_omission_is_reported_as_security_drift():
    """Regression for KCC <=0.1.1: literal constraint could be bypassed by omission."""
    context = _literal_tenant_context()
    result = evaluate(context, operation="read", parameters={})
    assert result.drift_detected is True
    assert result.within_claimed_authority is False
    assert result.capsule_integrity_valid is True
    assert any(
        v.code == "AUTH-PARAM-REQUIRED" and v.field == "customer_id"
        for v in result.violations
    )


def test_correct_literal_tenant_read_is_not_falsely_flagged():
    context = _literal_tenant_context()
    result = evaluate(
        context, operation="read", parameters={"customer_id": "tenant-a-001"}
    )
    assert result.drift_detected is False
    assert result.within_claimed_authority is True
    assert result.violations == []


def test_wrong_literal_tenant_and_scope_escalation_are_detected():
    context = _literal_tenant_context()
    wrong_tenant = evaluate(
        context, operation="read", parameters={"customer_id": "tenant-b-001"}
    )
    assert wrong_tenant.drift_detected is True
    assert any(v.code == "AUTH-PARAM-MISMATCH" for v in wrong_tenant.violations)

    extra_scope = evaluate(
        context, operation="read",
        parameters={"customer_id": "tenant-a-001", "all_tenants": True},
    )
    assert extra_scope.drift_detected is True
    assert any(
        v.code == "AUTH-PARAM-NOT-GRANTED" for v in extra_scope.violations
    )
