"""Container-side canonical verifier for one Reliability Lab candidate.

The container receives:
- /workspace/candidate.py (model-produced function only)
- /reference/reference.py
- /reference/live_tasks_v1.json

The outer runner must provide network-off/read-only/capability-dropped isolation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(fn, args):
    try:
        return ("value", fn(*args))
    except Exception as exc:
        return ("error", type(exc).__name__)


def verify(
    *,
    candidate_path: Path,
    reference_path: Path,
    manifest_path: Path,
    task_id: str,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    task = next(
        (row for row in manifest["tasks"] if row["task_id"] == task_id),
        None,
    )
    if task is None:
        raise ValueError("unknown_task_id")

    candidate = _load_module(candidate_path, "rlab_candidate")
    reference = _load_module(reference_path, "rlab_reference")

    candidate_fn = getattr(candidate, task["function"])
    reference_fn = getattr(reference, task["function"])

    cases = []
    passed = 0
    for index, case in enumerate(task["cases"]):
        args = case.get("args", [])
        expected = _call(reference_fn, args)
        actual = _call(candidate_fn, args)
        ok = actual == expected
        passed += int(ok)
        cases.append(
            {
                "index": index,
                "passed": ok,
                "expected_kind": expected[0],
                "actual_kind": actual[0],
            }
        )

    return {
        "schema_version": 1,
        "task_id": task_id,
        "function": task["function"],
        "verification_status": "pass" if passed == len(cases) else "fail",
        "verification_cases": len(cases),
        "verification_passed_cases": passed,
        "passed": passed == len(cases),
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()

    result = verify(
        candidate_path=Path(args.candidate),
        reference_path=Path(args.reference),
        manifest_path=Path(args.manifest),
        task_id=args.task_id,
    )
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
