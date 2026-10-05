import json
import sys

from benchmark import reliability_canary_readiness
from spectraflow.reliability.hook_attestation import inspect_runner_source


def _runner_text():
    return (
        "from spectraflow.reliability.kavi_runtime_shim import "
        "prepare_before_agent, finalize_after_agent\n"
        "def execute_once(task):\n"
        "    prepared = prepare_before_agent(task, workspace_root='x')\n"
        "    return finalize_after_agent(task, workspace_root='x', "
        "evidence_root='y', runtime_telemetry={})\n"
    )


def _bridge_health(path):
    path.write_text(
        json.dumps({"ok": True, "configured": True, "version": "0.2.2"}),
        encoding="utf-8",
    )


def test_readiness_cli_rejects_unbound_attestation(tmp_path, monkeypatch, capsys):
    runner = tmp_path / "execution_control.py"
    runner.write_text(_runner_text(), encoding="utf-8")
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(
        json.dumps(inspect_runner_source(runner)),
        encoding="utf-8",
    )
    health = tmp_path / "health.json"
    _bridge_health(health)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reliability_canary_readiness",
            "--bridge-health-json",
            str(health),
            "--runtime-hook-attestation-json",
            str(attestation_path),
        ],
    )
    reliability_canary_readiness.main()
    result = json.loads(capsys.readouterr().out)

    assert result["runtime_hook_attestation_checked"] is True
    assert result["runtime_hook_binding_checked"] is False
    assert result["runtime_hook_attested"] is False
    assert result["canary_dispatch_ready"] is False
    assert "local_runtime_hook_not_attested" in result["blockers"]


def test_readiness_cli_accepts_bound_source_and_runtime(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    source.write_text(_runner_text(), encoding="utf-8")
    deployed.write_text(_runner_text(), encoding="utf-8")

    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(
        json.dumps(inspect_runner_source(source)),
        encoding="utf-8",
    )
    health = tmp_path / "health.json"
    _bridge_health(health)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reliability_canary_readiness",
            "--bridge-health-json",
            str(health),
            "--runtime-hook-attestation-json",
            str(attestation_path),
            "--deployed-runner-file",
            str(deployed),
            "--source-runner-file",
            str(source),
        ],
    )
    reliability_canary_readiness.main()
    result = json.loads(capsys.readouterr().out)

    assert result["runtime_hook_binding_checked"] is True
    assert result["runtime_hook_binding"]["bound"] is True
    assert result["runtime_hook_attested"] is True
    assert result["canary_dispatch_ready"] is True


def test_readiness_cli_rejects_runtime_drift(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    source.write_text(_runner_text(), encoding="utf-8")
    deployed.write_text(_runner_text(), encoding="utf-8")

    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(
        json.dumps(inspect_runner_source(source)),
        encoding="utf-8",
    )
    deployed.write_text(_runner_text() + "# drift\n", encoding="utf-8")

    health = tmp_path / "health.json"
    _bridge_health(health)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reliability_canary_readiness",
            "--bridge-health-json",
            str(health),
            "--runtime-hook-attestation-json",
            str(attestation_path),
            "--deployed-runner-file",
            str(deployed),
            "--source-runner-file",
            str(source),
        ],
    )
    reliability_canary_readiness.main()
    result = json.loads(capsys.readouterr().out)

    assert result["runtime_hook_binding"]["bound"] is False
    assert result["runtime_hook_attested"] is False
    assert result["canary_dispatch_ready"] is False
