from pathlib import Path

from spectraflow.reliability.readiness import (
    evaluate_canary_readiness,
    evaluate_repository_contract,
)


ROOT = Path(__file__).parents[1]


def test_repository_contract_is_complete():
    ready, missing = evaluate_repository_contract(ROOT)
    assert ready is True
    assert missing == []


def test_default_readiness_fails_closed():
    result = evaluate_canary_readiness(
        repository_contract_ready=True,
        bridge_health=None,
        runtime_hook_attested=False,
    )

    assert result.canary_dispatch_ready is False
    assert result.attestation_is_benchmark_evidence is False
    assert result.blockers == (
        "bridge_health_not_verified",
        "local_runtime_hook_not_attested",
    )


def test_old_bridge_version_blocks_canary():
    result = evaluate_canary_readiness(
        repository_contract_ready=True,
        bridge_health={
            "ok": True,
            "configured": True,
            "version": "0.2.1",
        },
        runtime_hook_attested=True,
    )

    assert result.bridge_health_verified is True
    assert result.bridge_version_ready is False
    assert result.canary_dispatch_ready is False
    assert result.blockers == ("bridge_version_below_0.2.2",)


def test_ready_requires_all_three_preconditions():
    result = evaluate_canary_readiness(
        repository_contract_ready=True,
        bridge_health={
            "ok": True,
            "configured": True,
            "version": "0.2.2",
        },
        runtime_hook_attested=True,
    )

    assert result.repository_contract_ready is True
    assert result.bridge_health_verified is True
    assert result.bridge_version_ready is True
    assert result.runtime_hook_attested is True
    assert result.canary_dispatch_ready is True
    assert result.blockers == ()
    assert result.attestation_is_benchmark_evidence is False


def test_unconfigured_bridge_is_not_health_verified():
    result = evaluate_canary_readiness(
        repository_contract_ready=True,
        bridge_health={
            "ok": True,
            "configured": False,
            "version": "0.2.2",
        },
        runtime_hook_attested=True,
    )

    assert result.bridge_health_verified is False
    assert result.canary_dispatch_ready is False
    assert "bridge_health_not_verified" in result.blockers
