"""Inactive Claude route-order translations using exact v2 first-write-wins operations.

No timestamp or ordering decision is made here.  A harness timestamp is forwarded as transport
metadata (a string) when present; the application service establishes the authoritative total order
with its CAS-guarded monotone sequence and implements first-write-wins for both operations.
"""
from __future__ import annotations

from collections.abc import Mapping

from .correlation import PROTOCOL, request_id as new_request_id
from .selector import context_from_payload
from .transport import BridgeTransport, Transport

INVESTIGATIVE_TOOLS = frozenset({
    "Read", "Glob", "Grep", "Bash", "WebFetch", "WebSearch", "NotebookRead", "LSP",
    "Agent",
})


def _handle(run_id: object) -> str:
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty application run handle")
    return run_id


def observed_at(payload: Mapping[str, object]) -> str | None:
    """Return a genuine nonempty string host timestamp without consulting a clock.

    Only real string timestamps from the host event are forwarded; numeric, bool, and null
    values are omitted — never stringified as a ``seq:`` stamp (D6-C §3/C2).
    """
    for key in ("timestamp", "ts", "time", "event_ts"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _request(
    payload: Mapping[str, object], run_id: str, action: dict, operation: str,
    correlation_id: str | None,
) -> dict:
    context_from_payload(payload)
    command: dict = {"type": "ObserveAction", "run_id": _handle(run_id), "action": action}
    stamp = observed_at(payload)
    if stamp is not None:
        command["observed_at"] = stamp
    return {
        "protocol": PROTOCOL,
        "request_id": correlation_id or new_request_id(payload, operation),
        "command": command,
    }


def build_investigation_request(
    payload: Mapping[str, object], run_id: str, *, correlation_id: str | None = None,
) -> dict | None:
    """Build the first-investigation operation for every investigative native tool."""
    context_from_payload(payload)
    name = payload.get("tool_name")
    # Unknown tools (including third-party MCP research and writers) fail closed.
    # Only exact preparation/public names are exempt; suffix matching can spoof a server.
    public = {prefix + tool for prefix in ("", "mcp__plugin_empirica_empirica__")
              for tool in ("empirica_read", "empirica_observe", "report_convergence")}
    if not isinstance(name, str) or name in public | {"ToolSearch", "AskUserQuestion"}:
        return None
    return _request(
        payload, run_id, {"kind": "investigate"}, "investigate", correlation_id,
    )


def build_route_announcement_request(
    payload: Mapping[str, object], run_id: str, *, reason: str = "",
    correlation_id: str | None = None,
) -> dict:
    """Build the explicit first-write-wins route announcement operation."""
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    return _request(
        payload, run_id, {"kind": "route", "reason": reason}, "announce-route", correlation_id,
    )


def dispatch_investigation(
    payload: Mapping[str, object], run_id: str, *, transport: Transport | None = None,
    correlation_id: str | None = None,
) -> dict | None:
    request = build_investigation_request(payload, run_id, correlation_id=correlation_id)
    if request is None:
        return None
    return (transport if transport is not None else BridgeTransport()).dispatch(request)


def dispatch_route_announcement(
    payload: Mapping[str, object], run_id: str, *, reason: str = "",
    transport: Transport | None = None, correlation_id: str | None = None,
) -> dict:
    request = build_route_announcement_request(
        payload, run_id, reason=reason, correlation_id=correlation_id,
    )
    return (transport if transport is not None else BridgeTransport()).dispatch(request)
