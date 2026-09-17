#!/usr/bin/env python3
"""The shared JSON bridge between any host adapter and the Empirica v2 core (ADR-30/31/32, D6-C).

A host adapter — the Pi extension over stdio, or the Claude/Codex hooks in-process — speaks the
``empirica/v2`` contract but owns no domain rules. This module is the single transport target that
composes the host-neutral ``application.v2`` service, so there is exactly one place that wires the
core to storage:

    request envelope (dict)
      -> application.v2 service (composed with an explicit exact registry profile)
      -> response envelope (dict)

D7 composition boundary: the bridge composes ``application.v2`` with the hardened located run
facade and Git artifact repository. Public handles are decoded only by the strict ``er2`` codec;
raw selectors are hashed into safe storage IDs and no selector index or legacy decoder exists.

Each host supplies an exact registry ``profile_id`` at bridge construction, never in a public
request. The generic bridge has no host default. Missing, malformed, or unknown profile returns
correlated v2 ``unavailable``/closed and never projects another host's facts.

Invalid requests are validated by ``application.protocol.dispatch_request`` and return
``invalid_request``/closed BEFORE profile composition. The bridge does not catch an invalid
request and relabel it unavailable. Bridge construction/configuration errors return exact v2
``unavailable``/closed.

Two entry points, one service:

* :func:`handle` runs the service in-process and returns the response dict. The Claude and Codex
  hooks use this directly (they are already Python), so they reach the same typed operations the
  Pi adapter reaches over the wire — no second definition of the rules, no host branch in the core.
* :func:`main` is the stdio entry the Pi transport spawns as a subprocess: read one JSON request
  from stdin, write one JSON response to stdout, exit 0. The exact caller-supplied profile is
  taken from ``EMPIRICA_HOST_PROFILE_ID`` only; there is no default.

Both entry points always yield a well-formed response envelope and never raise into the caller:
any construction/dispatch error is mapped to a closed Fault so a caller's gate fails closed
rather than parsing a crash. This is a transport, not a policy: it adds no rule the core does
not already enforce.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# plugins/empirica/adapters/bridge.py -> plugins/empirica (so core/application/adapters import).
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from application import protocol as _proto  # noqa: E402
from application import v2 as _v2  # noqa: E402
from adapters.execution import FilesystemWorkspace, SubprocessSpikeHarness  # noqa: E402
from adapters.git.artifact_repo import GitArtifactRepository  # noqa: E402
from adapters.state.located import LocatedRunRepository  # noqa: E402

_PROTOCOL = _proto._PROTOCOL
_PROFILES = _proto._PROFILES


def _fault(code: str, request_id: str) -> dict:
    """A schema-valid v2 Fault envelope (closed). The caller's gate treats it as a denial."""
    return {
        "protocol": _PROTOCOL,
        "request_id": request_id,
        "result": {"type": "Fault", "code": code, "fail_direction": "closed"},
    }


def build_service(profile_id: str):
    """Compose the v2 service with an explicit exact registry ``profile_id`` (D6-C §4).

    Requires an explicit exact ``profile_id``; there is no host default. A missing (``None``) or
    unknown profile raises :class:`ValueError`. The service uses the hardened machine-local run
    repository and a Git-backed append-only artifact store rooted at ``EMPIRICA_REPO_DIR`` or cwd.
    """
    if not isinstance(profile_id, str) or not profile_id:
        raise ValueError("an explicit exact registry profile_id is required")
    if profile_id not in _PROFILES:
        raise ValueError(f"unknown host profile_id: {profile_id!r}")
    runs = LocatedRunRepository()
    repo_dir = Path(os.environ.get("EMPIRICA_REPO_DIR", Path.cwd()))
    artifacts = GitArtifactRepository(repo_dir)
    return _v2.compose(
        workspace=FilesystemWorkspace(Path.cwd()), harness=SubprocessSpikeHarness(),
        runs=runs, artifacts=artifacts,
        host=None, profile_id=profile_id, limits={}, clock=None,
    )


def trusted_audit_plan(profile_id: str, run_id: str, child_id: str) -> dict | None:
    """Load the immutable operation committed with one audit reservation."""
    return build_service(profile_id).trusted_audit_plan(run_id=run_id, child_id=child_id)


def trusted_resolve_child(profile_id: str, run_id: str, native_id: str) -> str | None:
    """Resolve an exact native audit execution without exposing correlation publicly."""
    return build_service(profile_id).trusted_resolve_child(
        run_id=run_id, native_id=native_id, purpose="audit")


def trusted_evidence_leaf(profile_id: str, run_id: str, payload: dict) -> dict:
    """Host-observed deterministic evidence ingress; never exposed by public dispatch."""
    return build_service(profile_id).trusted_evidence_leaf(run_id=run_id, payload=payload)


def trusted_child_event(profile_id: str, run_id: str, child_id: str, event: dict) -> dict:
    """Host-adapter-only lifecycle ingress; never exposed by the public wire dispatcher."""
    return build_service(profile_id).trusted_child_event(
        run_id=run_id, child_id=child_id, event=event)


def trusted_audit_verdict(profile_id: str, run_id: str, child_id: str, payload: dict) -> dict:
    return build_service(profile_id).trusted_audit_verdict(
        run_id=run_id, child_id=child_id, payload=payload)


def trusted_attribution(profile_id: str, run_id: str, payload: dict) -> dict:
    return build_service(profile_id).trusted_attribution(run_id=run_id, payload=payload)


def handle(request: object, profile_id: str) -> dict:
    """Run one ``empirica/v2`` request in-process, returning the response envelope. Never
    raises: a construction/dispatch failure becomes a closed Fault. Invalid requests return
    ``invalid_request``/closed BEFORE profile composition. A missing/unknown profile on a valid
    request returns correlated ``unavailable``/closed. The handler calls the service's private
    ``_dispatch_validated`` seam (not ``dispatch``) so the request and response are validated
    exactly once by this outer protocol gateway.
    """
    def handler(envelope: dict) -> dict:
        try:
            service = build_service(profile_id)
        except ValueError:
            return _fault("unavailable", envelope["request_id"])
        return service._dispatch_validated(envelope)

    return _proto.dispatch_request(request, handler)


def main() -> int:
    profile_id = os.environ.get("EMPIRICA_HOST_PROFILE_ID")
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except (ValueError, TypeError):
        # Route the non-envelope through handle/protocol gateway: invalid_request/closed
        # with request_id "invalid-request" (no direct duplicate fault).
        request = None
    json.dump(handle(request, profile_id), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
