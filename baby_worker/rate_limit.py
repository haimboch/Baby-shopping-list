"""Small process-wide rate limiter for shared third-party API budgets."""

from __future__ import annotations

import time
from typing import Any


_LAST_CALL: dict[str, float] = {}


def wait_for_slot(provider: str, minimum_interval: float) -> float:
    """Wait until the provider's next safe request slot and return wait seconds."""
    minimum_interval = max(0.0, float(minimum_interval))
    previous = _LAST_CALL.get(provider)
    if previous is None or minimum_interval <= 0:
        return 0.0
    remaining = minimum_interval - (time.monotonic() - previous)
    if remaining > 0:
        time.sleep(remaining)
        return remaining
    return 0.0


def record_call(provider: str) -> None:
    """Record an attempted call, including attempts that timed out."""
    _LAST_CALL[provider] = time.monotonic()


def retry_after_seconds(response: Any, *, default: float = 60.0, maximum: float = 75.0) -> float:
    """Read Retry-After from either headers or the provider's JSON error."""
    value: Any = None
    headers = getattr(response, "headers", None)
    if headers:
        value = headers.get("Retry-After") or headers.get("retry-after")
    if value in (None, ""):
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                value = error.get("retryAfterSeconds")
        except (TypeError, ValueError):
            value = None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = float(default)
    return min(max(1.0, seconds), float(maximum))
