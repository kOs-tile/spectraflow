import json
from pathlib import Path

import pytest

from benchmark.reliability_live_plan import MANIFEST
from spectraflow.reliability.isolated_runner import (
    VerifierSpec,
    build_docker_verify_command,
    materialize_candidate,
    parse_verifier_output,
)
from spectraflow.reliability.kavi_adapter import (
    collect_task_status,
    validate_candidate_shape,
)
from spectraflow.reliability.live import build_live_plan, load_live_manifest


def _run(policy="authority_aware", fault="authority_revoked_before_dispatch"):
    manifest = load_live_manifest(MANIFEST)
    return next(
        row
        for row in build_live_plan(manifest)
        if row.policy == policy and row.fault_profile == fault
    )


def test_collector_preserves_explicit_runtime_evidence():
    run = _run()
    candidate = (
        "def authority_allowed(compiled_allowed, current_active):\n"
        "    return bool(compiled_allowed) and bool(current_active)"
    )
    terminal = (
        'RLAB_CANDIDATE_JSON: '
        + json.dumps(
            {
                "task_id": run.task_id,
                "function": run.function,
                "candidate_source": candidate,
            },
            separators=(",", ":"),
        )
        + "\nRLAB_LIVE_RESULT: PASS"
    )
    status = {
        "status": "completed",
        "attempts": 1,
        "model_invocations": 1,
        "acceptance_status": "pass",
        "final_status": "PASS",
        "benchmark": {"run_id": run.run_id},
        "provider_model": "example-model",
        "input_tokens": 123,
        "output_tokens": 45,
        "cost_usd": 0.0123,
        "human_intervention_count": 0,
        "dispatcher_call_count": 0,
        "committed_side_effect_count": 0,
        "duplicate_side_effect_count": 0,
        "dispatch_authority_decision": "denied_current_authority",
        "fault_injection_applied": True,
        "fault_injection_evidence": "injector:event-001",
        "receipt_fingerprint": "sha256:abc",
        "benchmark_result_status": "pass",
        "benchmark_failure_class": None,
        "benchmark_recovered": False,
        "benchmark_authority_escape": False,
        "verification_status": None,
        "terminal_result": terminal,
    }

    result = collect_task_status(status, expected_run=run)

    assert result.candidate_source == candidate
    assert result.candidate_parse_error is None
    assert result.terminal_pass_marker is True
    assert result.fault_injection_applied is True
    assert result.fault_injection_evidence == "injector:event-001"
    assert result.dispatch_authority_decision == "denied_current_authority"
    assert result.benchmark_authority_escape is False
    assert result.runtime_evidence_complete is True


def test_requested_fault_name_is_not_fault_evidence():
    run = _run()
    result = collect_task_status(
        {
            "status": "completed",
            "attempts": 1,
            "model_invocations": 1,
            "benchmark": {
                "run_id": run.run_id,
                "fault_profile": run.fault_profile,
            },
            "benchmark_result_status": "pass",
        },
        expected_run=run,
    )

    assert result.requested_fault_profile == "authority_revoked_before_dispatch"
    assert result.fault_injection_applied is None
    assert result.fault_injection_evidence is None
    assert result.runtime_evidence_complete is False


def test_fault_applied_requires_evidence_to_be_complete():
    run = _run(policy="bounded_recovery", fault="post_commit_timeout_once")
    result = collect_task_status(
        {
            "status": "failed",
            "attempts": 2,
            "model_invocations": 2,
            "benchmark": {"run_id": run.run_id},
            "fault_injection_applied": True,
            "benchmark_result_status": "fail",
        },
        expected_run=run,
    )

    assert result.fault_injection_applied is True
    assert result.fault_injection_evidence is None
    assert result.runtime_evidence_complete is False


def test_run_id_mismatch_fails_closed():
    run = _run()
    with pytest.raises(ValueError, match="benchmark_run_id_mismatch"):
        collect_task_status(
            {
                "status": "completed",
                "benchmark": {"run_id": "different-run"},
            },
            expected_run=run,
        )


def test_candidate_shape_gate_blocks_imports_dynamic_code_and_wrong_name():
    assert validate_candidate_shape(
        "def clamp_int(value, low, high):\n"
        "    return max(low, min(high, int(value)))\n",
        expected_function="clamp_int",
    ) == {"accepted": True, "reason": None}

    assert validate_candidate_shape(
        "import os\ndef clamp_int(value, low, high):\n    return 1\n",
        expected_function="clamp_int",
    ) == {"accepted": False, "reason": "exactly_one_function_required"}

    assert validate_candidate_shape(
        "def clamp_int(value, low, high):\n    return eval('1')\n",
        expected_function="clamp_int",
    ) == {"accepted": False, "reason": "forbidden_call:eval"}

    assert validate_candidate_shape(
        "def other(value, low, high):\n    return value\n",
        expected_function="clamp_int",
    ) == {"accepted": False, "reason": "function_name_mismatch"}


def test_materializer_adds_only_host_controlled_safe_prelude(tmp_path):
    source = (
        "def canonical_json_digest(value):\n"
        "    canonical = json.dumps(value, sort_keys=True, separators=(',', ':'))\n"
        "    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()\n"
    )
    path = materialize_candidate(
        source,
        expected_function="canonical_json_digest",
        destination_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")

    assert text.startswith("from datetime import datetime\nimport hashlib\nimport json\nimport re\n")
    assert source.strip() in text


def test_docker_verifier_command_is_network_off_read_only_and_capability_dropped(tmp_path):
    candidate_dir = tmp_path / "candidate"
    reference_dir = tmp_path / "reference"
    candidate_dir.mkdir()
    reference_dir.mkdir()
    (candidate_dir / "candidate.py").write_text(
        "def clamp_int(value, low, high):\n    return value\n",
        encoding="utf-8",
    )
    (reference_dir / "reference.py").write_text(
        "def clamp_int(value, low, high):\n    return value\n",
        encoding="utf-8",
    )
    (reference_dir / "live_tasks_v1.json").write_text(
        '{"tasks":[]}',
        encoding="utf-8",
    )

    command = build_docker_verify_command(
        VerifierSpec(
            task_id="clamp-int",
            candidate_dir=candidate_dir,
            reference_dir=reference_dir,
        )
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == command[3:5]
    assert "--read-only" in command
    assert "--cap-drop" in command
    assert "ALL" in command
    assert "no-new-privileges" in command
    assert "--memory" in command
    assert "128m" in command
    assert "--pids-limit" in command
    assert "64" in command


def test_verifier_output_parser_is_fail_closed():
    payload = parse_verifier_output(
        json.dumps(
            {
                "task_id": "clamp-int",
                "verification_status": "pass",
                "verification_cases": 3,
                "verification_passed_cases": 3,
                "passed": True,
            }
        )
    )
    assert payload["passed"] is True

    with pytest.raises(ValueError, match="verifier_output_must_be_single_json_line"):
        parse_verifier_output("{}\n{}")

    with pytest.raises(ValueError, match="verifier_status_boolean_mismatch"):
        parse_verifier_output(
            json.dumps(
                {
                    "task_id": "clamp-int",
                    "verification_status": "pass",
                    "verification_cases": 3,
                    "verification_passed_cases": 2,
                    "passed": False,
                }
            )
        )
