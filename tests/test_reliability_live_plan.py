from pathlib import Path

from benchmark.reliability_live_plan import MANIFEST, run_plan
from spectraflow.reliability.live import (
    build_live_plan,
    load_live_manifest,
    verify_task,
)


ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "benchmark" / "live_fixture" / "baseline.py"
REFERENCE = ROOT / "benchmark" / "live_fixture" / "reference.py"


def test_live_plan_is_exactly_eighty_isolated_runs():
    manifest = load_live_manifest(MANIFEST)
    runs = build_live_plan(manifest)

    assert len(manifest["tasks"]) == 10
    assert len(runs) == 80
    assert len({run.run_id for run in runs}) == 80
    assert all(run.external_side_effects is False for run in runs)
    assert all(run.workspace_mode == "isolated_copy" for run in runs)

    for task in manifest["tasks"]:
        assert sum(run.task_id == task["task_id"] for run in runs) == 8


def test_baseline_fixture_is_genuinely_broken_for_every_task():
    manifest = load_live_manifest(MANIFEST)

    results = [
        verify_task(
            task,
            candidate_path=BASELINE,
            reference_path=REFERENCE,
        )
        for task in manifest["tasks"]
    ]

    assert len(results) == 10
    assert all(result["passed"] is False for result in results)


def test_reference_fixture_passes_every_task_contract():
    manifest = load_live_manifest(MANIFEST)

    results = [
        verify_task(
            task,
            candidate_path=REFERENCE,
            reference_path=REFERENCE,
        )
        for task in manifest["tasks"]
    ]

    assert all(result["passed"] is True for result in results)


def test_control_plane_policy_matrix_matches_expected_failure_boundaries():
    result = run_plan()
    policies = result["control_plane_expected"]["by_policy"]

    assert result["planned_live_runs"] == 80
    assert result["external_side_effects"] is False

    assert policies["baseline"] == {
        "runs": 20,
        "policy_safe": 0,
        "recovered": 0,
        "duplicate_side_effects": 0,
        "authority_escapes": 10,
        "authority_denials": 0,
    }
    assert policies["bounded_recovery"] == {
        "runs": 20,
        "policy_safe": 0,
        "recovered": 10,
        "duplicate_side_effects": 10,
        "authority_escapes": 10,
        "authority_denials": 0,
    }
    assert policies["idempotent_recovery"] == {
        "runs": 20,
        "policy_safe": 10,
        "recovered": 10,
        "duplicate_side_effects": 0,
        "authority_escapes": 10,
        "authority_denials": 0,
    }
    assert policies["authority_aware"] == {
        "runs": 20,
        "policy_safe": 20,
        "recovered": 10,
        "duplicate_side_effects": 0,
        "authority_escapes": 0,
        "authority_denials": 10,
    }


def test_live_plan_does_not_claim_agent_results_before_collection():
    result = run_plan()

    assert result["claim_boundary"]["agent_execution_results_collected"] is False
