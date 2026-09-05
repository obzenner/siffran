"""The run's *operational state* — the single CAS-guarded document behind :class:`RunRepository`.

This is the operational plane of ADR-31: the small mutable record a run updates as it progresses.
ADR-31 assigns this plane a specific inventory — "active-run pointers, status, phases, budgets,
modes, tickets, locks, and recovery journals" — and this document is where all of it lives so an
adapter needs no host-specific side files (no separate ledger, ticket file, or mode file). The knowledge plane (the graph and evidence themselves) lives in the append-only
:class:`ArtifactRepository`; this document only ever holds the content address of the graph that is
current, never the graph.

Why a pointer and not the graph inline: an artifact append is immutable and content-addressed, so a
graph update is "append the new graph, then compare-and-set this pointer to it" (see
``service._update_graph``). The CAS is what makes the swap atomic — a losing writer never advances
the pointer, so the pointer can only ever name a graph that was actually appended, never an orphan.

The stored form is a plain JSON object (the filesystem adapter requires it); this dataclass is the
typed view the service manipulates. ``revision`` here is the *wire* revision — a monotone integer a
caller sees in a response — and is distinct from the opaque storage :class:`~core.records.Revision`
token the repository mints for CAS.

Two ordering ideas are load-bearing and worth stating once:

* **First-write-wins ordering witnesses** (ADR-20 P1). ``route_seq`` and ``first_investigation_seq``
  are positions in this document's own write order, each assigned once (the first write wins) from
  the monotone ``stamp_seq`` counter. Because both are drawn from one counter under CAS, they form
  a *total* order the host cannot forge by choosing a stamp format — so the P1 "route before
  investigation" verdict is always comparable, never inconclusive (contrast the legacy
  ``stamps.py`` which had to reconcile ISO/``seq:``/``pass:`` stamp kinds).
* **The phase machine** (ADR-21 M1) is monotone: ``route → resolve → assess → audit → converged``.
  A transition may hold or advance, never regress — a phase is a record of progress, and letting it
  run backward would let a run relabel itself out of a commitment it already made.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .wire import STATUS_ACTIVE

SCHEMA = "empirica.run/1"
DEFAULT_MAX_PASSES = 8  # mirrors the hooks' DEFAULT_MAX_PASSES (manifest.py); a positive cap.
DEFAULT_THETA = 0.8  # mirrors EMPIRICA_THETA's default; the confidence threshold to approve a claim.

# The run's phase machine (ADR-21 M1). Ordered and monotone: a transition may stay or advance, never
# regress. `route` is the start — a run must classify its dependencies before investigating (P1).
PHASES = ("route", "resolve", "assess", "audit", "converged")
DEFAULT_PHASE = "route"

# The run modes (ADR-24/ADR-28), both OFF by default. A closed vocabulary: an unknown mode key is
# refused rather than persisted, so a typo cannot look like it enabled something.
MODES = ("multi_provider", "cli_exec")

# The lifecycle statuses this document may hold. Terminal statuses read as "not active", which is
# what tells the generation allocator to open a fresh generation next time (ADR-31).
TERMINAL_STATUSES = frozenset(
    {"converged", "stopped_residual", "stopped_frozen", "stopped_budget"}
)
# The closed status vocabulary a stored document may hold. Anything outside it is corrupt — an
# unknown status must fail closed, never be read as "terminal" (which would fail OPEN a reserve).
_VALID_STATUSES = frozenset({STATUS_ACTIVE}) | TERMINAL_STATUSES


@dataclass(frozen=True)
class OperationalState:
    """The typed operational-state document. Frozen: the service evolves it with :meth:`evolve`
    and writes the new value under CAS, never mutating in place (ADR-31 passes state by value)."""

    status: str
    revision: int
    passes: int
    max_passes: int
    theta: float
    goal: str
    claim_graph_artifact_id: str | None = None
    phase: str = DEFAULT_PHASE
    # --- spawn budget (ADR-17) ---
    max_spawns: int | None = None
    spawns: int = 0
    # --- scope freeze (ADR-26): the claims already gating at freeze time ---
    frozen_claims: tuple[str, ...] | None = None
    freeze_seq: int | None = None
    # --- P1 ordering witnesses (ADR-20 P1): positions in this document's write order ---
    route_seq: int | None = None
    route_reason: str = ""
    first_investigation_seq: int | None = None
    stamp_seq: int = 0
    # --- audit tickets (ADR-20 P6): server-minted spawn nonces + dispatch attribution ---
    audit_tickets: tuple[dict, ...] = ()
    # --- actor dispatch attribution (ADR-24) ---
    dispatches: tuple[dict, ...] = ()
    # --- modes (ADR-24/28) ---
    modes: dict = field(default_factory=dict)
    is_legacy: bool = False

    @classmethod
    def new(cls, *, goal: str, max_passes: int, max_spawns: int | None,
            theta: float, modes: dict | None) -> OperationalState:
        """A fresh active run at revision 0. Starts clean: no graph pointer, zero passes, phase
        `route` — a new generation is empty by construction (ADR-31)."""
        return cls(status=STATUS_ACTIVE, revision=0, passes=0, max_passes=max_passes,
                   theta=theta, goal=goal, max_spawns=max_spawns, modes=modes or {})

    def evolve(self, **changes: object) -> OperationalState:
        """A copy with ``changes`` applied and the wire revision advanced by one. Every state write
        goes through here so the revision a caller sees always tracks the number of writes."""
        return replace(self, revision=self.revision + 1, **changes)  # type: ignore[arg-type]

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    # --- phase machine (ADR-21 M1) -------------------------------------------

    def can_advance_to(self, phase: str) -> bool:
        """Is a transition to ``phase`` valid from the current phase? Monotone: the target must be a
        known phase at or after the current one. A regression is refused — a phase records progress
        and a run must not be able to relabel itself back into an earlier commitment."""
        if phase not in PHASES or self.phase not in PHASES:
            return False
        return PHASES.index(phase) >= PHASES.index(self.phase)

    # --- spawn budget (ADR-17) -----------------------------------------------

    @property
    def spawns_remaining(self) -> float:
        """Spawns left before the cap; ``inf`` when unbounded (``max_spawns`` is None)."""
        if self.max_spawns is None:
            return float("inf")
        return max(0, self.max_spawns - self.spawns)

    def can_reserve_spawn(self) -> bool:
        """True when one more spawn fits under the cap (always true when unbounded)."""
        return self.max_spawns is None or self.spawns < self.max_spawns

    # --- ordering witnesses (ADR-20 P1) --------------------------------------

    def route_p1_verdict(self) -> tuple[str, str]:
        """The P1 "route before investigation" verdict ``(verdict, reason)`` derived from the write
        order. Because ``route_seq`` and ``first_investigation_seq`` are positions in one monotone,
        CAS-assigned counter, the ordering is a total order — always comparable, so the answer is
        only ever ``ok`` or ``violation``, never the legacy ``inconclusive`` (ADR-20 P1)."""
        if self.first_investigation_seq is None:
            return ("ok", "no investigative action recorded yet")
        if self.route_seq is None:
            return ("violation",
                    "investigation began before any route was announced (ADR-20 P1: routing is a "
                    "commitment made up front, not a label applied retroactively)")
        if self.route_seq <= self.first_investigation_seq:
            return ("ok", "route was announced before investigation began (by write order)")
        return ("violation",
                "the route was announced after investigation began — the routing decision was "
                "applied retroactively (ADR-20 P1)")

    # --- (de)serialisation ---------------------------------------------------

    def encode(self) -> dict:
        """The JSON object the repository stores. Tuples become lists; ``None`` frozen_claims stays
        ``None`` (not frozen) — distinct from ``[]`` (frozen with empty committed scope)."""
        return {
            "schema": SCHEMA,
            "status": self.status,
            "revision": self.revision,
            "passes": self.passes,
            "max_passes": self.max_passes,
            "theta": self.theta,
            "goal": self.goal,
            "claim_graph_artifact_id": self.claim_graph_artifact_id,
            "phase": self.phase,
            "max_spawns": self.max_spawns,
            "spawns": self.spawns,
            "frozen_claims": (list(self.frozen_claims)
                              if self.frozen_claims is not None else None),
            "freeze_seq": self.freeze_seq,
            "route_seq": self.route_seq,
            "route_reason": self.route_reason,
            "first_investigation_seq": self.first_investigation_seq,
            "stamp_seq": self.stamp_seq,
            "audit_tickets": [dict(t) for t in self.audit_tickets],
            "dispatches": [dict(d) for d in self.dispatches],
            "modes": self.modes,
            "is_legacy": self.is_legacy,
        }

    @classmethod
    def decode(cls, value: object) -> OperationalState | None:
        """Rebuild the typed state from a stored object, or ``None`` if it is not one of ours.

        A ``None`` return means fail-closed at the call site (treat as corrupt): a document that
        lacks our schema marker or whose load-bearing fields are the wrong type cannot be trusted to
        drive a stop decision, so the service must not silently coerce it (ADR-31 corrupt≠absent).
        """
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            return None
        try:
            status = value["status"]
            revision = value["revision"]
            passes = value["passes"]
            max_passes = value["max_passes"]
            theta = value["theta"]
            goal = value["goal"]
        except KeyError:
            return None
        # Every load-bearing field must be the right type AND within its domain, or the document is
        # corrupt and the caller fails closed (ADR-31 corrupt≠absent): an unknown status, a
        # non-positive cap, a negative/boolean counter, or a non-finite theta cannot be trusted to
        # drive a stop decision, so the service must not silently coerce it into a plausible run.
        if not (isinstance(status, str) and status in _VALID_STATUSES
                and _is_plain_int(revision) and revision >= 0
                and _is_plain_int(passes) and passes >= 0
                and _is_plain_int(max_passes) and max_passes >= 1
                and isinstance(goal, str)
                and isinstance(theta, (int, float)) and not isinstance(theta, bool)
                and math.isfinite(theta)):
            return None
        frozen = value.get("frozen_claims")
        frozen_claims = (tuple(c for c in frozen if isinstance(c, str))
                         if isinstance(frozen, list) else None)
        pointer = value.get("claim_graph_artifact_id")
        pointer = pointer if isinstance(pointer, str) else None
        modes = value.get("modes")
        phase = value.get("phase")
        return cls(
            status=status,
            revision=revision,
            passes=passes,
            max_passes=max_passes,
            theta=float(theta),
            goal=goal,
            claim_graph_artifact_id=pointer,
            phase=phase if phase in PHASES else DEFAULT_PHASE,
            max_spawns=_opt_int(value.get("max_spawns")),
            spawns=_nonneg_int(value.get("spawns")),
            frozen_claims=frozen_claims,
            freeze_seq=_opt_int(value.get("freeze_seq")),
            route_seq=_opt_int(value.get("route_seq")),
            route_reason=(value.get("route_reason")
                          if isinstance(value.get("route_reason"), str) else ""),
            first_investigation_seq=_opt_int(value.get("first_investigation_seq")),
            stamp_seq=_nonneg_int(value.get("stamp_seq")),
            audit_tickets=_decode_tickets(value.get("audit_tickets")),
            dispatches=_decode_dispatches(value.get("dispatches")),
            modes=modes if isinstance(modes, dict) else {},
            is_legacy=bool(value.get("is_legacy", False)),
        )


def _opt_int(value: object) -> int | None:
    """A plain non-negative int, or None. Bools reject (bool ⊂ int)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_plain_int(value: object) -> bool:
    """A real int, never a bool (bool ⊂ int, and a budget of ``True`` is a bug, not a cap of 1)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _nonneg_int(value: object) -> int:
    """A plain non-negative int, or 0. Bools and negatives collapse to 0."""
    return value if (isinstance(value, int) and not isinstance(value, bool) and value >= 0) else 0


def _decode_tickets(value: object) -> tuple[dict, ...]:
    """Decode the stored audit tickets, dropping any entry that is not a well-formed ticket.

    A ticket must carry a string ``nonce`` and an int ``seq``; a malformed one is discarded rather
    than tolerated, because a ticket whose nonce cannot be matched is not a ticket for anything."""
    if not isinstance(value, list):
        return ()
    out: list[dict] = []
    for t in value:
        if not isinstance(t, dict):
            continue
        nonce, seq = t.get("nonce"), t.get("seq")
        if not isinstance(nonce, str) or not nonce:
            continue
        if isinstance(seq, bool) or not isinstance(seq, int):
            continue
        ticket = {"nonce": nonce, "seq": seq, "consumed": bool(t.get("consumed", False))}
        if isinstance(t.get("actor"), dict):
            ticket["actor"] = t["actor"]
        out.append(ticket)
    return tuple(out)


def _decode_dispatches(value: object) -> tuple[dict, ...]:
    """Decode the stored dispatch attribution records, dropping malformed entries."""
    if not isinstance(value, list):
        return ()
    out: list[dict] = []
    for d in value:
        if not isinstance(d, dict) or not isinstance(d.get("actor"), dict):
            continue
        rec = {"seq": d["seq"] if isinstance(d.get("seq"), int)
               and not isinstance(d.get("seq"), bool) else 0,
               "actor": d["actor"]}
        if isinstance(d.get("claim_id"), str):
            rec["claim_id"] = d["claim_id"]
        out.append(rec)
    return tuple(out)
