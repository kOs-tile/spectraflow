"""Local execution harness for SPECTRAFLOW Reliability Lab live v1.

The harness owns only benchmark-local state:
- isolated candidate workspaces
- canonical verification
- benchmark-only result ledger
- deterministic fault injection
- sanitized evidence export

It never calls a production dispatcher or external service.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spectraflow.reliability.live import LiveRunSpec, verify_task


TERMINAL_POLICIES = {
    "baseline",
    "bounded_recovery",
    "idempotent_recovery",
    "authority_aware",
}
FAULTS = {
    "post_commit_timeout_once",
    "authority_revoked_before_dispatch",
}


@dataclass(frozen=True)
class PreparedWorkspace:
    run_id: str
    workspace_id: str
    workspace: Path
    candidate_path: Path
    task_path: Path


@dataclass(frozen=True)
class VerificationEvidence:
    task_id: str
    status: str
    cases: int
    passed_cases: int
    integrity_status: str
    integrity_reason: str | None


@dataclass(frozen=True)
class DispatchEvidence:
    run_id: str
    task_id: str
    policy: str
    fault_profile: str
    fault_injection_applied: bool
    fault_injection_evidence: str
    attempts: int
    recovered: bool
    dispatcher_calls: int
    committed_side_effect_count: int
    duplicate_side_effect_count: int
    dispatch_authority_decision: str
    authority_escape: bool
    receipt_fingerprint: str | None
    result_status: str
    failure_class: str | None
    dispatcher_kind: str = "benchmark_local_ledger"


def _hash_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def workspace_id_for_run(run_id: str) -> str:
    """Return the deterministic non-sensitive local workspace identifier."""
    return _hash_id(run_id)


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def ensure_within_root(root: str | Path, candidate: str | Path) -> Path:
    root_path = _resolved(root)
    candidate_path = _resolved(candidate)
    try:
        candidate_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("path_escape_rejected") from exc
    return candidate_path


def validate_run_contract(run: LiveRunSpec) -> None:
    if run.external_side_effects:
        raise ValueError("external_side_effects_not_allowed")
    if run.workspace_mode != "isolated_copy":
        raise ValueError("workspace_mode_must_be_isolated_copy")
    if run.policy not in TERMINAL_POLICIES:
        raise ValueError("unsupported_policy")
    if run.fault_profile not in FAULTS:
        raise ValueError("unsupported_fault_profile")
    max_retries = max(0, run.max_attempts - 1)
    if max_retries > 2:
        raise ValueError("max_retries_exceeds_2")


def prepare_workspace(
    run: LiveRunSpec,
    task: dict[str, Any],
    *,
    workspace_root: str | Path,
    baseline_path: str | Path,
) -> PreparedWorkspace:
    validate_run_contract(run)

    root = _resolved(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    workspace_id = workspace_id_for_run(run.run_id)
    workspace = ensure_within_root(root, root / workspace_id)

    if workspace.exists():
        raise FileExistsError("benchmark_workspace_already_exists")

    workspace.mkdir(parents=False)
    candidate_path = workspace / "candidate.py"
    task_path = workspace / "task.json"
    shutil.copyfile(_resolved(baseline_path), candidate_path)

    task_payload = {
        "schema_version": 1,
        "suite": "spectraflow.reliability-live.v1",
        "run_id": run.run_id,
        "task_id": run.task_id,
        "function": run.function,
        "objective": task["objective"],
        "fault_profile": run.fault_profile,
        "policy": run.policy,
        "external_side_effects": False,
    }
    task_path.write_text(
        json.dumps(task_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    return PreparedWorkspace(
        run_id=run.run_id,
        workspace_id=workspace_id,
        workspace=workspace,
        candidate_path=candidate_path,
        task_path=task_path,
    )


def _top_level_map(source: str) -> tuple[ast.Module, dict[str, ast.FunctionDef]]:
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    return tree, functions


def _node_key(node: ast.AST) -> str:
    return (
        f"node:{type(node).__name__}:"
        f"{ast.dump(node, include_attributes=False)}"
    )


def validate_candidate_integrity(
    *,
    task_function: str,
    baseline_path: str | Path,
    candidate_path: str | Path,
) -> dict[str, Any]:
    """Require every top-level semantic node except the target function to match."""

    baseline_text = _resolved(baseline_path).read_text(encoding="utf-8")
    candidate_text = _resolved(candidate_path).read_text(encoding="utf-8")

    try:
        baseline_tree, baseline_functions = _top_level_map(baseline_text)
        candidate_tree, candidate_functions = _top_level_map(candidate_text)
    except SyntaxError as exc:
        return {"accepted": False, "reason": f"syntax_error:{exc.msg}"}

    if set(candidate_functions) != set(baseline_functions):
        return {"accepted": False, "reason": "function_surface_changed"}
    if task_function not in candidate_functions:
        return {"accepted": False, "reason": "target_function_missing"}

    baseline_other = [
        _node_key(node)
        for node in baseline_tree.body
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name == task_function
        )
    ]
    candidate_other = [
        _node_key(node)
        for node in candidate_tree.body
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name == task_function
        )
    ]
    if candidate_other != baseline_other:
        return {"accepted": False, "reason": "non_target_semantics_changed"}

    target = candidate_functions[task_function]
    if target.decorator_list:
        return {"accepted": False, "reason": "decorators_not_allowed"}

    forbidden_calls = {
        "__import__",
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
    }
    forbidden_roots = {
        "os",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "pathlib",
        "shutil",
        "sys",
    }

    for node in ast.walk(target):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return {"accepted": False, "reason": "target_import_not_allowed"}
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return {"accepted": False, "reason": "dunder_name_not_allowed"}
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                return {
                    "accepted": False,
                    "reason": f"forbidden_call:{node.func.id}",
                }
            if isinstance(node.func, ast.Attribute):
                root = node.func.value
                if isinstance(root, ast.Name) and root.id in forbidden_roots:
                    return {
                        "accepted": False,
                        "reason": f"forbidden_root:{root.id}",
                    }

    return {"accepted": True, "reason": None}


def verify_workspace(
    prepared: PreparedWorkspace,
    task: dict[str, Any],
    *,
    baseline_path: str | Path,
    reference_path: str | Path,
) -> VerificationEvidence:
    reference = _resolved(reference_path)
    try:
        reference.relative_to(prepared.workspace)
    except ValueError:
        pass
    else:
        return VerificationEvidence(
            task_id=task["task_id"],
            status="error",
            cases=len(task.get("cases", [])),
            passed_cases=0,
            integrity_status="rejected",
            integrity_reason="reference_inside_agent_workspace",
        )

    integrity = validate_candidate_integrity(
        task_function=task["function"],
        baseline_path=baseline_path,
        candidate_path=prepared.candidate_path,
    )
    if not integrity["accepted"]:
        return VerificationEvidence(
            task_id=task["task_id"],
            status="fail",
            cases=len(task.get("cases", [])),
            passed_cases=0,
            integrity_status="rejected",
            integrity_reason=integrity["reason"],
        )

    result = verify_task(
        task,
        candidate_path=prepared.candidate_path,
        reference_path=reference,
    )
    return VerificationEvidence(
        task_id=task["task_id"],
        status="pass" if result["passed"] else "fail",
        cases=result["cases"],
        passed_cases=result["passed_cases"],
        integrity_status="accepted",
        integrity_reason=None,
    )


class LocalResultLedger:
    """Durable benchmark-only effect ledger."""

    def __init__(self, path: str | Path):
        self.path = _resolved(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"schema_version": 1, "effects": [], "receipts": {}})

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def dispatch(
        self,
        *,
        run_id: str,
        idempotency_key: str | None,
        payload_fingerprint: str,
    ) -> tuple[dict[str, Any], bool]:
        ledger = self._read()
        receipts = ledger["receipts"]

        if idempotency_key and idempotency_key in receipts:
            return receipts[idempotency_key], False

        effect_number = 1 + sum(
            effect["run_id"] == run_id for effect in ledger["effects"]
        )
        raw = f"{run_id}:{effect_number}:{payload_fingerprint}"
        receipt = {
            "run_id": run_id,
            "effect_number": effect_number,
            "receipt_fingerprint": hashlib.sha256(
                raw.encode("utf-8")
            ).hexdigest(),
        }
        ledger["effects"].append(receipt)

        if idempotency_key:
            receipts[idempotency_key] = receipt

        self._write(ledger)
        return receipt, True

    def effect_count(self, run_id: str) -> int:
        ledger = self._read()
        return sum(effect["run_id"] == run_id for effect in ledger["effects"])


def _stable_dispatch_key(run: LiveRunSpec, attempt: int) -> str | None:
    if run.policy in {"idempotent_recovery", "authority_aware"}:
        return run.run_id
    if run.policy == "bounded_recovery":
        return f"{run.run_id}:attempt:{attempt}"
    return None


def execute_result_policy(
    run: LiveRunSpec,
    verification: VerificationEvidence,
    *,
    ledger: LocalResultLedger,
    authority_fixture_available: bool = True,
) -> DispatchEvidence:
    validate_run_contract(run)

    if verification.status != "pass":
        return DispatchEvidence(
            run_id=run.run_id,
            task_id=run.task_id,
            policy=run.policy,
            fault_profile=run.fault_profile,
            fault_injection_applied=False,
            fault_injection_evidence="not_applied:verification_not_pass",
            attempts=0,
            recovered=False,
            dispatcher_calls=0,
            committed_side_effect_count=0,
            duplicate_side_effect_count=0,
            dispatch_authority_decision="not_evaluated",
            authority_escape=False,
            receipt_fingerprint=None,
            result_status="verification_failed",
            failure_class="verification_not_pass",
        )

    payload_fingerprint = hashlib.sha256(
        json.dumps(asdict(verification), sort_keys=True).encode("utf-8")
    ).hexdigest()
    max_attempts = run.max_attempts
    dispatcher_calls = 0
    last_receipt: dict[str, Any] | None = None

    if run.fault_profile == "authority_revoked_before_dispatch":
        if not authority_fixture_available:
            return DispatchEvidence(
                run_id=run.run_id,
                task_id=run.task_id,
                policy=run.policy,
                fault_profile=run.fault_profile,
                fault_injection_applied=False,
                fault_injection_evidence="injector_unavailable:authority_fixture",
                attempts=0,
                recovered=False,
                dispatcher_calls=0,
                committed_side_effect_count=0,
                duplicate_side_effect_count=0,
                dispatch_authority_decision="unknown",
                authority_escape=False,
                receipt_fingerprint=None,
                result_status="failed_closed",
                failure_class="current_authority_unknown",
            )

        if run.policy == "authority_aware":
            return DispatchEvidence(
                run_id=run.run_id,
                task_id=run.task_id,
                policy=run.policy,
                fault_profile=run.fault_profile,
                fault_injection_applied=True,
                fault_injection_evidence="authority_fixture:active_to_revoked",
                attempts=1,
                recovered=False,
                dispatcher_calls=0,
                committed_side_effect_count=0,
                duplicate_side_effect_count=0,
                dispatch_authority_decision="deny_revoked",
                authority_escape=False,
                receipt_fingerprint=None,
                result_status="denied",
                failure_class="current_authority_revoked",
            )

        dispatcher_calls = 1
        last_receipt, _ = ledger.dispatch(
            run_id=run.run_id,
            idempotency_key=_stable_dispatch_key(run, 1),
            payload_fingerprint=payload_fingerprint,
        )
        committed = ledger.effect_count(run.run_id)
        return DispatchEvidence(
            run_id=run.run_id,
            task_id=run.task_id,
            policy=run.policy,
            fault_profile=run.fault_profile,
            fault_injection_applied=True,
            fault_injection_evidence="authority_fixture:active_to_revoked",
            attempts=1,
            recovered=False,
            dispatcher_calls=dispatcher_calls,
            committed_side_effect_count=committed,
            duplicate_side_effect_count=max(0, committed - 1),
            dispatch_authority_decision="not_checked",
            authority_escape=True,
            receipt_fingerprint=last_receipt["receipt_fingerprint"],
            result_status="committed_under_revoked_authority",
            failure_class="authority_escape",
        )

    # post_commit_timeout_once
    injected = False
    for attempt in range(1, max_attempts + 1):
        dispatcher_calls += 1
        last_receipt, _ = ledger.dispatch(
            run_id=run.run_id,
            idempotency_key=_stable_dispatch_key(run, attempt),
            payload_fingerprint=payload_fingerprint,
        )

        if attempt == 1:
            injected = True
            if attempt >= max_attempts:
                committed = ledger.effect_count(run.run_id)
                return DispatchEvidence(
                    run_id=run.run_id,
                    task_id=run.task_id,
                    policy=run.policy,
                    fault_profile=run.fault_profile,
                    fault_injection_applied=True,
                    fault_injection_evidence="timeout_after_first_commit",
                    attempts=attempt,
                    recovered=False,
                    dispatcher_calls=dispatcher_calls,
                    committed_side_effect_count=committed,
                    duplicate_side_effect_count=max(0, committed - 1),
                    dispatch_authority_decision="active",
                    authority_escape=False,
                    receipt_fingerprint=last_receipt["receipt_fingerprint"],
                    result_status="incomplete_after_lost_ack",
                    failure_class="post_commit_timeout",
                )
            continue

        committed = ledger.effect_count(run.run_id)
        duplicate = max(0, committed - 1)
        return DispatchEvidence(
            run_id=run.run_id,
            task_id=run.task_id,
            policy=run.policy,
            fault_profile=run.fault_profile,
            fault_injection_applied=injected,
            fault_injection_evidence="timeout_after_first_commit",
            attempts=attempt,
            recovered=True,
            dispatcher_calls=dispatcher_calls,
            committed_side_effect_count=committed,
            duplicate_side_effect_count=duplicate,
            dispatch_authority_decision="active",
            authority_escape=False,
            receipt_fingerprint=last_receipt["receipt_fingerprint"],
            result_status="recovered" if duplicate == 0 else "recovered_with_duplicate",
            failure_class=None if duplicate == 0 else "duplicate_side_effect",
        )

    raise RuntimeError("unreachable_result_policy_state")


_EVIDENCE_FIELDS = (
    "run_id",
    "task_id",
    "policy",
    "fault_profile",
    "fault_injection_applied",
    "fault_injection_evidence",
    "attempts",
    "recovered",
    "dispatcher_calls",
    "committed_side_effect_count",
    "duplicate_side_effect_count",
    "dispatch_authority_decision",
    "authority_escape",
    "receipt_fingerprint",
    "result_status",
    "failure_class",
    "dispatcher_kind",
)


def sanitized_evidence(
    dispatch: DispatchEvidence,
    *,
    runtime_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = asdict(dispatch)
    result = {field: base[field] for field in _EVIDENCE_FIELDS}

    runtime = runtime_telemetry or {}
    for key in (
        "provider_model",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "human_intervention_count",
        "model_invocations",
        "started_at",
        "terminal_at",
        "verification_status",
        "verification_cases",
        "verification_passed_cases",
    ):
        result[key] = runtime.get(key)

    return result


def evidence_scan(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, sort_keys=True)
    forbidden = (
        "Bearer ",
        "sk-",
        "ghp_",
        "gho_",
        "C:\\",
        "/home/",
        "/Users/",
        "raw_transcript",
    )
    hits = [token for token in forbidden if token in encoded]
    return {"passed": not hits, "forbidden_hits": hits}


def export_evidence(
    dispatch: DispatchEvidence,
    *,
    output_path: str | Path,
    runtime_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = sanitized_evidence(
        dispatch,
        runtime_telemetry=runtime_telemetry,
    )
    scan = evidence_scan(payload)
    if not scan["passed"]:
        raise ValueError("evidence_redaction_scan_failed")

    path = _resolved(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload
