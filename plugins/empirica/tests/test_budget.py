#!/usr/bin/env python3
"""Focused suite for the pure scope-derived pass/spawn budget (``core/budget.py``, the budget fix).

Run: python3 plugins/empirica/tests/test_budget.py   (stdlib only, no pytest dependency)
Exit 0 = all pass; 1 = at least one failed.

``core/budget`` is a pure function of an already-normalised graph: it counts the OPEN, externally-
evidenced gating Goals (kind in {needs-data, needs-experiment}, not refuted) via ``core.claims`` and
derives a working pass/spawn budget under an a-priori ceiling. These pin the refuting observations
for change A: ``derive`` is monotone in the claim count and never exceeds the ceiling, and
``ceiling_for`` never drops below the legacy floor of 8.
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
PLUGIN = HERE.parent  # plugins/empirica — makes `core` importable as a package
sys.path.insert(0, str(PLUGIN))

from core import budget  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# --- graph builders (the normalised node shape core.claims reads) ------------


def _node(kind="needs-data", refuted_by=None):
    return {"type": "Goal", "text": "t", "kind": kind, "confidence": 0.0,
            "blocked": None, "evidence": [], "refuted_by": refuted_by}


def graph_with_gating(n: int, kind="needs-data"):
    """A graph whose SupportedBy path holds exactly ``n`` open gating Goals of ``kind`` (n >= 1):
    a root G0 plus n-1 children hung under it."""
    nodes = {"G0": _node(kind)}
    edges = []
    for i in range(1, n):
        nodes[f"G{i}"] = _node(kind)
        edges.append({"from": "G0", "to": f"G{i}", "type": "SupportedBy"})
    return {"root": "G0", "nodes": nodes, "edges": edges}


def graph_zero_gating():
    """A one-node graph whose root is NOT an externally-evidenced kind, so nothing gates."""
    return {"root": "G0", "nodes": {"G0": _node(kind=None)}, "edges": []}


# --- open_gating_claim_count -------------------------------------------------


def test_count_matches_number_of_gating_goals():
    ok = budget.open_gating_claim_count(graph_zero_gating()) == 0
    for n in range(1, 6):
        ok = ok and budget.open_gating_claim_count(graph_with_gating(n)) == n
    check("BU1 open_gating_claim_count returns the number of open gating Goals on the path", ok)


def test_non_external_kind_is_not_gating():
    check("BU2 a residual kind (needs-decision) does not enlarge the working budget",
          budget.open_gating_claim_count(graph_with_gating(3, kind="needs-decision")) == 0,
          f"got {budget.open_gating_claim_count(graph_with_gating(3, kind='needs-decision'))}")


def test_refuted_claim_is_not_counted():
    g = {"root": "G0",
         "nodes": {"G0": _node("needs-data"), "G1": _node("needs-data", refuted_by="r1")},
         "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"}]}
    check("BU3 a refuted gating claim is excluded from the open count",
          budget.open_gating_claim_count(g) == 1, f"got {budget.open_gating_claim_count(g)}")


def test_refuted_subtree_is_pruned_not_counted():
    # A refuted parent AND its gating descendants must both drop from the count — once the parent is
    # refuted its subtree is no longer work the run owes (the earlier None-oracle read left the
    # subtree unpruned and counted the child, which the docstring wrongly claimed it pruned).
    g = {"root": "G0",
         "nodes": {"G0": _node("needs-data"),
                   "G1": _node("needs-data", refuted_by="r1"),
                   "G2": _node("needs-data")},
         "edges": [{"from": "G0", "to": "G1", "type": "SupportedBy"},
                   {"from": "G1", "to": "G2", "type": "SupportedBy"}]}
    check("BU3b a refuted node AND its descendant gating claims are excluded (subtree pruned)",
          budget.open_gating_claim_count(g) == 1, f"got {budget.open_gating_claim_count(g)}")


# --- derive: monotone + bounded by the ceiling (refuting obs #5) -------------


def test_derive_is_monotone_and_bounded_by_ceiling():
    ceiling = 8
    prev_passes = prev_spawns = -1
    monotone = bounded = True
    for n in range(0, 15):
        g = graph_zero_gating() if n == 0 else graph_with_gating(n)
        working_passes, working_spawns = budget.derive(g, ceiling)
        monotone = monotone and working_passes >= prev_passes and working_spawns >= prev_spawns
        bounded = bounded and working_passes <= ceiling
        prev_passes, prev_spawns = working_passes, working_spawns
    check("BU4 derive's working_passes/working_spawns are monotone non-decreasing in claim count",
          monotone)
    check("BU5 derive's working_passes never exceeds the ceiling", bounded)


def test_derive_exact_values_and_reserves():
    check("BU6 a zero-claim run reserves exactly PASS_RESERVE passes and AUDIT_RESERVE spawns",
          budget.derive(graph_zero_gating(), 8) == (budget.PASS_RESERVE, budget.AUDIT_RESERVE),
          f"got {budget.derive(graph_zero_gating(), 8)}")
    check("BU7 one open claim derives (1 + PASS_RESERVE, 1 + AUDIT_RESERVE) under a slack ceiling",
          budget.derive(graph_with_gating(1), 8)
          == (1 + budget.PASS_RESERVE, 1 + budget.AUDIT_RESERVE),
          f"got {budget.derive(graph_with_gating(1), 8)}")
    # A pathological graph cannot inflate the pass budget past the ceiling (spawns are unbounded).
    passes, spawns = budget.derive(graph_with_gating(20), 8)
    check("BU8 a large graph clamps working_passes to the ceiling (spawns scale with claims)",
          passes == 8 and spawns == 20 + budget.AUDIT_RESERVE, f"got ({passes}, {spawns})")


# --- ceiling_for: floor + scaling (refuting obs #5) --------------------------


def test_ceiling_for_never_below_floor_and_scales():
    check("BU9 ceiling_for is always >= the legacy floor of 8 (>= CEILING_FLOOR)",
          all(budget.ceiling_for(n) >= budget.CEILING_FLOOR for n in range(0, 50))
          and budget.CEILING_FLOOR >= 8, f"floor={budget.CEILING_FLOOR}")
    check("BU10 ceiling_for scales above the floor with scope (3 claims -> 9 > 8)",
          budget.ceiling_for(0) == 8 and budget.ceiling_for(1) == 8
          and budget.ceiling_for(3) == 9,
          f"got {budget.ceiling_for(0)},{budget.ceiling_for(1)},{budget.ceiling_for(3)}")
    check("BU11 ceiling_for is monotone non-decreasing in the seed claim count",
          all(budget.ceiling_for(n + 1) >= budget.ceiling_for(n) for n in range(0, 50)))


def main() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        mark = "ok  " if ok else "FAIL"
        line = f"  [{mark}] {name}"
        if not ok and detail:
            line += f"  — {detail}"
        print(line)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
