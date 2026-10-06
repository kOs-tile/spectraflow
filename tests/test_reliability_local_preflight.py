import json

from benchmark.reliability_local_preflight import build_local_preflight


def _runner_text():
    return (
        "from spectraflow.reliability.kavi_runtime_shim import "
        "prepare_before_agent, finalize_after_agent\n"
        "def execute_once(task):\n"
        "    prepared = prepare_before_agent(task, workspace_root='x')\n"
        "    return finalize_after_agent(task, workspace_root='x', "
        "evidence_root='y', runtime_telemetry={})\n"
    )


def _health(version="0.2.2"):
    return {"ok": True, "configured": True, "version": version}


def test_local_preflight_is_ready_only_when_all_bindings_match(tmp_path):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    source.write_text(_runner_text(), encoding="utf-8")
    deployed.write_text(_runner_text(), encoding="utf-8")

    result = build_local_preflight(
        source_runner_file=source,
        deployed_runner_file=deployed,
        bridge_health=_health(),
    )

    assert result["runtime_binding"]["bound"] is True
    assert result["readiness"]["canary_dispatch_ready"] is True
    assert result["readiness"]["blockers"] == []
    assert result["canary_plan"]["runs"] == 8
    assert result["network_calls_performed"] is False
    assert result["canary_execution_performed"] is False
    encoded = json.dumps(result)
    assert str(source) not in encoded
    assert str(deployed) not in encoded


def test_local_preflight_fails_closed_on_deployed_drift(tmp_path):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    source.write_text(_runner_text(), encoding="utf-8")
    deployed.write_text(_runner_text() + "# drift\n", encoding="utf-8")

    result = build_local_preflight(
        source_runner_file=source,
        deployed_runner_file=deployed,
        bridge_health=_health(),
    )

    assert result["runtime_binding"]["bound"] is False
    assert result["readiness"]["canary_dispatch_ready"] is False
    assert "local_runtime_hook_not_attested" in result["readiness"]["blockers"]


def test_local_preflight_fails_closed_on_old_bridge(tmp_path):
    source = tmp_path / "source_execution_control.py"
    deployed = tmp_path / "deployed_execution_control.py"
    source.write_text(_runner_text(), encoding="utf-8")
    deployed.write_text(_runner_text(), encoding="utf-8")

    result = build_local_preflight(
        source_runner_file=source,
        deployed_runner_file=deployed,
        bridge_health=_health("0.2.1"),
    )

    assert result["runtime_binding"]["bound"] is True
    assert result["readiness"]["canary_dispatch_ready"] is False
    assert "bridge_version_below_0.2.2" in result["readiness"]["blockers"]
