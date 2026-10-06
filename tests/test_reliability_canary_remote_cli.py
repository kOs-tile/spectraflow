import json

import pytest

from benchmark.reliability_canary_remote import validate_execute_precondition
from spectraflow.reliability.hook_attestation import inspect_runner_source


RUNNER = (
    "from spectraflow.reliability.kavi_runtime_shim import (\n"
    "    prepare_before_agent, finalize_after_agent,\n"
    ")\n"
    "def execute_once(task):\n"
    "    prepared = prepare_before_agent(task, workspace_root='x')\n"
    "    process = object()\n"
    "    return finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n"
)


def _files(tmp_path):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    attestation_path = tmp_path / "attestation.json"

    source.write_text(RUNNER, encoding="utf-8")
    deployed.write_text(RUNNER, encoding="utf-8")
    attestation = inspect_runner_source(source)
    attestation_path.write_text(
        json.dumps(attestation),
        encoding="utf-8",
    )
    return source, deployed, attestation_path


def test_bound_source_and_runtime_allow_execute_precondition(tmp_path):
    source, deployed, attestation_path = _files(tmp_path)

    binding = validate_execute_precondition(
        attestation_path=attestation_path,
        deployed_runner_path=deployed,
        source_runner_path=source,
    )

    assert binding["bound"] is True
    assert binding["deployed_matches_attestation"] is True
    assert binding["source_matches_deployed"] is True
    assert binding["local_path_exported"] is False

    encoded = json.dumps(binding)
    assert str(source) not in encoded
    assert str(deployed) not in encoded


def test_deployed_drift_blocks_execute_precondition(tmp_path):
    source, deployed, attestation_path = _files(tmp_path)
    deployed.write_text(RUNNER + "\n# drift\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="runtime_hook_bound_attestation_required",
    ):
        validate_execute_precondition(
            attestation_path=attestation_path,
            deployed_runner_path=deployed,
            source_runner_path=source,
        )


def test_source_drift_blocks_execute_precondition(tmp_path):
    source, deployed, attestation_path = _files(tmp_path)
    source.write_text(RUNNER + "\n# source drift\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="runtime_hook_bound_attestation_required",
    ):
        validate_execute_precondition(
            attestation_path=attestation_path,
            deployed_runner_path=deployed,
            source_runner_path=source,
        )


def test_tampered_attestation_blocks_execute_precondition(tmp_path):
    source, deployed, attestation_path = _files(tmp_path)
    payload = json.loads(attestation_path.read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    attestation_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="runtime_hook_bound_attestation_required",
    ):
        validate_execute_precondition(
            attestation_path=attestation_path,
            deployed_runner_path=deployed,
            source_runner_path=source,
        )
