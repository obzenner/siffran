"""Boundary check of the host-recorded runtime provenance of a ``StartRun`` (Empirica 4.1, D7).

A host whose profile declares ``subagents_compatibility`` executes its foreground audit in an external
runtime. Its adapter observes which package and exact version that is and records it once, as
``invocation.host_runtime``. This module is the application's check that the recorded facts satisfy
the profile; the request schema has already fixed their shape. The shared bridge applies the check
where the exact profile is bound (``adapters/bridge.py``, before any run exists), so every host entry
point — Pi over stdio, the Claude and Codex hooks in-process — refuses the same requests. The core never
sees or interprets the object: it is persisted beside the invocation and read only by receipt verification.

``rejection`` is pure and total over JSON values. The TypeScript adapter applies the same rules to
the same shared fixtures (``tests/fixtures/host-runtime-cases.json``) before it sends the request.

The runtime is recorded once, at ``StartRun``; a session that reloads or resumes may now be served by a
different (also reviewed) package. ``same_runtime`` is the single comparison the audit admission makes
between the recorded runtime and the one the adapter observes *now* (``adapters/pi/private_bridge.py``),
so a receipt derived from the recorded value can never name a runtime other than the one that ran the audit.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping


def _inside(path: object, root: object) -> bool:
    """Whether ``path`` is strictly below the absolute directory ``root`` (lexically; no filesystem).

    POSIX paths only, by design: the Pi adapter is POSIX-only (spikes run through ``/bin/sh`` under
    POSIX resource limits), so a Windows-style path is rejected rather than interpreted.
    """
    if not isinstance(path, str) or not isinstance(root, str):
        return False
    candidate, base = PurePosixPath(path), PurePosixPath(root)
    return (candidate.is_absolute() and base.is_absolute() and candidate != base
            and ".." not in candidate.parts and base in candidate.parents)


def rejection(profile: Mapping[str, Any], host_runtime: object) -> str | None:
    """A stable code when ``host_runtime`` does not satisfy ``profile``, else ``None``.

    Codes: ``unexpected`` (the profile has no external runtime but one was recorded), ``missing``,
    ``malformed`` (not an object with the recorded members), ``policy`` (another policy id),
    ``package`` (another package), ``version`` (not an exactly reviewed version), ``paths`` (the owner
    file or the preflight module is not inside the package root).
    """
    policy = profile.get("subagents_compatibility")
    if policy is None:
        return None if host_runtime is None else "unexpected"
    if host_runtime is None:
        return "missing"
    if not isinstance(host_runtime, Mapping) or not isinstance(host_runtime.get("subagents"), Mapping):
        return "malformed"
    subagents = host_runtime["subagents"]
    if host_runtime.get("policy_id") != policy["policy_id"]:
        return "policy"
    if subagents.get("package") != policy["package"]:
        return "package"
    if subagents.get("version") not in policy["reviewed_versions"]:
        return "version"
    root = subagents.get("package_root")
    if not (_inside(subagents.get("owner_path"), root) and _inside(subagents.get("preflight_path"), root)):
        return "paths"
    return None


def same_runtime(recorded: object, observed: object) -> bool:
    """Whether the adapter's currently observed runtime is exactly the one recorded at ``StartRun``.

    Both absent is the same (a host with no external runtime); otherwise both must be JSON objects
    with equal content. There is no partial match: another version, package root, owner or preflight
    path is another runtime.
    """
    if recorded is None or observed is None:
        return recorded is None and observed is None
    return (isinstance(recorded, Mapping) and isinstance(observed, Mapping)
            and _plain(recorded) == _plain(observed))


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
