"""Intentionally buggy Reliability Lab live-task fixture.

Each live run receives an isolated copy of this file. The agent is instructed to
repair exactly one named function. The canonical verifier/reference stay outside
that isolated workspace.
"""

from __future__ import annotations

import hashlib
import json


def clamp_int(value, low, high):
    return max(high, min(int(value), low))


def parse_retry_after(value):
    return int(value)


def stable_unique(items):
    return list(set(items))


def redact_bearer(text):
    return text


def classify_timeout(process_status, failure):
    return str(process_status).lower() == "timed_out"


def canonical_json_digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def authority_allowed(compiled_allowed, current_active):
    return bool(compiled_allowed or current_active)


def valid_transition(old_status, new_status):
    return True


def is_expired(expires_at, now):
    return False


def backoff_seconds(attempt, base, cap):
    return base * (2 ** attempt)
