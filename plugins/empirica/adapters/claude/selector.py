"""Translate Claude payload identity into an Empirica run selector (ADR-31).

Claude supplies a raw ``session_id`` and a working directory.  The wire contract deliberately does
not know how a host represents either, so this adapter applies the shared state identity functions:
Git-common-directory project identity and a traversal-safe session identity.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from adapters.state import project_id, run_id


class SelectorError(ValueError):
    """A Claude payload does not carry enough well-typed identity to select a run."""


@dataclass(frozen=True)
class PayloadContext:
    cwd: Path
    session_id: str


def context_from_payload(payload: Mapping[str, object]) -> PayloadContext:
    """Return validated host context.

    ``cwd`` keeps Claude's established fallback to the process directory when the field is absent;
    a present non-string/empty value is rejected instead of being stringified into a surprising
    repository path.
    """
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise SelectorError("session_id must be a non-empty string")
    cwd_value = payload.get("cwd")
    if cwd_value is None:
        cwd = Path.cwd()
    elif isinstance(cwd_value, str) and cwd_value:
        cwd = Path(cwd_value)
    else:
        raise SelectorError("cwd must be a non-empty string when present")
    return PayloadContext(cwd=cwd, session_id=session_id)


def selector_from_payload(payload: Mapping[str, object]) -> dict[str, str]:
    """Build the transport-neutral ``StartRun.selector`` for a Claude payload."""
    context = context_from_payload(payload)
    return {"project": project_id(context.cwd), "session": run_id(context.session_id)}
