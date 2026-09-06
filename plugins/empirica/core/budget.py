#!/usr/bin/env python3
"""Pure scope-derived pass/spawn budget — host-neutral, no I/O, no clock (ADR-30).

The a-priori ``max_passes=8`` was an ungrounded constant that did not scale with a run's scope and,
worse, was burned by idle waits (``application/service.py`` counted a pass on every stop attempt).
This module derives a *working* budget from the actual argument: the number of open gating claims —
the investigative Goals the run still owes evidence for. It stays a heuristic bound UNDER an
a-priori ceiling, never a substitute for the convergence decision (that remains in
``core/convergence.py``, which this module must never touch).

A "gating claim" here is a Goal node on the SupportedBy path from the root whose ``kind`` is one of
the externally-evidenced kinds (``needs-data`` → a citation, ``needs-experiment`` → a spike) and
that is not refuted. The graph walk is NOT reimplemented here — ``core.claims.gating_goals`` already
prunes InContextOf and discarded subtrees, so this counts the Goals it returns and filters by kind.
Everything here is a pure function of an already-normalised graph dict; it is fully unit-testable.
"""
from . import claims

# One mandatory audit round plus one finalize/convergence check: even a zero-claim run needs at
# least these two stops to reach a verdict, so the working cap reserves them on top of the claims.
PASS_RESERVE = 2
# One spawn reserved for the independent auditor that every run must run before it may converge.
AUDIT_RESERVE = 1
# The a-priori ceiling: a floor of 8 (the legacy default) that scales up with the seeded scope.
CEILING_FLOOR = 8
CEILING_SCOPE_MULTIPLIER = 3

# The claim kinds that gate on EXTERNAL evidence — each needs a dispatched worker (research or a
# spike) to resolve. `needs-decision`/`needs-budget` are human residuals, not investigative work,
# so they do not enlarge the working budget.
_GATING_KINDS = frozenset({"needs-data", "needs-experiment"})


def _refuted_prune_oracle(_nid: str, purpose: str) -> bool:
    """Evidence oracle for the budget count: honour a node's STRUCTURAL refutation so
    ``claims.gating_goals`` prunes the node AND its whole subtree, while approving NOTHING (a budget
    count must never adjudicate a claim). ``claims.state_of`` consults the refute verdict only for a
    node already carrying ``refuted_by``, so returning ``True`` for "refute" discards exactly the
    refuted subtrees; "approve" reads ``False``, so no node can reach APPROVED here."""
    return purpose == "refute"


def open_gating_claim_count(graph: dict) -> int:
    """The number of open, externally-evidenced gating Goals in ``graph`` (see module docstring).

    Delegates the SupportedBy walk to ``claims.gating_goals`` with a prune-only evidence oracle: it
    honours each node's structural ``refuted_by`` so a refuted node AND its descendants drop from the
    walk (they are not investigative work the run still owes), while approving nothing — so this stays
    a pure structural read that cannot move a claim across θ. Of the survivors it keeps only Goals of
    a gating ``kind``. ``theta`` cannot approve anything under this oracle, so any value works; 0.0 is
    passed for definiteness. Bounded (a subset of the graph's Goals) and monotone in scope.
    """
    nodes = graph["nodes"]
    return sum(1 for nid in claims.gating_goals(graph, 0.0, _refuted_prune_oracle)
               if nodes[nid].get("kind") in _GATING_KINDS)


def derive(graph: dict, ceiling: int) -> tuple[int, int]:
    """The scope-derived ``(working_passes, working_spawns)`` for ``graph`` under ``ceiling``.

    ``working_passes = min(ceiling, open_claims + PASS_RESERVE)`` — bounded by the a-priori ceiling
    so a pathological graph cannot inflate the pass budget without limit. ``working_spawns =
    open_claims + AUDIT_RESERVE`` — one dispatched worker per open externally-evidenced claim plus
    the mandatory auditor. Monotone non-decreasing in the open-claim count.
    """
    open_claims = open_gating_claim_count(graph)
    working_passes = min(ceiling, open_claims + PASS_RESERVE)
    working_spawns = open_claims + AUDIT_RESERVE
    return (working_passes, working_spawns)


def ceiling_for(seed_open_claims: int) -> int:
    """The a-priori pass ceiling for a run seeded with ``seed_open_claims`` gating claims: the
    legacy floor of 8, scaled up (never down) by the scope multiplier. Always ``>= CEILING_FLOOR``."""
    return max(CEILING_FLOOR, CEILING_SCOPE_MULTIPLIER * seed_open_claims)
