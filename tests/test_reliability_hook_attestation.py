import json

from spectraflow.reliability.hook_attestation import (
    ATTESTATION_SCHEMA,
    inspect_runner_source,
    validate_hook_attestation,
    validate_hook_attestation_binding,
)


def _header():
    return (
        "from spectraflow.reliability.kavi_runtime_shim import (\n"
        "    prepare_before_agent, finalize_after_agent,\n"
        ")\n"
    )


def test_two_hook_execute_once_produces_sanitized_attestation(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        _header()
        + "def execute_once(task):\n"
        + "    prepared = prepare_before_agent(task, workspace_root='x')\n"
        + "    process = object()\n"
        + "    return finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n",
        encoding="utf-8",
    )

    result = inspect_runner_source(runner)

    assert result["schema"] == ATTESTATION_SCHEMA
    assert result["attested"] is True
    assert result["execution_function"] == "execute_once"
    assert result["execution_function_found"] is True
    assert result["prepare_before_finalize"] is True
    assert result["hooks"]["prepare_before_agent"]["called_in_execution_function"] is True
    assert result["hooks"]["finalize_after_agent"]["called_in_execution_function"] is True
    assert result["local_path_exported"] is False
    assert len(result["source_sha256"]) == 64
    assert str(runner) not in json.dumps(result)
    assert validate_hook_attestation(result) is True


def test_hooks_called_only_outside_execute_once_fail_attestation(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        _header()
        + "def helper(task):\n"
        + "    prepare_before_agent(task, workspace_root='x')\n"
        + "    finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n"
        + "def execute_once(task):\n"
        + "    return task\n",
        encoding="utf-8",
    )

    result = inspect_runner_source(runner)

    assert result["attested"] is False
    assert result["reason"] == "required_two_hook_structure_not_found"
    assert result["hooks"]["prepare_before_agent"]["called"] is True
    assert result["hooks"]["prepare_before_agent"]["called_in_execution_function"] is False
    assert validate_hook_attestation(result) is False


def test_missing_execute_once_fails_attestation(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        _header()
        + "def cycle(task):\n"
        + "    prepare_before_agent(task, workspace_root='x')\n"
        + "    return finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n",
        encoding="utf-8",
    )

    result = inspect_runner_source(runner)

    assert result["attested"] is False
    assert result["reason"] == "execute_once_not_found"
    assert validate_hook_attestation(result) is False


def test_finalize_before_prepare_fails_order_gate(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        _header()
        + "def execute_once(task):\n"
        + "    finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n"
        + "    return prepare_before_agent(task, workspace_root='x')\n",
        encoding="utf-8",
    )

    result = inspect_runner_source(runner)

    assert result["attested"] is False
    assert result["reason"] == "hook_order_invalid"
    assert result["prepare_before_finalize"] is False
    assert validate_hook_attestation(result) is False


def test_missing_finalize_hook_fails_attestation(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        "from spectraflow.reliability.kavi_runtime_shim import prepare_before_agent\n"
        "def execute_once(task):\n"
        "    return prepare_before_agent(task, workspace_root='x')\n",
        encoding="utf-8",
    )

    result = inspect_runner_source(runner)

    assert result["attested"] is False
    assert result["reason"] == "required_two_hook_structure_not_found"
    assert validate_hook_attestation(result) is False


def test_invalid_or_tampered_attestation_is_rejected(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        _header()
        + "def execute_once(task):\n"
        + "    prepare_before_agent(task, workspace_root='x')\n"
        + "    finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n",
        encoding="utf-8",
    )
    result = inspect_runner_source(runner)
    assert validate_hook_attestation(result) is True

    result["local_path_exported"] = True
    assert validate_hook_attestation(result) is False



def _valid_runner_source():
    return (
        _header()
        + "def execute_once(task):\n"
        + "    prepared = prepare_before_agent(task, workspace_root='x')\n"
        + "    return finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n"
    )


def test_attestation_binding_matches_current_deployed_and_source_hashes(tmp_path):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    text = _valid_runner_source()
    source.write_text(text, encoding="utf-8")
    deployed.write_text(text, encoding="utf-8")

    attestation = inspect_runner_source(source)
    binding = validate_hook_attestation_binding(
        attestation,
        deployed_runner_path=deployed,
        source_runner_path=source,
    )

    assert binding["bound"] is True
    assert binding["attestation_valid"] is True
    assert binding["deployed_matches_attestation"] is True
    assert binding["source_matches_deployed"] is True
    assert binding["source_matches_attestation"] is True
    encoded = json.dumps(binding)
    assert str(source) not in encoded
    assert str(deployed) not in encoded
    assert binding["local_path_exported"] is False


def test_deployed_runtime_drift_invalidates_existing_attestation(tmp_path):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    text = _valid_runner_source()
    source.write_text(text, encoding="utf-8")
    deployed.write_text(text, encoding="utf-8")

    attestation = inspect_runner_source(source)
    deployed.write_text(text + "\n# runtime drift\n", encoding="utf-8")

    binding = validate_hook_attestation_binding(
        attestation,
        deployed_runner_path=deployed,
        source_runner_path=source,
    )

    assert binding["bound"] is False
    assert binding["deployed_matches_attestation"] is False
    assert binding["source_matches_deployed"] is False
    assert binding["source_matches_attestation"] is True


def test_source_runtime_mismatch_fails_closed(tmp_path):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    text = _valid_runner_source()
    source.write_text(text, encoding="utf-8")
    deployed.write_text(text.replace("prepared =", "prepared_runtime ="), encoding="utf-8")

    attestation = inspect_runner_source(source)
    binding = validate_hook_attestation_binding(
        attestation,
        deployed_runner_path=deployed,
        source_runner_path=source,
    )

    assert binding["bound"] is False
    assert binding["source_matches_deployed"] is False


def test_structurally_invalid_attestation_cannot_become_bound(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        "def execute_once(task):\n    return task\n",
        encoding="utf-8",
    )
    attestation = inspect_runner_source(runner)

    binding = validate_hook_attestation_binding(
        attestation,
        deployed_runner_path=runner,
        source_runner_path=runner,
    )

    assert binding["attestation_valid"] is False
    assert binding["bound"] is False
