import json

from spectraflow.reliability.hook_attestation import (
    ATTESTATION_SCHEMA,
    inspect_runner_source,
    validate_hook_attestation,
)


def test_two_hook_runner_produces_sanitized_attestation(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        "from spectraflow.reliability.kavi_runtime_shim import (\n"
        "    prepare_before_agent, finalize_after_agent,\n"
        ")\n"
        "def cycle(task):\n"
        "    prepared = prepare_before_agent(task, workspace_root='x')\n"
        "    return finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n",
        encoding="utf-8",
    )

    result = inspect_runner_source(runner)

    assert result["schema"] == ATTESTATION_SCHEMA
    assert result["attested"] is True
    assert result["local_path_exported"] is False
    assert len(result["source_sha256"]) == 64
    assert str(runner) not in json.dumps(result)
    assert validate_hook_attestation(result) is True


def test_missing_finalize_hook_fails_attestation(tmp_path):
    runner = tmp_path / "execution_control.py"
    runner.write_text(
        "from spectraflow.reliability.kavi_runtime_shim import prepare_before_agent\n"
        "def cycle(task):\n"
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
        "from spectraflow.reliability.kavi_runtime_shim import prepare_before_agent, finalize_after_agent\n"
        "def cycle(task):\n"
        "    prepare_before_agent(task, workspace_root='x')\n"
        "    finalize_after_agent(task, workspace_root='x', evidence_root='y', runtime_telemetry={})\n",
        encoding="utf-8",
    )
    result = inspect_runner_source(runner)
    assert validate_hook_attestation(result) is True

    result["local_path_exported"] = True
    assert validate_hook_attestation(result) is False
