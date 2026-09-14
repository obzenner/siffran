"""Request/response correlation helpers for the Empirica v2 bridge (D6-C).

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
    """Mint a request id, retaining Claude's prompt id as a reviewable correlation hint.

    A random suffix keeps repeated hook deliveries distinct even when Claude reuses a prompt id.
    """
    prompt_id = payload.get("prompt_id")
    hint = prompt_id if isinstance(prompt_id, str) and prompt_id else "event"
    safe_hint = "".join(c if c.isalnum() or c in "._-" else "-" for c in hint)[:48] or "event"
    return f"claude:{operation}:{safe_hint}:{uuid4().hex}"


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
