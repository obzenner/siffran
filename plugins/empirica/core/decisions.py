#!/usr/bin/env python3
"""Typed decision outcomes for the host-neutral empirica decision core (ADR-30 semantics).

The core answers one question — may an empirica run stop, and did it converge? — and returns
exactly one of four outcomes. NONE of them names a hook event, an exit code, a filesystem path,
a git command, or a UI string. Mapping an outcome onto a Stop-hook exit code, a stderr message,
or a manifest write is the HOST ADAPTER's job, deliberately kept out of this package.

  Inert  — this is not an empirica run; the core has no opinion (an adapter must never wedge it).
  Fault  — run/graph state cannot be trusted; the core refuses to judge, and the safe reading is
           "do not stop" (an adapter fails CLOSED). Carries a human-neutral reason.
  Allow  — the run may stop. `converged` says whether it truly converged; a residual, refuted, or
           frozen stop is `converged=False` — the core NEVER fabricates green (ADR-17). Carries
           the report fields a caller may surface.
  Block  — the run has NOT reached a stop the core will bless; the substantive verdict is "keep
           going". Carries WHY, structured, so an adapter can render it and — for `kind ==
           "converging"` or `"audit_failed"` — decide whether a pass-budget cap turns this into a
           non-converged stop instead (that termination policy is persistence, hence the adapter's).

These are frozen dataclasses rather than an enum + payload dict so that a caller reads a typed
field, not a stringly-keyed bag: the whole point of the extraction is that the decision has a
shape. `Decision` is the shared base for exhaustive `isinstance` dispatch.
"""
from dataclasses import dataclass


class Decision:
    """Sealed base for the four outcomes. Exists only for isinstance dispatch in an adapter."""


@dataclass(frozen=True)
class Inert(Decision):
    """Not an empirica run. The core has no opinion; an adapter must let the session proceed."""


@dataclass(frozen=True)
class Fault(Decision):
    """Run or graph state cannot be trusted (corrupt manifest, missing/corrupt graph on an active
    run). The core refuses to judge; the safe reading is to keep the run from stopping."""
    reason: str


@dataclass(frozen=True)
class ClaimReason:
    """One still-open claim, with the reason it is not yet terminal — the pure content an adapter
    needs to explain a block, with no formatting or host framing attached."""
    claim_id: str
    text: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class Allow(Decision):
    """The run may stop.

    `converged` is the load-bearing field: True ONLY when every gating goal is terminal, the top
    goal was not refuted, nothing was deferred, and the independent audit passed. Every other allow
    — residual, refuted root, frozen, finished, legacy — is `converged=False`.

    `status` is the terminal status an adapter would record for an ACTIVE run: one of
    "converged" | "stopped_residual" | "stopped_frozen". The sentinels "finished" and "legacy" mean
    the run was already terminal (or predates the substrate), so there is NOTHING for an adapter to
    persist — they are informational only.
    """
    converged: bool
    status: str
    note: str | None = None
    deferred: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    budget_blocked: tuple[str, ...] = ()
    audit: str | None = None
    attribution: dict | None = None
    p1_violation: str | None = None
    p1_unverified: str | None = None
    root_refuted: bool = False


@dataclass(frozen=True)
class Block(Decision):
    """The core will not bless this stop; keep going.

    `kind` is "converging" (claims still open) or "audit_failed" (the claim graph converged but the
    independent audit did not pass). Both are cases where an adapter's pass-budget cap MAY convert
    this into a non-converged `Allow` instead of a real block — that termination policy is
    persistence and belongs to the adapter, not here.
    """
    kind: str
    reason: str
    open_claims: tuple[ClaimReason, ...] = ()
    audit_reason: str | None = None
    p1_note: str | None = None
    frozen: tuple[str, ...] = ()
    frozen_count: int = 0
