#!/usr/bin/env python3
"""Pure claim-graph state derivation — the anti-forgery core, host-neutral (ADR-30).

This is the extraction of the DERIVED-STATE section of `hooks/claimgraph.py` into a package that
touches no filesystem, no host event, and no exit code. It operates on an ALREADY-NORMALISED graph
dict (the shape `claimgraph.normalise` produces) plus an injected `evidence_ok(node_id, purpose)
-> bool` oracle. Parsing/validation/persistence (`load`, `normalise`, `save`) stay in the hook: a
raw graph is turned into a normalised dict, `None`, or `CORRUPT` by the adapter before it reaches
here.

THE LOAD-BEARING PROPERTY is preserved exactly: a node's terminal state is DERIVED from
(confidence, evidence validity, residual tag, refutation) on every read — there is no persisted
`state` field, so writing `"state": "approved"` into a node is inert. That is what makes "grade
yourself by typing a number" impossible rather than merely discouraged, and it is the single
property to protect in any change here.

Expected normalised node shape (per id): {"type", "text", "kind", "confidence": float in [0,1],
"blocked": tag|None, "evidence": [ids], "refuted_by": str|None}. The graph is
{"root": id, "nodes": {id: node}, "edges": [{"from","to","type"}]}.
"""
import hashlib

CORRUPT = "__corrupt__"  # the sentinel a normaliser returns for an unusable graph → fail CLOSED

# The CLOSED set of residual tags that legitimately stop gating (ADR-9/17). A tag outside this set
# is not honoured upstream (normalisation drops it), so a claim cannot invent its own "done" tag.
VALID_BLOCKED_TAGS = frozenset({"needs-decision", "needs-data", "needs-experiment", "needs-budget"})

STATE_APPROVED = "approved"
STATE_BLOCKED = "blocked"
STATE_DISCARDED = "discarded"
STATE_OPEN = "open"
TERMINAL_STATES = frozenset({STATE_APPROVED, STATE_BLOCKED, STATE_DISCARDED})


def state_of(graph: dict, nid: str, theta: float, evidence_ok=None) -> str:
    """A node's terminal state, DERIVED on every read — never read from the file.

    `evidence_ok(node_id, purpose) -> bool` is the two-fold evidence verdict, injected; `purpose`
    is "approve" (does the claim have the folds it owes?) or "refute" (does a validating refutation
    exist?). When it is absent — a bare structural read — nothing can be approved and nothing can be
    discarded, so an unwired gate fails CLOSED rather than waving everything through.
    """
    node = graph["nodes"][nid]
    if node["refuted_by"] and evidence_ok is not None and evidence_ok(nid, "refute"):
        return STATE_DISCARDED
    if node["blocked"]:
        return STATE_BLOCKED
    if node["confidence"] >= theta and evidence_ok is not None and evidence_ok(nid, "approve"):
        return STATE_APPROVED
    return STATE_OPEN


def _children(graph: dict) -> dict[str, list[str]]:
    """SupportedBy adjacency: the argument's load path. InContextOf attaches context, which is not
    a claim to adjudicate, so it does not extend the gated path."""
    out: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        if edge["type"] == "SupportedBy":
            out.setdefault(edge["from"], []).append(edge["to"])
    return out


def root_is_refuted(graph: dict, theta: float, evidence_ok=None) -> bool:
    """Was the run's TOP goal — the intent itself — refuted? This is NOT convergence: refuting the
    intent is a residual for the human, never a green run. Treating "nothing pending" after a root
    refutation as converged was a real bypass (one citation prunes the whole tree)."""
    return state_of(graph, graph["root"], theta, evidence_ok) == STATE_DISCARDED


def gating_goals(graph: dict, theta: float, evidence_ok=None) -> list[str]:
    """Goals on the path to the top Goal, with discarded subtrees PRUNED (ADR-20 P3).

    Iterative with a visited set, so a cyclic graph terminates instead of blowing the stack. When
    the ROOT is discarded this returns [] — every claim is pruned — so an empty result does NOT mean
    "converged"; callers must check `root_is_refuted` first.
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
        if state_of(graph, nid, theta, evidence_ok) == STATE_DISCARDED:
            continue  # prune the refuted node AND everything it was supporting
        if graph["nodes"][nid]["type"] == "Goal":
            reached.append(nid)
        stack.extend(children.get(nid, []))
    return reached


def pending(graph: dict, theta: float, evidence_ok=None) -> list[str]:
    """Goals still in the loop: on the path to the goal and not yet terminal."""
    return [nid for nid in gating_goals(graph, theta, evidence_ok)
            if state_of(graph, nid, theta, evidence_ok) == STATE_OPEN]


def blocked_residuals(graph: dict, theta: float, evidence_ok=None) -> list[str]:
    """Goals surfaced to the human. They stop gating but they are NOT convergence — the core
    reports `converged=False` when any exist (ADR-17: never fabricate green)."""
    return [nid for nid in gating_goals(graph, theta, evidence_ok)
            if state_of(graph, nid, theta, evidence_ok) == STATE_BLOCKED]


def converged(graph: dict, theta: float, evidence_ok=None) -> bool:
    """Fixed point ⇔ every Goal on the path to the top Goal is terminal AND the root was not
    refuted (ADR-20 P7). This is convergence of the CLAIM GRAPH only; a run may still not report
    `converged` until the independent audit passes, which the adjudicator checks separately."""
    if root_is_refuted(graph, theta, evidence_ok):
        return False
    return not pending(graph, theta, evidence_ok)


def argument_digest(graph: dict) -> str:
    """sha256 over the SHAPE of the argument: its root, its nodes' gating attributes, and its
    SupportedBy edges. An audit verdict records this so reviewed-ness binds to the argument the
    auditor actually walked (ADR-27) — per-claim digests alone cannot see a claim LEAVING the gated
    set (detach a blocking claim and every survivor's digest is unchanged).

    Includes `blocked` and `kind` (both change whether/how a claim gates) and `refuted_by` (a
    discard prunes a subtree); EXCLUDES `confidence` (it moves constantly and is covered per claim
    by the state derivation) and InContextOf edges (context is not a claim to adjudicate).
    """
    h = hashlib.sha256()
    h.update(graph["root"].encode("utf-8"))
    h.update(b"\0")
    for nid in sorted(graph["nodes"]):
        node = graph["nodes"][nid]
        h.update(nid.encode("utf-8"))
        for field_name in ("type", "kind", "blocked", "refuted_by"):
            value = node.get(field_name)
            h.update(b"\0" if value is None else str(value).encode("utf-8"))
            h.update(b"\0")
    h.update(b"edges\0")
    for edge in sorted((e["from"], e["to"]) for e in graph["edges"]
                       if e["type"] == "SupportedBy"):
        h.update(f"{edge[0]}>{edge[1]}".encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()
