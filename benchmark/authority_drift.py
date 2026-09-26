"""Small adversarial benchmark for the SPECTRAFLOW authority-drift contract."""

from __future__ import annotations

import copy
import hashlib
import json

from spectraflow.authority.drift import AuthorityDriftRequest, evaluate_authority_drift


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _capsule():
    cap = {
        "version": "kcc.capsule.v0",
        "status": "ready",
        "issued_at": 100,
        "expires_at": 200,
        "inventory_digest": "inventory",
        "intent_digest": "intent",
        "policy_digest": "policy",
        "task": "bounded browser operation",
        "constraints": {},
        "grants": [{
            "id": "mcp:browser:browser_tabs",
            "fingerprint": "tool",
            "effect": "unknown",
            "risk_flags": ["context_dependent"],
            "constraints": {
                "operations": ["list"],
                "parameters": {"tab_index": {"type": "integer", "min": 0, "max": 3}},
            },
        }],
        "approvals": [],
        "denials": [],
        "fail_closed": True,
    }
    cap["capsule_id"] = _digest(cap)
    return cap


def _case(label, expected_drift, *, mutate=None, capability=None, operation=None, parameters=None, observed_at=101):
    cap = _capsule()
    if mutate:
        mutate(cap)
    req = AuthorityDriftRequest(
        capsule=cap,
        capability_id=capability or "mcp:browser:browser_tabs",
        operation=operation or "list",
        parameters=parameters if parameters is not None else {"tab_index": 2},
        observed_capsule_id=cap["capsule_id"],
        observed_at=observed_at,
    )
    return label, expected_drift, evaluate_authority_drift(req).drift_detected


def run_benchmark():
    def tamper(cap):
        cap["grants"][0]["constraints"]["operations"].append("close")

    cases = [
        _case("within_authority", False),
        _case("ungranted_capability", True, capability="mcp:browser:browser_type"),
        _case("operation_escape", True, operation="close"),
        _case("parameter_escape", True, parameters={"tab_index": 9}),
        _case("expired", True, observed_at=201),
        _case("tampered_capsule", True, mutate=tamper),
    ]

    tp = sum(1 for _, expected, actual in cases if expected and actual)
    tn = sum(1 for _, expected, actual in cases if not expected and not actual)
    fp = sum(1 for _, expected, actual in cases if not expected and actual)
    fn = sum(1 for _, expected, actual in cases if expected and not actual)

    return {
        "cases": len(cases),
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "drift_recall": tp / (tp + fn) if tp + fn else 1.0,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
        "results": [
            {"label": label, "expected_drift": expected, "actual_drift": actual}
            for label, expected, actual in cases
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
