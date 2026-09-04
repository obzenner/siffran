"""The run's *knowledge plane* — claim graphs, evidence, and audit records as immutable artifacts.

Everything a run learns is an append-only, content-addressed :class:`~core.records.Artifact` in the
:class:`ArtifactRepository` (ADR-31). This module owns the *encoding* of those artifacts and the
pure derivations the adjudicator needs from them:

* one artifact ``kind`` per fact — ``graph`` (the current claim graph), ``evidence`` (a recorded
  two-fold verdict for a claim), ``audit_ticket`` (an auditor spawn), ``audit_verdict`` (the
  auditor's result), ``attribution`` (the P-clash report);
* :func:`content_address` — the id every artifact is keyed on, so equal bodies share an id and an
  append is idempotent (a re-recorded fact is a no-op) and commutative (order-independent);
* :func:`canonicalize_graph` — validate the caller's graph against the shape ``core.claims`` needs,
  then re-serialise it canonically so the same argument always hashes to the same graph id;
* :func:`build_evidence_oracle` / :func:`build_audit_oracle` / :func:`build_digest_of` — turn the
  recorded artifacts into the injected verdicts ``core.convergence.adjudicate`` consumes.

Crucially this is host-neutral: the evidence verdict the host hooks derive by re-reading the working
tree (a spike's file-staleness check) is here *recorded at observation time* as an ``evidence``
artifact and simply read back — the application never touches a filesystem or a working tree.
"""
from __future__ import annotations

import hashlib
import json
import re

from core import claims

KIND_GRAPH = "graph"
KIND_EVIDENCE = "evidence"
KIND_AUDIT_TICKET = "audit_ticket"
KIND_AUDIT_VERDICT = "audit_verdict"
KIND_ATTRIBUTION = "attribution"

PURPOSE_APPROVE = "approve"
PURPOSE_REFUTE = "refute"
_PURPOSES = frozenset({PURPOSE_APPROVE, PURPOSE_REFUTE})


class InvalidGraph(Exception):
    """The caller's claim graph is not the shape ``core.claims`` can adjudicate.

    Raised by :func:`canonicalize_graph` *before* any artifact is appended or any pointer moved, so
    a rejected graph update leaves the run's storage byte-identical — the validate step of the
    graph-update transaction fails closed (ADR-31)."""


def _canonical(obj: object) -> str:
    """Deterministic JSON: sorted keys, tight separators, non-ASCII preserved. The single encoding
    used for both content addressing and artifact bodies, so identical facts hash identically."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_address(body: str) -> str:
    """The artifact id for a body: its SHA-256 hex digest. A hex digest is verbatim-safe as a Git
    tree path (ADR-31 shadow-ref adapter), and content addressing is what gives append its
    commutative-idempotent set semantics."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --- artifact envelopes ------------------------------------------------------


def _envelope(kind: str, payload: dict) -> tuple[str, str]:
    """Serialise a knowledge artifact and return ``(artifact_id, body)``. The kind is part of the
    body, so two different kinds can never collide on a shared id."""
    body = _canonical({"kind": kind, **payload})
    return content_address(body), body


def graph_artifact(canonical_graph: dict) -> tuple[str, str]:
    return _envelope(KIND_GRAPH, {"graph": canonical_graph})


def evidence_artifact(claim_id: str, purpose: str, ok: bool, reason: str) -> tuple[str, str]:
    return _envelope(KIND_EVIDENCE,
                     {"claim_id": claim_id, "purpose": purpose, "ok": ok, "reason": reason})


def audit_ticket_artifact(nonce: str) -> tuple[str, str]:
    return _envelope(KIND_AUDIT_TICKET, {"nonce": nonce})


def audit_verdict_artifact(payload: dict) -> tuple[str, str]:
    return _envelope(KIND_AUDIT_VERDICT, payload)


def attribution_artifact(report: dict) -> tuple[str, str]:
    return _envelope(KIND_ATTRIBUTION, {"report": report})


# --- graph validation / canonicalisation -------------------------------------


def canonicalize_graph(raw: object) -> dict:
    """Validate ``raw`` against the shape ``core.claims`` requires and return a canonical copy.

    The canonical copy keeps only the gating-relevant node fields (type/text/kind/confidence/
    blocked/evidence/refuted_by) in a fixed order, so two structurally identical arguments produce
    byte-identical bodies and therefore the same graph id. Raises :class:`InvalidGraph` on any
    structural violation — an invalid graph is refused, never partially stored.
    """
    if not isinstance(raw, dict):
        raise InvalidGraph("graph must be an object")
    root = raw.get("root")
    nodes = raw.get("nodes")
    edges = raw.get("edges", [])
    if not isinstance(root, str) or not root:
        raise InvalidGraph("graph.root must be a non-empty string")
    if not isinstance(nodes, dict) or not nodes:
        raise InvalidGraph("graph.nodes must be a non-empty object")
    if root not in nodes:
        raise InvalidGraph("graph.root is not among graph.nodes")
    if not isinstance(edges, list):
        raise InvalidGraph("graph.edges must be a list")

    canon_nodes: dict[str, dict] = {}
    for nid, node in nodes.items():
        if not isinstance(nid, str) or not isinstance(node, dict):
            raise InvalidGraph(f"node {nid!r} is malformed")
        confidence = node.get("confidence", 0.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise InvalidGraph(f"node {nid!r} confidence must be a number")
        confidence = max(0.0, min(1.0, float(confidence)))
        blocked = node.get("blocked")
        if blocked is not None and blocked not in claims.VALID_BLOCKED_TAGS:
            raise InvalidGraph(f"node {nid!r} has an unrecognised blocked tag: {blocked!r}")
        evidence = node.get("evidence", [])
        if not isinstance(evidence, list) or any(not isinstance(e, str) for e in evidence):
            raise InvalidGraph(f"node {nid!r} evidence must be a list of ids")
        refuted_by = node.get("refuted_by")
        if refuted_by is not None and not isinstance(refuted_by, str):
            raise InvalidGraph(f"node {nid!r} refuted_by must be a string or null")
        node_type = node.get("type")
        if not isinstance(node_type, str) or not node_type:
            raise InvalidGraph(f"node {nid!r} must have a string type")
        text = node.get("text", "")
        if not isinstance(text, str):
            raise InvalidGraph(f"node {nid!r} text must be a string")
        kind = node.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise InvalidGraph(f"node {nid!r} kind must be a string or null")
        canon_nodes[nid] = {
            "type": node_type,
            "text": text,
            "kind": kind,
            "confidence": confidence,
            "blocked": blocked,
            "evidence": list(evidence),
            "refuted_by": refuted_by,
        }

    canon_edges: list[dict] = []
    for edge in edges:
        if not isinstance(edge, dict):
            raise InvalidGraph("each edge must be an object")
        src, dst, etype = edge.get("from"), edge.get("to"), edge.get("type")
        if src not in canon_nodes or dst not in canon_nodes:
            raise InvalidGraph(f"edge references an unknown node: {src!r}->{dst!r}")
        if etype not in ("SupportedBy", "InContextOf"):
            raise InvalidGraph(f"edge has an unknown type: {etype!r}")
        canon_edges.append({"from": src, "to": dst, "type": etype})
    canon_edges.sort(key=lambda e: (e["from"], e["to"], e["type"]))

    return {"root": root, "nodes": canon_nodes, "edges": canon_edges}


# --- decoded artifact set ----------------------------------------------------


class KnowledgeError(Exception):
    """A stored knowledge artifact could not be decoded. Surfaced as ``Fault(corrupt_artifacts)``:
    the run's argument cannot be trusted, so the run must not stop (fail closed)."""


class Knowledge:
    """The decoded knowledge artifacts for one run, grouped by kind. Built from the append-only set
    the :class:`ArtifactRepository` returns; nothing here writes."""

    def __init__(self) -> None:
        self.graphs: dict[str, dict] = {}  # graph artifact id -> canonical graph
        self.evidence: list[dict] = []
        self.tickets: list[dict] = []
        self.verdicts: list[dict] = []
        self.attributions: list[dict] = []

    @classmethod
    def from_artifacts(cls, artifacts) -> Knowledge:
        """Decode a set of :class:`~core.records.Artifact`. Raises :class:`KnowledgeError` on an
        undecodable or mis-kinded body — a corrupt knowledge plane fails closed, it is not skipped."""
        k = cls()
        for art in artifacts:
            try:
                record = json.loads(art.body)
            except (json.JSONDecodeError, ValueError) as exc:
                raise KnowledgeError(f"undecodable artifact {art.artifact_id}: {exc}") from exc
            if not isinstance(record, dict):
                raise KnowledgeError(f"artifact {art.artifact_id} body is not an object")
            kind = record.get("kind")
            if kind == KIND_GRAPH:
                k.graphs[art.artifact_id] = record.get("graph")
            elif kind == KIND_EVIDENCE:
                k.evidence.append(record)
            elif kind == KIND_AUDIT_TICKET:
                k.tickets.append(record)
            elif kind == KIND_AUDIT_VERDICT:
                k.verdicts.append(record)
            elif kind == KIND_ATTRIBUTION:
                k.attributions.append(record.get("report"))
            else:
                raise KnowledgeError(f"artifact {art.artifact_id} has unknown kind {kind!r}")
        return k


# --- injected verdicts for adjudicate ----------------------------------------


def build_evidence_oracle(evidence_records: list[dict]):
    """The ``evidence(node_id, purpose) -> (ok, reason)`` oracle from recorded evidence artifacts.

    Monotone by design: a claim is approvable once an approving evidence artifact exists for it, so
    a later contradicting record cannot silently un-approve it — approval reflects evidence that was
    earned and recorded, matching the run's append-only history (ADR-20 P3).
    """
    index: dict[tuple[str, str], tuple[bool, str]] = {}
    for rec in evidence_records:
        claim_id, purpose = rec.get("claim_id"), rec.get("purpose")
        if not isinstance(claim_id, str) or purpose not in _PURPOSES:
            continue
        ok, reason = bool(rec.get("ok")), str(rec.get("reason", ""))
        key = (claim_id, purpose)
        prev = index.get(key)
        if prev is None or (ok and not prev[0]):  # a truthy verdict wins over a falsy one
            index[key] = (ok, reason)

    def evidence(node_id: str, purpose: str) -> tuple[bool, str]:
        return index.get((node_id, purpose),
                         (False, f"no recorded {purpose} evidence for {node_id}"))

    return evidence


def _select_verdict(verdicts: list[dict], tickets: list[dict]) -> dict | None:
    """Pick the audit verdict that best covers the run: prefer one whose nonce matches a recorded
    spawn and that passed, then any nonce-matching one, then the most recent by content. The
    coverage decision (``core.audit.coverage_check``) still judges it — this only disambiguates a
    set with more than one recorded verdict (the append-only store keeps them all)."""
    if not verdicts:
        return None
    ticket_nonces = {t.get("nonce") for t in tickets}
    return max(
        verdicts,
        key=lambda v: (v.get("nonce") in ticket_nonces, v.get("verdict") == "pass",
                       _canonical(v)),
    )


_SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")


def _normalise_verdict(verdict: dict | None) -> dict | None:
    """A verdict coerced to the exact shape ``core.audit.coverage_check`` indexes into, or ``None``.

    Defence in depth for the wire boundary: a stored verdict artifact (or one accepted at observe
    time) may carry an arbitrarily-shaped ``claims_reviewed`` — e.g. ``[1]`` or ``[{}]`` — and
    ``coverage_check`` indexes ``e["claim_id"]``/``entry["claim_digest"]`` on each entry, which would
    raise and escape ``handle()`` instead of failing closed. Dropping malformed entries here means a
    malformed verdict simply fails to cover the run (a Block), never crashes it (mirrors the hooks'
    ``audit._review_entry``/``read_verdict``). A non-``pass`` verdict value is preserved verbatim so
    the coverage check still reports it as a failing audit.
    """
    if not isinstance(verdict, dict):
        return None
    nonce = verdict.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        return None  # a verdict that names no spawn cannot match any ticket
    reviewed: list[dict] = []
    raw_reviewed = verdict.get("claims_reviewed")
    if isinstance(raw_reviewed, list):
        for entry in raw_reviewed:
            if not isinstance(entry, dict):
                continue
            claim_id = entry.get("claim_id")
            cd, ed = entry.get("claim_digest"), entry.get("evidence_digest")
            if (isinstance(claim_id, str) and claim_id
                    and isinstance(cd, str) and _SHA256_HEX.match(cd)
                    and isinstance(ed, str) and _SHA256_HEX.match(ed)):
                reviewed.append({"claim_id": claim_id, "claim_digest": cd, "evidence_digest": ed})
    argument = verdict.get("argument_digest")
    findings = verdict.get("findings")
    return {
        "verdict": verdict.get("verdict") if verdict.get("verdict") in ("pass", "fail") else "fail",
        "nonce": nonce,
        "argument_digest": argument if isinstance(argument, str) and _SHA256_HEX.match(argument)
        else None,
        "claims_reviewed": reviewed,
        "findings": [f for f in findings if isinstance(f, str)] if isinstance(findings, list) else [],
    }


def build_audit_oracle(tickets: list[dict], verdicts: list[dict]):
    """The ``audit(approved_digests, argument_digest) -> (ok, reason)`` oracle, delegating to the
    pure :func:`core.audit.coverage_check` over the recorded tickets and selected verdict. The
    selected verdict is normalised first, so a malformed stored verdict fails the run CLOSED (a
    Block) rather than raising through the wire boundary."""
    from core.audit import coverage_check

    verdict = _normalise_verdict(_select_verdict(verdicts, tickets))

    def audit(approved_digests: dict, argument_digest: str) -> tuple[bool, str]:
        return coverage_check(tickets, verdict, approved_digests, argument_digest)

    return audit


def claim_digest(text: str) -> str:
    """Digest of a claim's reviewable content — its text (mirrors the hooks' ``evidence.claim_digest``
    over the node text). A reworded claim gets a new digest, so an old audit verdict no longer
    covers it (ADR-25)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_digest(evidence_ids: list[str]) -> str:
    """Digest over the set of evidence artifact ids supporting a claim. Re-evidencing a claim changes
    this digest, so an audit that re-read the old citation no longer covers it (ADR-25)."""
    return hashlib.sha256("\0".join(sorted(evidence_ids)).encode("utf-8")).hexdigest()


def build_digest_of(graph: dict, approving_evidence_ids: dict[str, list[str]]):
    """The ``digest_of(node_id) -> {claim_digest, evidence_digest}`` map an approved claim must have
    been reviewed at. Computed from the current graph and the recorded evidence, so it always
    reflects the run's live state — the audit is judged against what supports the claim *now*."""
    def digest_of(node_id: str) -> dict:
        text = graph["nodes"][node_id]["text"]
        return {"claim_digest": claim_digest(text),
                "evidence_digest": evidence_digest(approving_evidence_ids.get(node_id, []))}

    return digest_of


def approving_evidence_ids(evidence_records: list[dict]) -> dict[str, list[str]]:
    """Map each claim to the ids of its approving evidence artifacts — the input to
    :func:`evidence_digest` for that claim. Recomputes each artifact id from its body so the mapping
    matches what was stored, not what a caller claimed."""
    out: dict[str, list[str]] = {}
    for rec in evidence_records:
        if rec.get("purpose") != PURPOSE_APPROVE or not rec.get("ok"):
            continue
        claim_id = rec.get("claim_id")
        if not isinstance(claim_id, str):
            continue
        art_id, _ = evidence_artifact(claim_id, PURPOSE_APPROVE, True, str(rec.get("reason", "")))
        out.setdefault(claim_id, []).append(art_id)
    return out
