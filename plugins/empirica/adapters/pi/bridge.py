#!/usr/bin/env python3
"""The shared JSON stdio bridge between a host adapter and the Empirica core.

A host adapter (e.g. the Pi extension in ``src/``) speaks the ``empirica/v1``
contract but owns no domain rules; this bridge is the transport target that runs
the host-neutral :class:`application.EmpiricaService` against the real
persistence adapters. It is deliberately minimal:

    read one JSON request envelope from stdin
      -> EmpiricaService.handle(request)            (all decisions live here)
      -> write one JSON response envelope to stdout
      -> exit 0

State lives only under the machine-local home (``$EMPIRICA_HOME`` or
``~/.empirica-plugin``, ADR-31) via :class:`adapters.state.FilesystemRunRepository`,
and knowledge artifacts under Git shadow refs via
:class:`adapters.git.GitArtifactRepository`. Nothing here writes to ``.pi``,
``.claude``, or the working tree.

The bridge always emits a well-formed response envelope and exits 0 even on
failure, mapping any construction/dispatch error to a closed ``Fault`` so the
caller's gate fails closed rather than parsing a crash. This is a transport, not
a policy: it adds no rule the core does not already enforce.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# plugins/empirica/adapters/pi/bridge.py -> plugins/empirica (so core/application/adapters import)
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
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


def _build_service():
    """Wire the service against the real persistence adapters. Artifacts are
    Git-backed and rooted at the bridge's working directory (the workspace)."""
    from adapters.git import GitArtifactRepository
    from adapters.state import FilesystemRunRepository, GenerationAllocator
    from application import EmpiricaService

    runs = FilesystemRunRepository()
    artifacts = GitArtifactRepository(Path.cwd())
    return EmpiricaService(runs, artifacts, GenerationAllocator(runs))


def main() -> int:
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except (ValueError, TypeError):
        # A malformed request still gets a correlated fault; the service would do
        # the same, but it cannot run if we cannot even decode the envelope.
        json.dump(_fault_envelope("unknown", "request was not valid JSON"), sys.stdout)
        return 0

    request_id = request.get("request_id") if isinstance(request, dict) else None
    request_id = request_id if isinstance(request_id, str) else "unknown"

    try:
        service = _build_service()
        response = service.handle(request)
    except Exception as exc:  # noqa: BLE001 - the bridge must never crash the caller's gate
        json.dump(_fault_envelope(request_id, f"empirica core error: {exc}"), sys.stdout)
        return 0

    json.dump(response, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
