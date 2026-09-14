"""Pure evidence-record classification and the two-fold verdict — host-neutral (ADR-30).

This module touches no filesystem, no host event, and no exit code.  It classifies in-toto evidence
statements into a neutral leaf-fact shape (:func:`project_leaf`) and derives the two-fold verdict a
claim owes (:func:`two_fold_verdict`) purely from those recorded facts.  The application knowledge
plane reprojects persisted statements here at EVAL time over a claim's COMPLETE active leaf set, so
a verdict frozen at write-time over only one request's statements can never strand a claim
(evidence submitted across separate ObserveAction calls is adjudicated together).

Staleness is a WRITE-TIME attestation.  The core verdict MUST NOT read the filesystem at eval: the
knowledge plane is append-only and a claim's state is derived on read, so it must be a pure
function of the recorded evidence.  Re-reading disk would make two evals of identical knowledge
diverge (one run sees the file, a later one does not) and would let a post-hoc file change silently
un-approve a claim.  Instead the spike leaf already carries the harness's recorded ``gate``,
``files``, ``files_hash`` and ``result_hash`` — the deterministic harness that produced them at
spike-run time is the only approver, so those recorded facts are trusted here.  Post-hoc file
changes are handled by re-evidencing (ADR-25 supersede) and the adapter's explicit re-gate path,
not by an eval-time disk re-read.  The "research must precede spike" ordering is preserved via the
recorded ``ts``.
"""
from __future__ import annotations

import hashlib
import re

# Predicate URIs and the closed vocabulary of recorded evidence facts.  Defined once here so the
# host-neutral core and the host adapters share a single source of truth for both classification
# (:func:`evidence_fold`/:func:`project_leaf`) and the verdict (:func:`two_fold_verdict`).
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_RESEARCH = "https://empirica.dev/attestation/research/v1"
PREDICATE_SPIKE = "https://empirica.dev/attestation/spike/v1"
RESEARCH_KINDS = frozenset({"docs", "code", "runtime", "web"})
SUPPORTS, REFUTES = "supports", "refutes"
FOLD1, FOLD2 = "research", "spike"
GATE_PASS, GATE_FAIL = "pass", "fail"
NEEDS_HUMAN, NEEDS_SPIKE = "needs-decision", "needs-experiment"
PURPOSE_APPROVE, PURPOSE_REFUTE = "approve", "refute"


def evidence_fold(statement: object) -> str | None:
    """Return the fold named by a validated in-toto predicate type, if supported."""
    if not isinstance(statement, dict):
        return None
    predicate_type = statement.get("predicateType")
    if not isinstance(predicate_type, str):
        return None
    if predicate_type.endswith("/research/v1"):
        return "research"
    if predicate_type.endswith("/spike/v1"):
        return "spike"
    return None


def claim_digest(text: str) -> str:
    """SHA-256 of a claim's reviewable text.  A leaf binds to a claim only when its recorded
    ``claim_digest`` equals this, so a reworded claim no longer matches old evidence (ADR-25)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def project_leaf(statement: object) -> dict | None:
    """Project one in-toto evidence statement into a neutral leaf-fact shape, or ``None``.

    Fail-closed: a malformed, short, or unparseable statement contributes no facts and never raises.
    Returns exactly the verdict-relevant facts (``claim_id``, ``claim_digest``, ``fold``, ``ts``
    plus the fold-specific fields); the host adapters add their own telemetry fields on top.  This
    is the projection persisted statements are reprojected through at eval time, so it never
    touches the filesystem (see the module docstring on write-time staleness).
    """
    if not isinstance(statement, dict) or statement.get("_type") != STATEMENT_TYPE:
        return None
    ptype = statement.get("predicateType")
    if ptype not in (PREDICATE_RESEARCH, PREDICATE_SPIKE):
        return None
    subject = statement.get("subject")
    if not isinstance(subject, list) or len(subject) != 1 or not isinstance(subject[0], dict):
        return None
    name, digest = subject[0].get("name"), subject[0].get("digest")
    sha = digest.get("sha256") if isinstance(digest, dict) else None
    if (not isinstance(name, str) or not name or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha)):
        return None
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict) or not isinstance(predicate.get("ts"), str):
        return None
    common = {"claim_id": name, "claim_digest": sha, "ts": predicate["ts"]}
    if ptype == PREDICATE_RESEARCH:
        if predicate.get("kind") not in RESEARCH_KINDS or predicate.get("result") not in (
                SUPPORTS, REFUTES):
            return None
        if any(not isinstance(predicate.get(field), str) or not predicate[field].strip()
               for field in ("source", "citation")):
            return None
        return {**common, "fold": FOLD1, "kind": predicate["kind"],
                "source": predicate["source"], "citation": predicate["citation"],
                "result": predicate["result"]}
    hashes, files = predicate.get("hashes"), predicate.get("files")
    if (predicate.get("gate") not in (GATE_PASS, GATE_FAIL) or not isinstance(hashes, dict)
            or not isinstance(hashes.get("files"), str) or not isinstance(files, list)
            or any(not isinstance(path, str) for path in files)):
        return None
    return {**common, "fold": FOLD2, "gate": predicate["gate"], "files": files,
            "files_hash": hashes["files"], "result_hash": hashes.get("result")}


def _binds(leaf: dict, claim_id: str, claim_text: str) -> bool:
    """A leaf attests to this claim only when it names it AND its recorded digest matches the
    claim's current text (ADR-25 binding)."""
    return leaf["claim_id"] == claim_id and leaf["claim_digest"] == claim_digest(claim_text)


def _recorded_intact(leaf: dict) -> bool:
    """The spike leaf carries the recorded facts a real deterministic harness produces: a passing
    ``gate``, a non-empty ``files`` list, and a non-empty recorded ``files_hash`` (and a non-empty
    ``result_hash`` when the schema carries one).  No filesystem re-read — see the module docstring."""
    return (bool(leaf["files"]) and bool(leaf["files_hash"])
            and (leaf.get("result_hash") is None or bool(leaf["result_hash"])))


def two_fold_verdict(leaves: list[dict], claim_id: str, claim_text: str,
                     claim_kind: str | None, purpose: str) -> tuple[bool, str]:
    """The two-fold verdict a claim owes, derived purely from recorded leaf facts.

    ``leaves`` are neutral leaf-facts (the output of :func:`project_leaf`, or the adapter's richer
    ``validate_leaf`` projection — only the verdict-relevant fields are read).  Binding is by
    ``claim_digest`` over the supplied claim text, so a reworded claim does not inherit old
    evidence.  Refute paths surface a research REFUTES or a failing spike gate; a needs-decision
    claim is surfaced to the human rather than approved; a non-experiment claim is approved after
    Fold 1; a needs-experiment claim additionally requires a passing Fold-2 spike whose recorded
    facts are intact and which was recorded at or after the earliest supporting research.
    """
    bound = [leaf for leaf in leaves if _binds(leaf, claim_id, claim_text)]
    research = [leaf for leaf in bound if leaf["fold"] == FOLD1]
    spikes = [leaf for leaf in bound if leaf["fold"] == FOLD2]
    if purpose == PURPOSE_REFUTE:
        if any(leaf["result"] == REFUTES for leaf in research):
            return True, "refuted by research evidence"
        if any(leaf["gate"] == GATE_FAIL for leaf in spikes):
            return True, "refuted by a failing spike"
        return False, "cannot discard: no evidence refutes this claim"
    if claim_kind == NEEDS_HUMAN:
        return False, "needs-decision claims must be surfaced to the human"
    supporting = [leaf for leaf in research if leaf["result"] == SUPPORTS]
    if not supporting:
        return False, "FOLD 1 MISSING: fetch/read a real source and record its citation"
    if claim_kind != NEEDS_SPIKE:
        return True, "Fold 1 satisfied (research citation present)"
    passing = [leaf for leaf in spikes if leaf["gate"] == GATE_PASS]
    if not passing:
        return False, "FOLD 2 MISSING: a passing deterministic spike is required"
    intact = [leaf for leaf in passing if _recorded_intact(leaf)]
    if not intact:
        return False, "FOLD 2 STALE OR UNBOUND: re-run the spike with every dependent file"
    earliest = min(leaf["ts"] for leaf in supporting)
    if not any(leaf["ts"] >= earliest for leaf in intact):
        return False, "ORDER VIOLATION: research must precede the spike"
    return True, "Fold 1 + Fold 2 satisfied"
