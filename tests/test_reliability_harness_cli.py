import ast
from pathlib import Path

from benchmark.reliability_harness_cli import (
    BASELINE,
    REFERENCE,
    canary_plan,
    dispatch_command,
    full_plan,
    prepare_command,
    verify_command,
)
from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.live import build_live_plan, load_live_manifest


def _run(policy, fault, task_id="clamp-int"):
    manifest = load_live_manifest(MANIFEST)
    return next(
        run
        for run in build_live_plan(manifest)
        if run.policy == policy
        and run.fault_profile == fault
        and run.task_id == task_id
    )


def _function_source(path: Path, function_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(text, node)
    raise AssertionError(function_name)


def _apply_reference_fix(candidate: Path, function_name: str):
    current = candidate.read_text(encoding="utf-8")
    buggy = _function_source(candidate, function_name)
    fixed = _function_source(REFERENCE, function_name)
    candidate.write_text(current.replace(buggy, fixed, 1), encoding="utf-8")


def test_cli_canary_plan_is_one_task_across_all_eight_arms():
    result = canary_plan()

    assert result["task_id"] == "clamp-int"
    assert result["run_count"] == 8
    assert len(set(result["run_ids"])) == 8
    assert result["expected_faults"] == 2
    assert result["expected_policies"] == 4


def test_cli_full_plan_is_eighty_runs():
    result = full_plan()

    assert result["run_count"] == 80
    assert len(set(result["run_ids"])) == 80


def test_prepare_creates_only_local_candidate_and_task_contract(tmp_path):
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    result = prepare_command(run.run_id, tmp_path / "workspaces")

    workspace = Path(result["workspace"])
    assert workspace.exists()
    assert {path.name for path in workspace.iterdir()} == {
        "candidate.py",
        "task.json",
    }
    assert "reference.py" not in {path.name for path in workspace.iterdir()}
    assert "Do not access network services" in result["agent_instruction"]
    assert result["network_dispatch_performed"] is False


def test_prepare_verify_dispatch_lifecycle_produces_sanitized_evidence(tmp_path):
    run = _run("idempotent_recovery", "post_commit_timeout_once")
    workspace_root = tmp_path / "workspaces"
    evidence_root = tmp_path / "evidence"

    prepared = prepare_command(run.run_id, workspace_root)
    candidate = Path(prepared["candidate_path"])
    _apply_reference_fix(candidate, run.function)

    verified = verify_command(
        run.run_id,
        workspace_root=workspace_root,
        evidence_root=evidence_root,
    )
    assert verified["status"] == "pass"
    assert verified["passed_cases"] == verified["cases"]

    dispatched = dispatch_command(
        run.run_id,
        evidence_root=evidence_root,
    )
    assert dispatched["fault_injection_applied"] is True
    assert dispatched["attempts"] == 2
    assert dispatched["recovered"] is True
    assert dispatched["committed_side_effect_count"] == 1
    assert dispatched["duplicate_side_effect_count"] == 0
    assert dispatched["dispatcher_kind"] == "benchmark_local_ledger"
    assert dispatched["network_dispatch_performed"] is False

    evidence_path = Path(dispatched["evidence_record"])
    assert evidence_path.exists()
    evidence_text = evidence_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in evidence_text
    assert "prompt" not in evidence_text.lower()
    assert "transcript" not in evidence_text.lower()


def test_dispatch_refuses_missing_verification_record(tmp_path):
    run = _run("baseline", "post_commit_timeout_once")

    try:
        dispatch_command(
            run.run_id,
            evidence_root=tmp_path / "evidence",
        )
    except FileNotFoundError as exc:
        assert str(exc) == "verification_record_not_found"
    else:
        raise AssertionError("dispatch proceeded without verification")


def test_authority_fixture_unavailable_fails_closed_through_cli(tmp_path):
    run = _run("authority_aware", "authority_revoked_before_dispatch")
    workspace_root = tmp_path / "workspaces"
    evidence_root = tmp_path / "evidence"

    prepared = prepare_command(run.run_id, workspace_root)
    _apply_reference_fix(Path(prepared["candidate_path"]), run.function)
    verify_command(
        run.run_id,
        workspace_root=workspace_root,
        evidence_root=evidence_root,
    )

    dispatched = dispatch_command(
        run.run_id,
        evidence_root=evidence_root,
        authority_fixture_available=False,
    )

    assert dispatched["result_status"] == "failed_closed"
    assert dispatched["failure_class"] == "current_authority_unknown"
    assert dispatched["dispatcher_calls"] == 0
    assert dispatched["committed_side_effect_count"] == 0
