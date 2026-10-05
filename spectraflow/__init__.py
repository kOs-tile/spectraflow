"""
SPECTRAFLOW — LLM Production Observability & Semantic Drift Detection Platform

An OpenAI-compatible proxy that intercepts LLM calls, detects behavioral drift,
and auto-generates regression tests.

Author: Onur Kavi
License: MIT
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


__version__ = "0.1.0"
__author__ = "Onur Kavi"
__license__ = "MIT"

__all__ = ["Settings", "get_settings", "__version__"]


if TYPE_CHECKING:
    from spectraflow.config import Settings as Settings


def __getattr__(name: str) -> Any:
    """Keep the historical top-level config API without eager dependencies."""

    if name in {"Settings", "get_settings"}:
        from spectraflow.config import Settings, get_settings

        return {
            "Settings": Settings,
            "get_settings": get_settings,
        }[name]
    raise AttributeError(f"module 'spectraflow' has no attribute {name!r}")
