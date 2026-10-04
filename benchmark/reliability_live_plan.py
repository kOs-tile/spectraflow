"""Render Reliability Lab v1 80-run live plan and control-plane matrix."""

from __future__ import annotations

import json
from pathlib import Path

from spectraflow.reliability.live import (
    build_live_plan,
    load_live_manifest,
    summarize_control_plane,
)


MANIFEST = Path(__file__).parent / "live_tasks_v1.json"


def run_plan():
    manifest = load_live_manifest(MANIFEST)
    runs = build_live_plan(manifest)
    summary = summarize_control_plane(runs)

    return {
        "suite": manifest["suite"],
        "task_count": len(manifest["tasks"]),
        "fault_profiles": 2,
        "policy_arms": 4,
        "planned_live_runs": len(runs),
        "external_side_effects": manifest["workspace_contract"][
            "external_side_effects"
        ],
        "control_plane_expected": summary,
        "claim_boundary": {
            "agent_execution_results_collected": False,
            "note": (
                "The 80-run plan is executable, but live KAVI/Hermes model, token, "
                "cost, latency, and intervention results must be collected by the "
                "agent adapter before comparative production claims are made."
            ),
        },
    }


if __name__ == "__main__":
    print(json.dumps(run_plan(), indent=2))
