"""Request/response correlation for Codex hook deliveries (D6-C C2b).

The request id is transport metadata only; run identity comes exclusively from the
``StartRun``/``ResolveRun`` selector.  :func:`correlate` proves a bridge response answers the
request that was sent by checking the exact v2 protocol and request-id echo.
"""
from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

PROTOCOL = "empirica/v2"


class CorrelationError(RuntimeError):
    """The bridge response cannot be proven to answer the request that was sent."""


def request_id(payload: Mapping[str, object], operation: str) -> str:
    """Mint a request id, retaining Codex's turn/tool identifier as a reviewable correlation hint.

    A random suffix keeps repeated hook deliveries distinct even when Codex reuses a turn id.
    """
    hint = payload.get("tool_use_id") or payload.get("turn_id") or "event"
    hint = hint if isinstance(hint, str) and hint else "event"
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in hint)[:48] or "event"
    return f"codex:{operation}:{safe}:{uuid4().hex}"


def correlate(request: Mapping[str, object], response: object) -> dict:
    """Validate the exact v2 protocol and request-id echo, returning the response dictionary."""
    expected = request.get("request_id")
    if not isinstance(response, dict):
        raise CorrelationError("bridge response must be an object")
    if response.get("protocol") != PROTOCOL:
        raise CorrelationError("bridge response used an unexpected protocol")
    if not isinstance(expected, str) or not expected or response.get("request_id") != expected:
        raise CorrelationError("bridge response did not echo the request_id")
    if not isinstance(response.get("result"), dict):
        raise CorrelationError("bridge response has no result object")
    return response
