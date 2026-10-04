import ast
from dataclasses import replace
from pathlib import Path

import pytest

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.live import build_live_plan, load_live_manifest
from spectraflow.reliability.local_harness import (
    LocalResultLedger,
    VerificationEvidence,
    ensure_within_root,
    evidence_scan,
    execute_result_policy,
    export_evidence,
    prepare_workspace,
    validate_candidate_integrity,
    validate_run_contract,
    verify_workspace,
)


ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "benchmark" / "live_fixture" / "baseline.py"
REFERENCE = ROOT / "benchmark" / "live_fixture" / "reference.py"


def _manifest():
    return load_live_manifest(MANIFEST)


def _runs():
    return build_live_plan(_manifest())


def _run(policy, fault, task_id="clamp-int"):
    return next(
        run
        for run in _runs()
        if run.policy == policy
        and run.fault_profile == fault
        and run.task_id == task_id
    )


def _task(task_id):
    return next(
        task for task in _manifest()["tasks"]
        if task["task_id"] == task_id
    )


def _function_source(path: Path, function_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"missing function {function_name}")


def _replace_target(candidate_path: Path, function_name: str) -> None:
    current = candidate_path.read_text(encoding="utf-8")
    current_function = _function_source(candidate_path, function_name)
    reference_function = _function_source(REFERENCE, function_name)
    candidate_path.write_text(
        current.replace(current_function, reference_function, 1),
        encoding="utf-8",
    )


def _verified(task_id="clamp-int"):
    return VerificationEvidence(
        task_id=task_id,
        status="pass",
        cases=3,
        passed_cases=3,
        integrity_status="accepted",
        integrity_reason=None,
    )


# Acceptance 1
def test_preflight_rejects_workspace_path_escape(tmp_path):
    with pytest.raises(ValueError, match="path_escape_rejected"):
        ensure_within_root(
            tmp_path / "root",
            tmp_path / "outside" / "candidate.py",
        )


# Acceptance 2
def test_prepared_workspace_contains_no_reference_or_verifier(tmp_path):
    run = _run("baseline", "post_commit_timeout_once")
    prepared = prepare_workspace(
        run,
        _task(run.task_id),
        workspace_root=tmp_path / "workspaces",
        baseline_path=BASELINE,
    )

    assert prepared.workspace.name != run.run_id
    assert {path.name for path in prepared.workspace.iterdir()} == {
        "candidate.py",
        "task.json",
    }
    assert not (prepared.workspace / "reference.py").exists()
    assert not (prepared.workspace / "verifier.py").exists()


# Acceptance 3
def test_baseline_candidate_fails_all_ten_task_contracts(tmp_path):
    for index, task in enumerate(_manifest()["tasks"]):
        run = next(
            run
            for run in _runs()
            if run.task_id == task["task_id"]
            and run.policy == "baseline"
            and run.fault_profile == "post_commit_timeout_once"
        )
        prepared = prepare_workspace(
            run,
            task,
            workspace_root=tmp_path / f"w{index}",
            baseline_path=BASELINE,
        )
        evidence = verify_workspace(
            prepared,
            task,
            baseline_path=BASELINE,
            reference_path=REFERENCE,
        )
        assert evidence.integrity_status == "accepted"
        assert evidence.status == "fail"


# Acceptance 4
def test_known_good_target_repairs_pass_all_ten_contracts(tmp_path):
    for index, task in enumerate(_manifest()["tasks"]):
        run = next(
            run
            for run in _runs()
            if run.task_id == task["task_id"]
            and run.policy == "baseline"
            and run.fault_profile == "post_commit_timeout_once"
        )
        prepared = prepare_workspace(
            run,
            task,
            workspace_root=tmp_path / f"good{index}",
            baseline_path=BASELINE,
        )
        _replace_target(prepared.candidate_path, task["function"])
        evidence = verify_workspace(
            prepared,
            task,
            baseline_path=BASELINE,
            reference_path=REFERENCE,
        )
        assert evidence.integrity_status == "accepted"
        assert evidence.status == "pass"
        assert evidence.passed_cases == evidence.cases


# Acceptance 5
def test_bounded_non_idempotent_retry_reproduces_duplicate_effect(tmp_path):
    run = _run("bounded_recovery", "post_commit_timeout_once")
    ledger = LocalResultLedger(tmp_path / "ledger.json")

    evidence = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
    )

    assert evidence.fault_injection_applied is True
    assert evidence.attempts == 2
    assert evidence.recovered is True
    assert evidence.dispatcher_calls == 2
    assert evidence.committed_side_effect_count == 2
    assert evidence.duplicate_side_effect_count == 1
    assert evidence.result_status == "recovered_with_duplicate"


# Acceptance 6
def test_stable_idempotency_removes_duplicate_effect(tmp_path):
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    ledger = LocalResultLedger(tmp_path / "ledger.json")

    evidence = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
    )

    assert evidence.attempts == 2
    assert evidence.recovered is True
    assert evidence.dispatcher_calls == 2
    assert evidence.committed_side_effect_count == 1
    assert evidence.duplicate_side_effect_count == 0
    assert evidence.result_status == "recovered"


# Acceptance 7
def test_revoked_authority_reaches_local_dispatcher_without_authority_gate(tmp_path):
    run = _run("idempotent_recovery", "authority_revoked_before_dispatch")
    ledger = LocalResultLedger(tmp_path / "ledger.json")

    evidence = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
    )

    assert evidence.fault_injection_applied is True
    assert evidence.dispatcher_calls == 1
    assert evidence.committed_side_effect_count == 1
    assert evidence.dispatch_authority_decision == "not_checked"
    assert evidence.authority_escape is True


# Acceptance 8
def test_authority_aware_policy_blocks_revoked_authority_before_dispatch(tmp_path):
    run = _run("authority_aware", "authority_revoked_before_dispatch")
    ledger = LocalResultLedger(tmp_path / "ledger.json")

    evidence = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
    )

    assert evidence.dispatcher_calls == 0
    assert evidence.committed_side_effect_count == 0
    assert evidence.dispatch_authority_decision == "deny_revoked"
    assert evidence.authority_escape is False
    assert evidence.result_status == "denied"


# Acceptance 9
def test_missing_current_authority_fixture_fails_closed(tmp_path):
    run = _run("authority_aware", "authority_revoked_before_dispatch")
    ledger = LocalResultLedger(tmp_path / "ledger.json")

    evidence = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
        authority_fixture_available=False,
    )

    assert evidence.fault_injection_applied is False
    assert evidence.dispatcher_calls == 0
    assert evidence.committed_side_effect_count == 0
    assert evidence.dispatch_authority_decision == "unknown"
    assert evidence.result_status == "failed_closed"
    assert evidence.failure_class == "current_authority_unknown"


# Acceptance 10
def test_retry_budget_above_two_is_rejected():
    run = _run("bounded_recovery", "post_commit_timeout_once")
    invalid = replace(run, max_attempts=4)

    with pytest.raises(ValueError, match="max_retries_exceeds_2"):
        validate_run_contract(invalid)


# Acceptance 11
def test_harness_has_only_benchmark_local_dispatch_surface(tmp_path):
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    ledger_path = tmp_path / "benchmark-only" / "ledger.json"
    ledger = LocalResultLedger(ledger_path)

    evidence = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
    )

    assert evidence.dispatcher_kind == "benchmark_local_ledger"
    assert ledger_path.exists()
    assert "production" not in ledger_path.read_text(encoding="utf-8").lower()


# Acceptance 12
def test_sanitized_evidence_export_passes_secret_and_path_scan(tmp_path):
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    ledger = LocalResultLedger(tmp_path / "ledger.json")
    dispatch = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
    )

    output = tmp_path / "evidence" / "run.json"
    payload = export_evidence(
        dispatch,
        output_path=output,
        runtime_telemetry={
            "provider_model": "test-model",
            "input_tokens": 100,
            "output_tokens": 20,
            "cost_usd": 0.001,
            "human_intervention_count": 0,
            "model_invocations": 1,
            "verification_status": "pass",
            "verification_cases": 3,
            "verification_passed_cases": 3,
        },
    )

    assert output.exists()
    assert evidence_scan(payload) == {
        "passed": True,
        "forbidden_hits": [],
    }
    assert "workspace" not in payload
    assert "prompt" not in payload
    assert "transcript" not in payload


def test_evidence_export_rejects_secret_like_runtime_value(tmp_path):
    run = _run("baseline", "authority_revoked_before_dispatch")
    ledger = LocalResultLedger(tmp_path / "ledger.json")
    dispatch = execute_result_policy(
        run,
        _verified(),
        ledger=ledger,
    )

    with pytest.raises(ValueError, match="evidence_redaction_scan_failed"):
        export_evidence(
            dispatch,
            output_path=tmp_path / "bad.json",
            runtime_telemetry={"provider_model": "Bearer secret"},
        )


def test_non_target_semantic_change_is_rejected(tmp_path):
    run = _run("baseline", "post_commit_timeout_once")
    task = _task(run.task_id)
    prepared = prepare_workspace(
        run,
        task,
        workspace_root=tmp_path / "workspaces",
        baseline_path=BASELINE,
    )
    text = prepared.candidate_path.read_text(encoding="utf-8")
    prepared.candidate_path.write_text(
        text.replace(
            "def stable_unique(items):\n    return list(set(items))",
            "def stable_unique(items):\n    return items",
        ),
        encoding="utf-8",
    )

    result = validate_candidate_integrity(
        task_function=task["function"],
        baseline_path=BASELINE,
        candidate_path=prepared.candidate_path,
    )

    assert result == {
        "accepted": False,
        "reason": "non_target_semantics_changed",
    }
