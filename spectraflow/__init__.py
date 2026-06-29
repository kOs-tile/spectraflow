"""
SPECTRAFLOW — LLM Production Observability & Semantic Drift Detection Platform

An OpenAI-compatible proxy that intercepts LLM calls, detects behavioral drift,
and auto-generates regression tests.

Author: Onur Kavi
License: MIT
"""

__version__ = "0.1.0"
__author__ = "Onur Kavi"
__license__ = "MIT"

from spectraflow.config import Settings, get_settings

__all__ = ["Settings", "get_settings", "__version__"]
