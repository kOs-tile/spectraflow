"""Local KAVI runner hook attestation for Reliability Lab.

The probe reads one explicitly supplied runner source file and emits only a
sanitized structural attestation. It never exports the local path or source
contents.

A valid attestation requires both benchmark hooks to be called from the existing
execution_control.execute_once() path, with prepare_before_agent ordered before
finalize_after_agent. Merely importing or calling the hooks elsewhere is not
sufficient.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any


HOOK_MODULE = "spectraflow.reliability.kavi_runtime_shim"
REQUIRED_HOOKS = ("prepare_before_agent", "finalize_after_agent")
EXECUTION_FUNCTION = "execute_once"
ATTESTATION_SCHEMA = "spectraflow.kavi-runtime-hook-attestation.v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _function_node(tree: ast.AST, name: str) -> ast.AST | None:
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_name(node: ast.Call, aliases: dict[str, str]) -> str | None:
    if isinstance(node.func, ast.Name):
        return aliases.get(node.func.id, node.func.id)
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


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
            "execution_function_found": False,
            "execution_function": EXECUTION_FUNCTION,
            "hooks": {
                hook: {
                    "imported": False,
                    "called": False,
                    "called_in_execution_function": False,
                }
                for hook in REQUIRED_HOOKS
            },
            "prepare_before_finalize": False,
            "reason": f"runner_syntax_error:{exc.msg}",
            "local_path_exported": False,
        }

    aliases: dict[str, str] = {}
    module_reference_found = False
    imported_canonical: set[str] = set()
    global_called: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == HOOK_MODULE:
            module_reference_found = True
            for alias in node.names:
                if alias.name in REQUIRED_HOOKS:
                    imported_canonical.add(alias.name)
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == HOOK_MODULE:
                    module_reference_found = True

        if isinstance(node, ast.Call):
            name = _call_name(node, aliases)
            if name in REQUIRED_HOOKS:
                global_called.add(name)

    execution_node = _function_node(tree, EXECUTION_FUNCTION)
    scoped_lines: dict[str, list[int]] = {hook: [] for hook in REQUIRED_HOOKS}
    if execution_node is not None:
        for node in ast.walk(execution_node):
            if isinstance(node, ast.Call):
                name = _call_name(node, aliases)
                if name in REQUIRED_HOOKS:
                    scoped_lines[name].append(getattr(node, "lineno", 0))

    hooks = {
        hook: {
            "imported": hook in imported_canonical or module_reference_found,
            "called": hook in global_called,
            "called_in_execution_function": bool(scoped_lines[hook]),
        }
        for hook in REQUIRED_HOOKS
    }

    prepare_lines = scoped_lines["prepare_before_agent"]
    finalize_lines = scoped_lines["finalize_after_agent"]
    ordered = bool(
        prepare_lines
        and finalize_lines
        and min(prepare_lines) < max(finalize_lines)
    )

    execution_found = execution_node is not None
    structural_hooks = all(
        row["imported"]
        and row["called"]
        and row["called_in_execution_function"]
        for row in hooks.values()
    )
    attested = (
        module_reference_found
        and execution_found
        and structural_hooks
        and ordered
    )

    if not module_reference_found:
        reason = "runtime_shim_module_not_found"
    elif not execution_found:
        reason = "execute_once_not_found"
    elif not structural_hooks:
        reason = "required_two_hook_structure_not_found"
    elif not ordered:
        reason = "hook_order_invalid"
    else:
        reason = None

    return {
        "schema": ATTESTATION_SCHEMA,
        "attested": attested,
        "source_sha256": _sha256(raw),
        "source_size_bytes": len(raw),
        "module_reference_found": module_reference_found,
        "execution_function_found": execution_found,
        "execution_function": EXECUTION_FUNCTION,
        "hooks": hooks,
        "prepare_before_finalize": ordered,
        "reason": reason,
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
    if payload.get("execution_function") != EXECUTION_FUNCTION:
        return False
    if payload.get("execution_function_found") is not True:
        return False
    if payload.get("prepare_before_finalize") is not True:
        return False

    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return all(
        hooks.get(hook, {}).get("imported") is True
        and hooks.get(hook, {}).get("called") is True
        and hooks.get(hook, {}).get("called_in_execution_function") is True
        for hook in REQUIRED_HOOKS
    )


def validate_hook_attestation_binding(
    payload: dict[str, Any],
    *,
    deployed_runner_path: str | Path,
    source_runner_path: str | Path | None = None,
) -> dict[str, Any]:
    """Bind a structural attestation to the current deployed/source runner bytes.

    The returned object is sanitized: it exposes digests and booleans only, never
    the local paths used for verification.
    """

    attestation_valid = validate_hook_attestation(payload)

    deployed = Path(deployed_runner_path).expanduser().resolve()
    deployed_sha256 = _sha256(deployed.read_bytes())

    source_sha256 = None
    source_matches_deployed = None
    source_matches_attestation = None
    if source_runner_path is not None:
        source = Path(source_runner_path).expanduser().resolve()
        source_sha256 = _sha256(source.read_bytes())
        source_matches_deployed = source_sha256 == deployed_sha256
        source_matches_attestation = (
            source_sha256 == payload.get("source_sha256")
        )

    deployed_matches_attestation = (
        deployed_sha256 == payload.get("source_sha256")
    )

    bound = (
        attestation_valid
        and deployed_matches_attestation
        and (
            source_runner_path is None
            or (
                source_matches_deployed is True
                and source_matches_attestation is True
            )
        )
    )

    return {
        "bound": bound,
        "attestation_valid": attestation_valid,
        "attested_source_sha256": payload.get("source_sha256"),
        "deployed_sha256": deployed_sha256,
        "deployed_matches_attestation": deployed_matches_attestation,
        "source_sha256": source_sha256,
        "source_matches_deployed": source_matches_deployed,
        "source_matches_attestation": source_matches_attestation,
        "local_path_exported": False,
    }
