"""Codex CLI adapter for the shared ``empirica/v1`` application service."""

from .lifecycle import (
    build_investigation_request,
    build_reserve_spawn_request,
    build_restore_request,
    build_route_request,
    build_start_run_request,
    build_stop_request,
    explicit_activation,
    event_stamp,
)
from .transport import BridgeTransport, Transport, dispatch

__all__ = [
    "BridgeTransport",
    "Transport",
    "build_investigation_request",
    "build_reserve_spawn_request",
    "build_restore_request",
    "build_route_request",
    "build_start_run_request",
    "build_stop_request",
    "dispatch",
    "event_stamp",
    "explicit_activation",
]
