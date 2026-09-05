"""Interpret the explicit failure direction on ``empirica/v1`` Fault results.

A host event supplies the fallback because native events differ: a completion gate defaults closed,
while observational/run-start events default open so an unavailable adapter cannot wedge a prompt.
The core's explicit direction always wins.
"""
from __future__ import annotations

from enum import Enum


class FailureDirection(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


def failure_direction(response: object, *, fallback: FailureDirection) -> FailureDirection:
    """Return an explicit Fault direction, or the host event's declared fallback.

    Malformed responses are transport failures and therefore use ``fallback``.  Non-Fault results
    are not failures, but returning the fallback keeps this helper total and prevents callers from
    accidentally inventing a third policy.
    """
    if not isinstance(response, dict):
        return fallback
    result = response.get("result")
    if not isinstance(result, dict) or result.get("type") != "Fault":
        return fallback
    raw = result.get("fail_direction")
    try:
        return FailureDirection(raw)
    except (TypeError, ValueError):
        return fallback


def blocks_on_failure(response: object, *, fallback: FailureDirection) -> bool:
    """Whether the applicable failure policy is fail-closed."""
    return failure_direction(response, fallback=fallback) is FailureDirection.CLOSED
