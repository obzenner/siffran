#!/usr/bin/env python3
"""The shared JSON bridge between any host adapter and the Empirica core (ADR-30/31/32).

A host adapter — the Pi extension in ``pi/src`` over stdio, or the Claude Code hooks in
``hooks/`` in-process — speaks the ``empirica/v1`` contract but owns no domain rules. This
module is the single transport target that runs the host-neutral
:class:`application.EmpiricaService` against the real persistence adapters, so there is exactly
one place that wires the core to storage:

    request envelope (dict)
      -> EmpiricaService.handle(request)            (all decisions live here)
      -> response envelope (dict)

Two entry points, one service:

* :func:`handle` runs the service in-process and returns the response dict. The Claude and Codex
  hooks use this directly (they are already Python), so they reach the same typed operations the
  Pi adapter reaches over the wire — no second definition of the rules, no host branch in the core.
* :func:`main` is the stdio entry the Pi transport spawns as a subprocess: read one JSON request
  from stdin, write one JSON response to stdout, exit 0.

State lives only under the machine-local home (``$EMPIRICA_HOME`` or ``~/.empirica-plugin``,
ADR-31) via :class:`adapters.state.FilesystemRunRepository`; knowledge artifacts live under Git
shadow refs via :class:`adapters.git.GitArtifactRepository`. Nothing here writes host-specific
runtime directories or the working tree.

Both entry points always yield a well-formed response envelope and never raise into the caller:
any construction/dispatch error is mapped to a closed ``Fault`` so a caller's gate fails closed
rather than parsing a crash. This is a transport, not a policy: it adds no rule the core does not
already enforce.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# plugins/empirica/adapters/bridge.py -> plugins/empirica (so core/application/adapters import).
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

API_VERSION = "empirica/v1"


def _fault_envelope(request_id: str, message: str) -> dict:
    """A closed Fault the caller's gate treats as a denial (never a silent pass)."""
    return {
        "protocol": API_VERSION,
        "request_id": request_id or "unknown",
        "result": {
            "type": "Fault",
            "code": "unavailable",
            "message": message,
            "fail_direction": "closed",
        },
    }


def build_service(cwd: Path | None = None):
    """Wire the service against the real persistence adapters. Operational state is machine-local
    (``$EMPIRICA_HOME``); knowledge artifacts are Git-backed and rooted at ``cwd`` (the workspace,
    the bridge's working directory by default)."""
    from adapters.git import GitArtifactRepository
    from adapters.state import FilesystemRunRepository, GenerationAllocator
    from application import EmpiricaService

    runs = FilesystemRunRepository()
    artifacts = GitArtifactRepository(Path(cwd) if cwd is not None else Path.cwd())
    return EmpiricaService(runs, artifacts, GenerationAllocator(runs))


def handle(request: object, *, cwd: Path | None = None) -> dict:
    """Run one ``empirica/v1`` request against the real adapters, in-process, returning the
    response envelope. Never raises: a construction/dispatch failure becomes a closed Fault so an
    in-process caller (the Claude hooks) gets the same fail-closed transport guarantee the stdio
    bridge gives the Pi adapter."""
    request_id = request.get("request_id") if isinstance(request, dict) else None
    request_id = request_id if isinstance(request_id, str) and request_id else "unknown"
    try:
        service = build_service(cwd)
        return service.handle(request)
    except Exception as exc:  # noqa: BLE001 - the bridge must never crash the caller's gate
        return _fault_envelope(request_id, f"empirica core error: {exc}")


def main() -> int:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except (ValueError, TypeError):
        # A malformed request still gets a correlated fault; the service would do the same, but it
        # cannot run if we cannot even decode the envelope.
        json.dump(_fault_envelope("unknown", "request was not valid JSON"), sys.stdout)
        return 0

    json.dump(handle(request), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
