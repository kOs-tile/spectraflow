"""Canonical verifier functions for Reliability Lab live tasks."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime


def clamp_int(value, low, high):
    if low > high:
        raise ValueError("low must be <= high")
    return max(low, min(high, int(value)))


def parse_retry_after(value):
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def stable_unique(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def redact_bearer(text):
    return re.sub(
        r"(?i)Bearer\s+[^\s,;]+",
        "Bearer [REDACTED]",
        str(text),
    )


def classify_timeout(process_status, failure):
    return (
        str(process_status or "").lower() == "timed_out"
        or "timeout" in str(failure or "").lower()
    )


def canonical_json_digest(value):
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def authority_allowed(compiled_allowed, current_active):
    return bool(compiled_allowed) and bool(current_active)


def valid_transition(old_status, new_status):
    allowed = {
        "ready": {"running", "blocked", "cancelled"},
        "running": {"completed", "failed", "blocked"},
        "blocked": {"ready", "cancelled"},
        "queued": {"ready", "cancelled"},
    }
    return str(new_status).lower() in allowed.get(str(old_status).lower(), set())


def _dt(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def is_expired(expires_at, now):
    return _dt(expires_at) <= _dt(now)


def backoff_seconds(attempt, base, cap):
    return min(float(cap), float(base) * (2 ** int(attempt)))
