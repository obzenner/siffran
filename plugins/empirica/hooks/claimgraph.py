#!/usr/bin/env python3
"""The claim graph — a GSN assurance argument with in-toto evidence leaves (ADR-22).

This module replaces the markdown `## Unknowns` checklist the Stop gate used to parse.
The substrate change is not cosmetic: a document model let a model *type* its own verdict,
and that is precisely the hole ADR-20 exists to close.

THE LOAD-BEARING PROPERTY — state is DERIVED, never read.
    There is deliberately NO persisted `state` field. A node's terminal state is computed
    from (confidence, evidence validity, residual tag, refutation) on every read. Writing
    `"state": "approved"` into the file is INERT. This is what makes "grade yourself by
    typing a number" impossible rather than merely discouraged, and it is the single
    property to protect in any future change here. Proven by the spike at
    `.claude/spike-claimgraph` (36/36 falsification attacks repelled), including the
    headline attack: self-attested confidences with no evidence do not converge.

Terminal states (ADR-20 P3/P7):
    approved  — confidence ≥ θ AND the required evidence folds validate
    blocked   — a residual tag from the CLOSED tag set (surfaced to the human, ADR-9)
    discarded — evidence REFUTES the claim: the node and its sub-goals are pruned. A
                discard requires a validating refutation ref, because "discard everything"
                would otherwise be the cheapest possible bypass of the whole gate.
    open      — anything else; the claim is still in the loop

Fail directions match manifest.py exactly, so the gate keeps its ADR-19 matrix:
    load() → None      — NO graph file: the fail-OPEN signal ("not our run / nothing yet")
    load() → CORRUPT   — a graph EXISTS but is unusable: fail CLOSED
    load() → dict      — a structurally valid graph
A structurally invalid graph is CORRUPT rather than "unconverged" on purpose: a malformed
argument is not a weak argument, and an active run whose state cannot be read is exactly
when the gate should bite.

HONEST LIMITATION (do not overclaim — ADR-21's no-overclaim rule): the graph can only gate
claims that are IN it. An agent that never writes a claim, or detaches one from the root,
silences it; off-path nodes do not gate, by design (ADR-20 P7 gates "every claim on the path
to the goal"). No hook can detect an unwritten claim. The mitigations are the auditor
(ADR-20 P6), which walks the graph against the intent, and route-before-investigate (P1),
which fixes the claim set before evidence gathering so later shrinkage is visible. The graph
alone does not close this.

Loaded by sibling hooks via spec_from_file_location, so it uses no package imports; file-io
is reused from atomicio.py — one hardened writer (ADR-19).
"""
import importlib.util
import json
import math
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_io = _load("atomicio")
_actors = _load("actors")

DEFAULT_THETA = 0.8
CORRUPT = "__corrupt__"  # sentinel: a graph exists but cannot be trusted → fail CLOSED

# --- GSN element vocabulary --------------------------------------------------
# Verified against the GSN Community Standard v3 (SCSC-141C, May 2021, SCSC Assurance Case
# Working Group, CC BY 4.0) — a live read of the standard, not recall.
#
# empirica uses GSN's element and relationship VOCABULARY as its node/edge schema. It does NOT
# implement the OMG SACM v2.3 metamodel, produces no SACM-conformant XMI, and claims
# conformance to no SACM compliance point (SACM v2.3 §2 defines five: Argumentation, Artifact,
# Assurance Case, Terminology, UML Profile). If XMI export is ever built it would target the
# Argumentation Model compliance point only (§2.2), which SACM defines as independent of the
# Artifact/Terminology subpackages and which is the point GSN tools conventionally map onto
# (SACM v2.3 Annex A). This resolves ADR-22's deferred open item: vocabulary-only.
NODE_TYPES = frozenset(
    {"Goal", "Strategy", "Solution", "Context", "Assumption", "Justification"}
)
# GSN relationships (§1:2.1.3): SupportedBy carries inferential/evidential relationships,
# InContextOf declares contextual ones. "Solution" is the current normative name for an
# evidence leaf (v3 did not rename it to "Evidence").
EDGE_TYPES = frozenset({"SupportedBy", "InContextOf"})
# Permitted (source type → target type) pairs, transcribed from the standard's own normative
# lists rather than inferred:
#   "Permitted 'supported by' connections are: goal-to-goal, goal-to-strategy,
#    goal-to-solution, strategy-to-goal."
#   "Permitted 'in context of' connections are: goal-to-context, goal-to-assumption,
#    goal-to-justification, strategy-to-context, strategy-to-assumption,
#    strategy-to-justification."
# An illegal edge is a malformed argument, so the graph is rejected rather than gated on.
# Note strategy-to-solution is NOT permitted: a Strategy is supported by Goals, which are in
# turn supported by Solutions. A Goal is likewise never *supported by* Context/Assumption/
# Justification — those attach InContextOf.
LEGAL_SUPPORTED_BY = frozenset({
    ("Goal", "Goal"), ("Goal", "Strategy"), ("Goal", "Solution"),
    ("Strategy", "Goal"),
})
LEGAL_IN_CONTEXT_OF = frozenset({
    ("Goal", "Context"), ("Goal", "Assumption"), ("Goal", "Justification"),
    ("Strategy", "Context"), ("Strategy", "Assumption"), ("Strategy", "Justification"),
})

# A claim's kind decides which evidence folds it owes (ADR-20 P3): every claim owes Fold 1
# (research); only `needs-experiment` additionally owes Fold 2 (a real spike verdict).
CLAIM_KINDS = frozenset({"needs-data", "needs-experiment", "needs-decision"})
NEEDS_SPIKE = "needs-experiment"

# The CLOSED set of residual tags that legitimately stop gating (ADR-9/17), carried over
# unchanged from the markdown gate. A tag outside this set is NOT honoured — the claim stays
# open and blocks, so a model cannot invent `blocked: made-up` to declare its own work done.
VALID_BLOCKED_TAGS = frozenset(
    {"needs-decision", "needs-data", "needs-experiment", "needs-budget"}
)

STATE_APPROVED = "approved"
STATE_BLOCKED = "blocked"
STATE_DISCARDED = "discarded"
STATE_OPEN = "open"
TERMINAL_STATES = frozenset({STATE_APPROVED, STATE_BLOCKED, STATE_DISCARDED})


def _raise_non_finite(_c):
    raise ValueError("non-finite JSON constant")


def default_graph_path(run_dir: Path) -> Path:
    """The claim graph's only home: `claims.json` inside the run directory (ADR-14/19).

    It is transient run memory — never a repository deliverable, never at the repo root.
    That mistake is what the whole ADR-22 substrate change exists to prevent.
    """
    return run_dir / "claims.json"


# --- load / normalise -------------------------------------------------------


def load(path: Path):
    """None (no file), CORRUPT (present but unusable), or a normalised graph.

    parse_constant rejects JSON Infinity/NaN so a crafted graph cannot inject a non-finite
    confidence — the same guard manifest.py applies to its pass counters.
    """
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_raise_non_finite)
    except (OSError, json.JSONDecodeError, ValueError):
        return CORRUPT
    return normalise(raw)


def normalise(raw):
    """Structural validation → a normalised graph, or CORRUPT.

    Anything that would make gating AMBIGUOUS is corruption: no top Goal means there is no
    "path to the goal" to gate against, and an unknown node/edge type means we cannot say
    what the argument claims.
    """
    if not isinstance(raw, dict):
        return CORRUPT
    nodes_in = raw.get("nodes")
    edges_in = raw.get("edges", [])
    root = raw.get("root")
    if not isinstance(nodes_in, dict) or not isinstance(edges_in, list):
        return CORRUPT
    if not isinstance(root, str) or root not in nodes_in:
        return CORRUPT

    nodes: dict[str, dict] = {}
    for nid, node in nodes_in.items():
        if not isinstance(nid, str) or not isinstance(node, dict):
            return CORRUPT
        if node.get("type") not in NODE_TYPES:
            return CORRUPT
        nodes[nid] = {
            "type": node["type"],
            "text": node["text"] if isinstance(node.get("text"), str) else "",
            "kind": node["kind"] if node.get("kind") in CLAIM_KINDS else None,
            "confidence": coerce_confidence(node.get("confidence")),
            "blocked": (node["blocked"]
                        if node.get("blocked") in VALID_BLOCKED_TAGS else None),
            # Ids of evidence leaves bound to this claim. Presence is checked here; whether
            # the evidence actually VALIDATES is evidence.py's verdict (Fold 1 / Fold 2),
            # injected into state_of as `evidence_ok`.
            "evidence": ([e for e in node["evidence"] if isinstance(e, str)]
                         if isinstance(node.get("evidence"), list) else []),
            "refuted_by": (node["refuted_by"]
                           if isinstance(node.get("refuted_by"), str) else None),
            # ADR-24 §1: the actor this claim is ASSIGNED to, if any. Purely additive — a node
            # without one resolves exactly as it always has (session default / agent frontmatter),
            # so every existing graph keeps working unchanged. A malformed actor normalises to
            # None rather than corrupting the graph: an assignment is a routing preference, and
            # losing a preference must never make an otherwise-valid argument unreadable.
            #
            # Assignment is not attribution. This field says who SHOULD resolve the claim; who
            # actually did is recorded on the evidence, by the dispatcher (see actors.py).
            "actor": _actors.normalise(node.get("actor")),
        }
        # NOTE: a `state` key present in the file is deliberately NOT read. See module docs.

    edges: list[dict] = []
    for edge in edges_in:
        if not isinstance(edge, dict):
            return CORRUPT
        src, dst, etype = edge.get("from"), edge.get("to"), edge.get("type")
        if src not in nodes or dst not in nodes or etype not in EDGE_TYPES:
            return CORRUPT
        pair = (nodes[src]["type"], nodes[dst]["type"])
        legal = LEGAL_SUPPORTED_BY if etype == "SupportedBy" else LEGAL_IN_CONTEXT_OF
        if pair not in legal:
            return CORRUPT
        edges.append({"from": src, "to": dst, "type": etype})

    graph = {"root": root, "nodes": nodes, "edges": edges}
    # GSN requires a goal structure to be a DIRECTED ACYCLIC graph: "SupportedBy relationships
    # shall not be constructed so as to directly or indirectly allow a goal to support itself."
    # A cycle is circular reasoning — a claim used as its own support — so it is a malformed
    # argument, not a weak one, and fails CLOSED. (The walkers below are independently
    # cycle-safe, so this is a correctness gate rather than a crash guard.)
    if _has_cycle(graph):
        return CORRUPT
    return graph


def _has_cycle(graph: dict) -> bool:
    """True iff the SupportedBy relation contains a cycle. Iterative three-colour DFS, so a
    deep graph cannot blow the stack while checking for one."""
    children = _children(graph)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {nid: WHITE for nid in graph["nodes"]}
    for start in graph["nodes"]:
        if colour[start] != WHITE:
            continue
        stack = [(start, iter(children.get(start, ())))]
        colour[start] = GREY
        while stack:
            node, kids = stack[-1]
            advanced = False
            for kid in kids:
                if colour[kid] == GREY:
                    return True  # back-edge → the claim supports itself, directly or not
                if colour[kid] == WHITE:
                    colour[kid] = GREY
                    stack.append((kid, iter(children.get(kid, ()))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
    return False


def coerce_confidence(value) -> float:
    """A real number in [0,1], else 0.0. Bools reject (bool ⊂ int); non-finite rejects.

    Absence of proof must never read as proof, so every malformed confidence collapses to
    0.0 — the claim stays open and blocks (the markdown gate's rule, preserved).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    v = float(value)
    return v if math.isfinite(v) and 0.0 <= v <= 1.0 else 0.0


def save(path: Path, graph: dict) -> None:
    """Persist a graph atomically under the lock (atomicio, ADR-19). Used by tooling and
    tests; the gate itself only ever READS the graph."""
    with _io.lock(path):
        _io.atomic_write_json(path, {
            "root": graph["root"],
            "nodes": graph["nodes"],
            "edges": graph["edges"],
        })


# --- derived state (the anti-forgery core) ----------------------------------


def state_of(graph: dict, nid: str, th: float, evidence_ok=None) -> str:
    """A node's terminal state, DERIVED on every read — never read from the file.

    `evidence_ok(node_id, purpose) -> bool` is evidence.py's two-fold verdict, injected;
    `purpose` is "approve" (does the claim have the folds it owes?) or "refute" (does a
    validating refutation exist?). When it is absent — a bare structural read — nothing can
    be approved and nothing can be discarded, so an unwired gate fails CLOSED rather than
    waving everything through.
    """
    node = graph["nodes"][nid]
    if node["refuted_by"] and evidence_ok is not None and evidence_ok(nid, "refute"):
        return STATE_DISCARDED
    if node["blocked"]:
        return STATE_BLOCKED
    if node["confidence"] >= th and evidence_ok is not None and evidence_ok(nid, "approve"):
        return STATE_APPROVED
    return STATE_OPEN


def _children(graph: dict) -> dict[str, list[str]]:
    """SupportedBy adjacency: the argument's load path. InContextOf attaches context, which
    is not a claim to adjudicate, so it does not extend the gated path."""
    out: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        if edge["type"] == "SupportedBy":
            out.setdefault(edge["from"], []).append(edge["to"])
    return out


def root_is_refuted(graph: dict, th: float, evidence_ok=None) -> bool:
    """Was the run's TOP goal — the intent itself — refuted?

    This is its own question because it is NOT convergence, and treating it as such was a real
    bypass: refute the root with one citation, the whole tree prunes, `pending` is empty, and
    "nothing pending" reads as "converged" with zero work done. Found by adversarial review.

    Refuting the intent is a legitimate and valuable outcome — "this cannot be built as asked"
    is real knowledge — but it is a RESIDUAL for the human, never a green run. The gate reports
    it as non-converged (see convergence_gate).
    """
    return state_of(graph, graph["root"], th, evidence_ok) == STATE_DISCARDED


def gating_goals(graph: dict, th: float, evidence_ok=None) -> list[str]:
    """Goals on the path to the top Goal, with discarded subtrees PRUNED (ADR-20 P3).

    Iterative with a visited set, so a cyclic graph terminates instead of blowing the stack
    (a hostile or merely buggy graph must not crash the gate). Off-path nodes are excluded:
    ADR-20 P7 gates "every claim on the path to the goal" — see the module's honest
    limitation note about what that does and does not catch.

    NOTE: when the ROOT is discarded this returns [] — every claim is pruned. An empty result
    therefore does NOT mean "converged"; callers must check root_is_refuted() first. `converged`
    below does exactly that.
    """
    children = _children(graph)
    reached: list[str] = []
    seen: set[str] = set()
    stack = [graph["root"]]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        if state_of(graph, nid, th, evidence_ok) == STATE_DISCARDED:
            continue  # prune the refuted node AND everything it was supporting
        if graph["nodes"][nid]["type"] == "Goal":
            reached.append(nid)
        stack.extend(children.get(nid, []))
    return reached


def pending(graph: dict, th: float, evidence_ok=None) -> list[str]:
    """Goals still in the loop: on the path to the goal and not yet terminal."""
    return [nid for nid in gating_goals(graph, th, evidence_ok)
            if state_of(graph, nid, th, evidence_ok) == STATE_OPEN]


def blocked_residuals(graph: dict, th: float, evidence_ok=None) -> list[str]:
    """Goals surfaced to the human. They stop gating but they are NOT convergence — the gate
    reports `converged: false` when any exist (ADR-17: never fabricate green)."""
    return [nid for nid in gating_goals(graph, th, evidence_ok)
            if state_of(graph, nid, th, evidence_ok) == STATE_BLOCKED]


def converged(graph: dict, th: float, evidence_ok=None) -> bool:
    """Fixed point ⇔ every Goal on the path to the top Goal is terminal (ADR-20 P7).

    A REFUTED ROOT is explicitly not convergence, even though it leaves nothing pending: the
    run disproved its own intent, which is a residual for the human, not a green result. Without
    this guard, one forged refutation of the top goal converged the entire run vacuously.

    This is convergence of the CLAIM GRAPH only. A run may still not report `converged`:
    P7 also requires the independent audit to have passed, which the Stop gate checks
    separately — a claim graph cannot audit itself.
    """
    if root_is_refuted(graph, th, evidence_ok):
        return False
    return not pending(graph, th, evidence_ok)
