"""Regression tests for the fully offline, read-only reliability hiring demonstration."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
from reliability_interview_demo import DemoQueue, control_plane_demonstration


def test_control_plane_assertions():
    result = control_plane_demonstration()
    assert result["status"] == "PASS"
    assert result["check_count"] == 12
    assert result["model_calls_in_rejected_preflight"] == 0
    assert result["production_access"] is False


def test_end_to_end_uses_existing_spectraflow_evidence():
    proc = subprocess.run(
        [sys.executable, "examples/reliability_interview_demo.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "KAVI_RELIABILITY_DEMO: PASS" in proc.stdout
    line = next(s for s in proc.stdout.splitlines() if s.startswith("EVIDENCE_JSON="))
    result = json.loads(line.split("=", 1)[1])
    assert result["source_kind"] == "isolated_sqlite_reference_model"
    assert result["control_plane"]["check_count"] == 12
    assert result["synthetic_dispatch"]["blind_retry_commits"] == 2
    assert result["synthetic_dispatch"]["idempotent_retry_commits"] == 1
    assert result["synthetic_dispatch"]["blind_retry_safe"] is False
    assert result["historical_metadata"]["cases"] == 8
    assert result["historical_metadata"]["verified_pass"] == 2
    assert result["historical_metadata"]["completed_unverified"] == 2
    assert result["historical_metadata"]["side_effect_counts_available"] is False


def test_duplicate_idempotency_identity_cannot_be_inserted(tmp_path):
    queue = DemoQueue(tmp_path / "queue.sqlite")
    try:
        queue.create("same-id", "codex")
        import sqlite3
        try:
            queue.create("same-id", "codex")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("duplicate identity was accepted")
        assert queue.get("same-id")["attempts"] == 0
    finally:
        queue.close()


def test_fresh_continuation_does_not_mutate_failed_task(tmp_path):
    queue = DemoQueue(tmp_path / "queue.sqlite")
    try:
        queue.create("original", "work")
        failed = queue.finish("original", "work", exit_code=0, acceptance=None, marker=None)
        assert failed["status"] == "failed"
        queue.create("continuation", "work", parent_id="original")
        queue.finish("continuation", "work", exit_code=0, acceptance="pass", marker="PASS")
        assert queue.get("original") == failed
        assert queue.get("continuation")["status"] == "pass"
    finally:
        queue.close()
