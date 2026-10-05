"""Local KAVI runner hook attestation for Reliability Lab.

The probe reads one explicitly supplied runner source file and emits only a
sanitized structural attestation. It never exports the local path or source
contents.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any


HOOK_MODULE = "spectraflow.reliability.kavi_runtime_shim"
REQUIRED_HOOKS = ("prepare_before_agent", "finalize_after_agent")
ATTESTATION_SCHEMA = "spectraflow.kavi-runtime-hook-attestation.v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_runner_source(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    raw = target.read_bytes()
    text = raw.decode("utf-8")

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {
            "schema": ATTESTATION_SCHEMA,
            "attested": False,
            "source_sha256": _sha256(raw),
            "source_size_bytes": len(raw),
            "module_reference_found": False,
            "hooks": {
                hook: {"imported": False, "called": False}
                for hook in REQUIRED_HOOKS
            },
            "reason": f"runner_syntax_error:{exc.msg}",
            "local_path_exported": False,
        }

    imported: set[str] = set()
    module_reference_found = False
    called: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == HOOK_MODULE:
            module_reference_found = True
            for alias in node.names:
                if alias.name in REQUIRED_HOOKS:
                    imported.add(alias.asname or alias.name)
                    imported.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == HOOK_MODULE:
                    module_reference_found = True

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in REQUIRED_HOOKS:
                    called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in REQUIRED_HOOKS:
                    called.add(node.func.attr)

    hooks = {
        hook: {
            "imported": hook in imported or module_reference_found,
            "called": hook in called,
        }
        for hook in REQUIRED_HOOKS
    }
    attested = module_reference_found and all(
        row["imported"] and row["called"] for row in hooks.values()
    )

    return {
        "schema": ATTESTATION_SCHEMA,
        "attested": attested,
        "source_sha256": _sha256(raw),
        "source_size_bytes": len(raw),
        "module_reference_found": module_reference_found,
        "hooks": hooks,
        "reason": None if attested else "required_two_hook_structure_not_found",
        "local_path_exported": False,
    }


def validate_hook_attestation(payload: dict[str, Any]) -> bool:
    if payload.get("schema") != ATTESTATION_SCHEMA:
        return False
    if payload.get("attested") is not True:
        return False
    if payload.get("local_path_exported") is not False:
        return False
    digest = payload.get("source_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        return False
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return all(
        hooks.get(hook, {}).get("imported") is True
        and hooks.get(hook, {}).get("called") is True
        for hook in REQUIRED_HOOKS
    )
