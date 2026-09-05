"""Request/response correlation for Codex hook deliveries."""
from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

PROTOCOL = "empirica/v1"


class CorrelationError(RuntimeError):
    """The bridge response cannot be proven to answer the request."""


def request_id(payload: Mapping[str, object], operation: str) -> str:
    """Mint a unique id while retaining Codex's turn/tool identifier as an audit hint."""
    hint = payload.get("tool_use_id") or payload.get("turn_id") or "event"
    hint = hint if isinstance(hint, str) else "event"
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in hint)[:48] or "event"
    return f"codex:{operation}:{safe}:{uuid4().hex}"


def correlate(request: Mapping[str, object], response: object) -> dict:
    expected = request.get("request_id")
    if not isinstance(response, dict):
        raise CorrelationError("bridge response must be an object")
    if response.get("protocol") != PROTOCOL:
        raise CorrelationError("bridge response used an unexpected protocol")
    if not isinstance(expected, str) or not expected or response.get("request_id") != expected:
        raise CorrelationError("bridge response did not echo request_id")
    if not isinstance(response.get("result"), dict):
        raise CorrelationError("bridge response has no result object")
    return response
