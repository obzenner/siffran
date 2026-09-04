"""The run's *operational state* — the single CAS-guarded document behind :class:`RunRepository`.

This is the operational plane of ADR-31: the small mutable record a run updates as it progresses —
its lifecycle status, its pass budget, and, load-bearingly, the *pointer* to the claim graph that is
current for this run. The knowledge plane (the graph and evidence themselves) lives in the
append-only :class:`ArtifactRepository`; this document only ever holds the content address of the
graph that is current, never the graph.

Why a pointer and not the graph inline: an artifact append is immutable and content-addressed, so a
graph update is "append the new graph, then compare-and-set this pointer to it" (see
``service._update_graph``). The CAS is what makes the swap atomic — a losing writer never advances
the pointer, so the pointer can only ever name a graph that was actually appended, never an orphan.

The stored form is a plain JSON object (the filesystem adapter requires it); this dataclass is the
typed view the service manipulates. ``revision`` here is the *wire* revision — a monotone integer a
caller sees in a response — and is distinct from the opaque storage :class:`~core.records.Revision`
token the repository mints for CAS.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .wire import STATUS_ACTIVE

SCHEMA = "empirica.run/1"
DEFAULT_MAX_PASSES = 8  # mirrors the hooks' DEFAULT_MAX_PASSES (manifest.py); a positive cap.
DEFAULT_THETA = 0.8  # mirrors EMPIRICA_THETA's default; the confidence threshold to approve a claim.

# The lifecycle statuses this document may hold. Terminal statuses read as "not active", which is
# what tells the generation allocator to open a fresh generation next time (ADR-31).
TERMINAL_STATUSES = frozenset(
    {"converged", "stopped_residual", "stopped_frozen", "stopped_budget"}
)


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
    max_spawns: int | None = None
    frozen_claims: tuple[str, ...] | None = None
    route_verdict: tuple[str, str] = ("ok", "")
    modes: dict = field(default_factory=dict)
    is_legacy: bool = False

    @classmethod
    def new(cls, *, goal: str, max_passes: int, max_spawns: int | None,
            theta: float, modes: dict | None) -> OperationalState:
        """A fresh active run at revision 0. Starts clean: no graph pointer, zero passes — a new
        generation is empty by construction (ADR-31)."""
        return cls(status=STATUS_ACTIVE, revision=0, passes=0, max_passes=max_passes,
                   theta=theta, goal=goal, max_spawns=max_spawns, modes=modes or {})

    def evolve(self, **changes: object) -> OperationalState:
        """A copy with ``changes`` applied and the wire revision advanced by one. Every state write
        goes through here so the revision a caller sees always tracks the number of writes."""
        return replace(self, revision=self.revision + 1, **changes)  # type: ignore[arg-type]

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

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
            "max_spawns": self.max_spawns,
            "frozen_claims": (list(self.frozen_claims)
                              if self.frozen_claims is not None else None),
            "route_verdict": list(self.route_verdict),
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
        if not (isinstance(status, str) and isinstance(revision, int)
                and isinstance(passes, int) and isinstance(max_passes, int)
                and isinstance(goal, str)) or isinstance(revision, bool):
            return None
        frozen = value.get("frozen_claims")
        frozen_claims = tuple(frozen) if isinstance(frozen, list) else None
        route = value.get("route_verdict")
        route_verdict = (tuple(route) if isinstance(route, list) and len(route) == 2
                         else ("ok", ""))
        pointer = value.get("claim_graph_artifact_id")
        pointer = pointer if isinstance(pointer, str) else None
        modes = value.get("modes")
        return cls(
            status=status,
            revision=revision,
            passes=passes,
            max_passes=max_passes,
            theta=float(theta),
            goal=goal,
            claim_graph_artifact_id=pointer,
            max_spawns=value.get("max_spawns") if isinstance(value.get("max_spawns"), int) else None,
            frozen_claims=frozen_claims,
            route_verdict=route_verdict,  # type: ignore[arg-type]
            modes=modes if isinstance(modes, dict) else {},
            is_legacy=bool(value.get("is_legacy", False)),
        )
