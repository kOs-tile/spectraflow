"""Small adversarial benchmark for the SPECTRAFLOW authority-drift contract."""

from __future__ import annotations

import copy
import json

import kavi_capability_compiler as kcc

from spectraflow.authority.drift import AuthorityDriftRequest, evaluate_authority_drift


def _context():
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
            "ttl_seconds": 100,
            "capability_constraints": {
                cid: {
                    "operations": ["list"],
                    "parameters": {
                        "tab_index": {"type": "integer", "min": 0, "max": 3}
                    },
                }
            },
        },
        {"default": "deny", "allow": [{"capabilities": [cid]}]},
        now=100,
    )
    return capsule, cid


def _case(
    label,
    expected_drift,
    *,
    mutate=None,
    capability=None,
    operation=None,
    parameters=None,
    observed_at=101,
):
    cap, cid = _context()
    if mutate:
        mutate(cap)
    req = AuthorityDriftRequest(
        capsule=cap,
        capability_id=capability or cid,
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
        _case("ungranted_capability", True, capability="kcc:browser:browser_type"),
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
        "capsule_version": kcc.CAPSULE_VERSION,
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
