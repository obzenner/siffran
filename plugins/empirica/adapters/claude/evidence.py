"""Pure Claude evidence validation and two-fold verdict derivation.

This module has no persistence operations.  It validates in-toto statements supplied to the
adapter and derives the verdict recorded by the application knowledge adapter.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_RESEARCH = "https://empirica.dev/attestation/research/v1"
PREDICATE_SPIKE = "https://empirica.dev/attestation/spike/v1"
RESEARCH_KINDS = frozenset({"docs", "code", "runtime", "web"})
SUPPORTS, REFUTES = "supports", "refutes"
FOLD1, FOLD2 = "research", "spike"
GATE_PASS, GATE_FAIL = "pass", "fail"
NEEDS_HUMAN, NEEDS_SPIKE = "needs-decision", "needs-experiment"


def claim_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def files_digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p)):
        h.update(str(path).encode())
        h.update(b"\0")
        try:
            h.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        except OSError:
            h.update(b"__absent__")
        h.update(b"\0")
    return h.hexdigest()


def _actor(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    model = value.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        return None
    return dict(value)


def validate_leaf(raw: object) -> dict | None:
    if not isinstance(raw, dict) or raw.get("_type") != STATEMENT_TYPE:
        return None
    ptype = raw.get("predicateType")
    if ptype not in (PREDICATE_RESEARCH, PREDICATE_SPIKE):
        return None
    subject = raw.get("subject")
    if not isinstance(subject, list) or len(subject) != 1 or not isinstance(subject[0], dict):
        return None
    name, digest = subject[0].get("name"), subject[0].get("digest")
    sha = digest.get("sha256") if isinstance(digest, dict) else None
    if not isinstance(name, str) or not name or not isinstance(sha, str) or not re.fullmatch(
        r"[0-9a-f]{64}", sha
    ):
        return None
    predicate = raw.get("predicate")
    if not isinstance(predicate, dict) or not isinstance(predicate.get("ts"), str):
        return None
    common = {"claim_id": name, "claim_digest": sha, "ts": predicate["ts"],
              "actor": _actor(predicate.get("actor"))}
    if ptype == PREDICATE_RESEARCH:
        if predicate.get("kind") not in RESEARCH_KINDS or predicate.get("result") not in (
            SUPPORTS, REFUTES
        ):
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
    samples = predicate.get("samples")
    codes = predicate.get("exit_codes")
    return {**common, "fold": FOLD2, "gate": predicate["gate"], "files": files,
            "files_hash": hashes["files"], "result_hash": hashes.get("result"),
            "command_hash": predicate.get("command_hash"),
            "command": ([part for part in predicate.get("command", []) if isinstance(part, str)]
                        if isinstance(predicate.get("command"), list) else []),
            "samples": samples if type(samples) is int and samples > 0 else 1,
            "exit_codes": codes if isinstance(codes, list) else []}


def _binds(leaf: dict, claim_id: str, claim_text: str) -> bool:
    return leaf["claim_id"] == claim_id and leaf["claim_digest"] == claim_digest(claim_text)


def verdict(leaves: list[dict], claim_id: str, claim_text: str, claim_kind: str | None,
            purpose: str) -> tuple[bool, str]:
    bound = [leaf for leaf in leaves if _binds(leaf, claim_id, claim_text)]
    research = [leaf for leaf in bound if leaf["fold"] == FOLD1]
    spikes = [leaf for leaf in bound if leaf["fold"] == FOLD2]
    if purpose == "refute":
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
    intact = [leaf for leaf in passing if leaf["files"] and
              leaf["files_hash"] == files_digest([Path(path) for path in leaf["files"]])]
    if not intact:
        return False, "FOLD 2 STALE OR UNBOUND: re-run the spike with every dependent file"
    earliest = min(leaf["ts"] for leaf in supporting)
    if not any(leaf["ts"] >= earliest for leaf in intact):
        return False, "ORDER VIOLATION: research must precede the spike"
    return True, "Fold 1 + Fold 2 satisfied"
