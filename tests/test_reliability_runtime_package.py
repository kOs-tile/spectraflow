import os
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).parents[1]


def test_runtime_package_metadata_is_minimal():
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["name"] == "spectraflow-reliability-runtime"
    assert payload["project"]["dependencies"] == []
    assert payload["tool"]["setuptools"]["packages"] == [
        "spectraflow",
        "spectraflow.reliability",
        "benchmark",
    ]
    assert "live_tasks_v1.json" not in str(payload)
    assert "*.json" in payload["tool"]["setuptools"]["package-data"]["benchmark"]


def test_reliability_hook_import_does_not_eagerly_require_full_app_dependencies(tmp_path):
    script = r"""
import builtins

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.startswith("pydantic"):
        raise RuntimeError("pydantic import forbidden in minimal runtime smoke")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import

import spectraflow.reliability.kavi_runtime_shim as shim

assert shim.SUITE == "spectraflow.reliability-live.v1"
"""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
