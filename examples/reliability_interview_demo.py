"""Credential-free KAVI agent-reliability engineering evidence demo.

Uses existing SPECTRAFLOW benchmarks plus a separate, synthetic SQLite model.
Run from the SPECTRAFLOW checkout: python examples/reliability_interview_demo.py
No Hermes production access or external model/API calls.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

MAX_ATTEMPTS = 3  # initial attempt + at most two retries


class Rejected(RuntimeError):
    """Preflight veto: never invoke a model or commit a side effect."""


class DemoQueue:
    """Interview-only durable reference model, NOT the real KAVI/Hermes runner."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY,
              owner TEXT NOT NULL CHECK(owner IN ('work','codex')),
              idem_key TEXT NOT NULL UNIQUE,
              parent_id TEXT REFERENCES tasks(task_id),
              status TEXT NOT NULL CHECK(status IN ('ready','failed','pass')),
              attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
              model_invocations INTEGER NOT NULL DEFAULT 0,
              acceptance TEXT,
              final_marker TEXT
            );
            CREATE TRIGGER IF NOT EXISTS protect_terminal_update
              BEFORE UPDATE ON tasks WHEN OLD.status IN ('failed','pass')
              BEGIN SELECT RAISE(ABORT,'terminal task is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS protect_terminal_delete
              BEFORE DELETE ON tasks WHEN OLD.status IN ('failed','pass')
              BEGIN SELECT RAISE(ABORT,'terminal task is immutable'); END;
            CREATE TABLE IF NOT EXISTS events (
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id TEXT NOT NULL REFERENCES tasks(task_id),
              event TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS protect_event_update BEFORE UPDATE ON events
              BEGIN SELECT RAISE(ABORT,'history immutable'); END;
            CREATE TRIGGER IF NOT EXISTS protect_event_delete BEFORE DELETE ON events
              BEGIN SELECT RAISE(ABORT,'history immutable'); END;
        """)

    def close(self):
        self.db.close()

    def get(self, task_id):
        item = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if item is None:
            raise Rejected("unknown_task")
        return dict(item)

    def events(self, task_id):
        return [r[0] for r in self.db.execute(
            "SELECT event FROM events WHERE task_id=? ORDER BY event_id", (task_id,)
        )]

    def create(self, task_id, owner, *, idem_key=None, parent_id=None):
        if not task_id or idem_key not in (None, task_id):
            raise Rejected("identity_mismatch")
        if parent_id is not None:
            parent = self.get(parent_id)
            if parent_id == task_id or parent["status"] != "failed":
                raise Rejected("continuation_requires_failed_parent_and_fresh_id")
        self.db.execute(
            "INSERT INTO tasks(task_id,owner,idem_key,parent_id,status) VALUES(?,?,?,?,?)",
            (task_id, owner, task_id, parent_id, "ready")
        )
        self.db.execute("INSERT INTO events(task_id,event) VALUES(?,?)", (task_id, "created"))

    def _preflight(self, task_id, actor):
        task = self.get(task_id)
        if actor != task["owner"]:
            raise Rejected("actor_not_owner")
        if task["idem_key"] != task_id:
            raise Rejected("identity_mismatch")
        if task["status"] != "ready":
            raise Rejected("terminal_or_unavailable")
        if task["attempts"] >= MAX_ATTEMPTS:
            raise Rejected("retry_budget_exhausted")
        return task

    def premodel_fault(self, task_id, actor):
        self._preflight(task_id, actor)
        with self.db:
            self.db.execute("UPDATE tasks SET attempts=attempts+1 WHERE task_id=?", (task_id,))
            if self.get(task_id)["attempts"] >= MAX_ATTEMPTS:
                self.db.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (task_id,))
            self.db.execute("INSERT INTO events(task_id,event) VALUES(?,?)",
                            (task_id, "preflight_failure"))
        return self.get(task_id)

    def finish(self, task_id, actor, *, exit_code, acceptance, marker):
        self._preflight(task_id, actor)
        with self.db:
            self.db.execute(
                "UPDATE tasks SET attempts=attempts+1, model_invocations=model_invocations+1 "
                "WHERE task_id=?", (task_id,)
            )
            verified = exit_code == 0 and acceptance == "pass" and marker == "PASS"
            status = "pass" if verified else "failed"
            self.db.execute(
                "UPDATE tasks SET status=?, acceptance=?, final_marker=? WHERE task_id=?",
                (status, acceptance, marker, task_id)
            )
            self.db.execute("INSERT INTO events(task_id,event) VALUES(?,?)",
                            (task_id, "verified_pass" if verified else "semantic_rejection"))
        return self.get(task_id)


def control_plane_demonstration():
    """Twelve deterministic assertions against temporary, persisted SQLite state."""
    with tempfile.TemporaryDirectory(prefix="kavi-reliability-demo-") as directory:
        path = Path(directory) / "tasks.sqlite3"
        queue = DemoQueue(path)
        checks = []
        try:
            queue.create("job-work", "work")
            try:
                queue.finish("job-work", "codex", exit_code=0, acceptance="pass", marker="PASS")
            except Rejected as exc:
                assert str(exc) == "actor_not_owner"
                checks.append("wrong_actor_denied_before_model")
            else:
                raise AssertionError("wrong actor was accepted")
            assert queue.get("job-work")["model_invocations"] == 0
            item = queue.finish("job-work", "work", exit_code=0,
                                acceptance="pass", marker="PASS")
            assert item["status"] == "pass" and item["model_invocations"] == 1
            checks.append("correct_actor_verified_pass")
            try:
                queue.finish("job-work", "work", exit_code=0, acceptance="pass", marker="PASS")
            except Rejected as exc:
                assert str(exc) == "terminal_or_unavailable"
                checks.append("terminal_task_cannot_reexecute")
            else:
                raise AssertionError("terminal replay allowed")

            queue.create("job-failed", "codex")
            failed = queue.finish("job-failed", "codex", exit_code=0,
                                  acceptance=None, marker=None)
            assert failed["status"] == "failed" and failed["model_invocations"] == 1
            checks.append("exit_zero_without_acceptance_is_not_pass")
            history = queue.events("job-failed")
            try:
                queue.db.execute("UPDATE tasks SET status='pass' WHERE task_id='job-failed'")
            except sqlite3.DatabaseError:
                checks.append("failed_history_cannot_be_rewritten")
            else:
                raise AssertionError("immutable task rewritten")
            assert queue.events("job-failed") == history
            try:
                queue.db.execute("DELETE FROM events WHERE task_id='job-failed'")
            except sqlite3.DatabaseError:
                checks.append("audit_events_cannot_be_deleted")
            else:
                raise AssertionError("audit history deleted")

            queue.create("job-failed-r1", "codex", parent_id="job-failed")
            assert queue.get("job-failed-r1")["idem_key"] == "job-failed-r1"
            assert queue.get("job-failed-r1")["parent_id"] == "job-failed"
            checks.append("fresh_continuation_and_identity")
            assert queue.finish("job-failed-r1", "codex", exit_code=0,
                                acceptance="pass", marker="PASS")["status"] == "pass"
            assert queue.get("job-failed")["status"] == "failed"
            checks.append("continuation_does_not_overwrite_failure")

            queue.create("job-budget", "codex")
            for index in range(MAX_ATTEMPTS):
                state = queue.premodel_fault("job-budget", "codex")
                assert state["attempts"] == index + 1
                assert state["model_invocations"] == 0
            assert state["status"] == "failed"
            checks.append("bounded_premodel_retries_without_model_calls")
            try:
                queue.premodel_fault("job-budget", "codex")
            except Rejected as exc:
                assert str(exc) == "terminal_or_unavailable"
                checks.append("retry_budget_stops_execution")
            else:
                raise AssertionError("retry budget exceeded")
            try:
                queue.create("bad-continuation", "codex", parent_id="job-work")
            except Rejected as exc:
                assert str(exc) == "continuation_requires_failed_parent_and_fresh_id"
                checks.append("no_continuation_from_success")
            else:
                raise AssertionError("invalid continuation accepted")

            queue.close()
            reopened = DemoQueue(path)
            try:
                assert reopened.get("job-failed")["status"] == "failed"
                assert reopened.get("job-failed-r1")["status"] == "pass"
                assert reopened.get("job-budget")["attempts"] == MAX_ATTEMPTS
                assert reopened.events("job-failed") == history
                checks.append("durable_state_survives_restart")
            finally:
                reopened.close()
        finally:
            try:
                queue.close()
            except Exception:
                pass
        return {
            "status": "PASS", "checks": checks, "check_count": len(checks),
            "model_calls_in_rejected_preflight": 0,
            "max_attempts": MAX_ATTEMPTS, "production_access": False
        }


def run_demo(*, controls_only=False):
    controls = control_plane_demonstration()
    report = {
        "status": "PASS", "control_plane": controls,
        "source_kind": "isolated_sqlite_reference_model"
    }
    print("KAVI control-plane assertions:", controls["check_count"], "PASS")
    if not controls_only:
        # Reuse pre-existing SPECTRAFLOW evaluators, rather than inventing metrics.
        from benchmark.execution_traces import run_benchmark as synthetic_dispatch_benchmark
        from benchmark.kavi_runtime_traces import run_benchmark as runtime_benchmark
        side = synthetic_dispatch_benchmark()
        actual = runtime_benchmark()
        by_id = {r["task_id"]: r for r in side["results"]}
        blind = by_id["post_commit_timeout_naive"]
        safe = by_id["post_commit_timeout_idempotent"]
        assert blind["committed_side_effects"] == 2
        assert blind["duplicate_side_effects"] == 1 and blind["safe_completion"] is False
        assert safe["dispatcher_calls"] == 2 and safe["committed_side_effects"] == 1
        assert safe["duplicate_side_effects"] == 0 and safe["safe_completion"] is True
        assert side["cases"] == 6 and side["safe_completions"] == 3
        assert actual["cases"] == 8 and actual["verified_pass"] == 2
        assert actual["completed_unverified"] == 2 and actual["explicit_failures"] == 4
        report["synthetic_dispatch"] = {
            "cases": side["cases"], "safe_completions": side["safe_completions"],
            "blind_retry_commits": blind["committed_side_effects"],
            "idempotent_retry_commits": safe["committed_side_effects"],
            "blind_retry_safe": blind["safe_completion"],
            "idempotent_retry_safe": safe["safe_completion"]
        }
        report["historical_metadata"] = {
            "cases": actual["cases"], "verified_pass": actual["verified_pass"],
            "completed_unverified": actual["completed_unverified"],
            "explicit_failures": actual["explicit_failures"],
            "source_revision": actual["source"]["source_revision"],
            "side_effect_counts_available": False
        }
        print("Synthetic post-commit retry: blind 2 commits / idempotent 1 commit")
        print("Historical runtime records: 2 verified / 2 unverified / 4 failed")
    print("KAVI_RELIABILITY_DEMO: PASS")
    print("EVIDENCE_JSON=" + json.dumps(report, sort_keys=True))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls-only", action="store_true")
    args = parser.parse_args()
    run_demo(controls_only=args.controls_only)
