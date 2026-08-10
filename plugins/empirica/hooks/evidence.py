#!/usr/bin/env python3
"""Two-fold evidence binding — research FIRST, then spike (ADR-20 P3, ADR-21 M2, ADR-18).

Validation has two folds, and the order is load-bearing:

  Fold 1 — RESEARCH, EVERY claim, first. A claim's confidence may not leave 0.0 until a
      research record binds it to a source OUTSIDE the model's training data:
      {source, kind: docs|code|runtime|web, citation, result: supports|refutes, ts}.
      This is the fold the observed failures skipped entirely: an agent that "read the repo
      and drew conclusions from training data" performed ZERO Fold-1 validation, and every
      confidence it wrote was unbacked.

  Fold 2 — SPIKE, `needs-experiment` claims only, and it PRESUPPOSES Fold 1. A real
      deterministic check whose verdict is a subprocess exit code:
      {command_hash, gate: pass, result_hash, files_hash, ts}. Written ONLY by
      spike_harness.py from the real exit code — never from the transcript.

"Presupposes" is enforced, not advisory: a spike record is rejected unless a research record
for the same claim exists AND is older (`research.ts <= spike.ts`). A passing spike over a
claim that was never researched is a green light on an unexamined assumption, so the gate
refuses it rather than counting it.

WHAT EACH LAYER CAN AND CANNOT PROVE (the honesty rule, ADR-21 — do not overclaim):
  * Fold 2 is HARNESS-PROVEN. spike_harness.py writes the record from a real exit code, and
    the record is keyed to a command hash and a file digest, so the model cannot forge it.
  * Fold 1 presence is HARNESS-CHECKED (the gate refuses ≥θ without a structurally valid
    record) but its CONTENT is not: a model can write a citation to a source that does not
    say what it claims, or does not exist. Closing that needs two things this module does not
    do — the PreToolUse stamp that records the tool call which actually fetched the source,
    and the independent auditor (ADR-20 P6) who re-reads the citation. The gate's guarantee
    is "a citation was recorded", not "the citation is true".

Tamper-evidence uses TWO distinct digests, because there are two distinct tamper cases and
the ADRs (18/20/21) name `files_hash` without defining its scope:
  * `subject[].digest.sha256` — the CLAIM TEXT hash (ADR-22's in-toto binding). Rewording a
    claim after evidencing it invalidates the evidence: the evidence answered a different
    question.
  * `predicate.hashes.files` — the digest of the FILES the spike ran against. Editing the
    code after a green spike invalidates the spike: it tested a different tree.

Storage: one JSON file per evidence leaf under `<run_dir>/evidence/`, so the two writers
(spike_harness.py for Fold 2, the run for Fold 1) never contend on one file. Transient run
memory, git-ignored, never committed (ADR-14).

Time: hooks must not call datetime.now() in a resumable run (ADR-19's rule), so every `ts`
is stamped by the CALLER and this module only ORDERS timestamps, never generates them.
"""
import hashlib
import importlib.util
import json
import re
from pathlib import Path


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_io = _load("atomicio")
_actors = _load("actors")

# --- in-toto attestation Statement v1 ----------------------------------------
# Verified against the in-toto attestation spec (github.com/in-toto/attestation, spec/v1/
# statement.md, resource_descriptor.md, digest_set.md, field_types.md) — a live read, not recall.
#
# Required Statement fields, exact spelling: _type, subject[], predicateType, predicate.
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
# predicateType is typed as a TypeURI: it "SHOULD resolve to a human-readable description, but
# MAY be unresolvable", SHOULD carry a version, and needs no registration ("the natural
# namespacing of URIs is sufficient"). It must therefore be a real URI — a bare
# "empirica/research/v1" has no scheme, so it is a relative reference and NOT conformant. These
# need not resolve to a live page today.
PREDICATE_RESEARCH = "https://empirica.dev/attestation/research/v1"
PREDICATE_SPIKE = "https://empirica.dev/attestation/spike/v1"
# Subject digests: the spec requires every Statement subject to set `digest`, and RECOMMENDS at
# least sha256. A subject need not be a file — the spec's own examples include build services
# and non-file resources — so hashing a claim's TEXT is conformant. `name` is optional but the
# spec says to set it when meaningful; a claim_id is meaningful, so we always set it.
# No DSSE envelope: the Envelope layer is optional and only IT mandates signatures, so a bare
# unsigned Statement is a legitimate standalone artifact. empirica does not sign evidence.

FOLD1, FOLD2 = "research", "spike"
RESEARCH_KINDS = frozenset({"docs", "code", "runtime", "web"})
SUPPORTS, REFUTES = "supports", "refutes"
GATE_PASS, GATE_FAIL = "pass", "fail"

# Claim kinds that owe a Fold-2 spike, and the one kind no agent may approve at all.
NEEDS_SPIKE = "needs-experiment"
NEEDS_HUMAN = "needs-decision"

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def evidence_dir(run_dir: Path) -> Path:
    """The evidence store: `<run_dir>/evidence/`. One Statement per file."""
    return run_dir / "evidence"


def _leaf_path(run_dir: Path, evidence_id: str) -> Path:
    """Path for one evidence leaf. The id is sanitised to a single safe filename segment —
    no traversal out of the evidence directory (the manifest's run_id rule)."""
    safe = _SAFE_ID.sub("_", evidence_id) or "unnamed"
    return evidence_dir(run_dir) / f"{safe}.json"


def claim_digest(claim_text: str) -> str:
    """sha256 of the claim's text — the in-toto subject digest that binds evidence to the
    exact claim it answered. Reword the claim and this changes, so the binding breaks."""
    return hashlib.sha256(claim_text.encode("utf-8")).hexdigest()


def evidence_digest(leaves: list[dict], claim_id: str, claim_text: str) -> str:
    """sha256 over the SUPPORTING evidence bound to this claim — the digest an audit verdict
    records so its reviewed-ness ages per claim (ADR-25).

    ONE definition, called by both the auditor that writes a verdict and the gate that checks
    it. A second hand-rolled hash on either side would drift, and a drifted digest reads as
    "evidence changed" forever — an audit nobody can ever pass.

    Covers the fields a reviewer's judgement actually rests on: which fold, which source, which
    citation, and the verdict the leaf carries. It deliberately does NOT cover `ts`, because
    re-recording identical research with a new timestamp did not change what the auditor read,
    and it does not cover the claim text — `claim_digest` already carries that, and folding it in
    twice would make one change move two digests for no gain.

    Why this is needed at all: `claim_digest` hashes claim TEXT. Swapping a citation for a
    fabricated one leaves the text — and so that digest — identical, which is why keying an audit
    verdict on the claim digest alone would be blind to evidence substitution (ADR-25, option B).

    Empty (nothing bound and supporting) hashes to a distinct constant rather than raising: an
    approved claim always has supporting research, so a caller seeing the empty digest is looking
    at a claim that cannot be approved anyway, and the gate's per-fold message says so better than
    an exception here would.
    """
    bound = sorted(
        (lf for lf in leaves
         if _binds(lf, claim_id, claim_text) and lf.get("result") != REFUTES),
        key=lambda lf: (lf["fold"], lf.get("source") or "", lf.get("citation") or "",
                        lf.get("command_hash") or "", lf.get("files_hash") or ""),
    )
    h = hashlib.sha256()
    for lf in bound:
        for field in ("fold", "kind", "source", "citation", "result", "gate",
                      "command_hash", "files_hash", "result_hash"):
            value = lf.get(field)
            h.update(b"\0" if value is None else str(value).encode("utf-8"))
            h.update(b"\0")
    return h.hexdigest()


def command_digest(cmd: list[str]) -> str:
    """sha256 over the argv of the checked command, NUL-joined so ["a b"] and ["a","b"] are
    distinct — a command hash that collided on argv boundaries would let a different command
    inherit a green record."""
    return hashlib.sha256("\0".join(cmd).encode("utf-8")).hexdigest()


def files_digest(paths: list[Path]) -> str:
    """sha256 over the (relative path, content hash) of each file the spike ran against, in
    sorted order so the digest is deterministic. A missing file hashes as absent rather than
    raising — deleting a file after a spike must CHANGE the digest, not crash the gate.
    """
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: str(x)):
        h.update(str(p).encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(hashlib.sha256(Path(p).read_bytes()).hexdigest().encode("ascii"))
        except OSError:
            h.update(b"__absent__")
        h.update(b"\0")
    return h.hexdigest()


# --- writing (Fold 1 by the run; Fold 2 ONLY by spike_harness.py) -----------


def _attach_actor(predicate: dict, actor: dict | None) -> None:
    """Add `actor` to a predicate IN PLACE, only when it normalises (ADR-24 §2).

    Absent or malformed → the key is not written at all, rather than written as null. That keeps
    an actor-less leaf byte-identical to what this module produced before ADR-24, so the whole
    feature is additive: no existing digest shifts, and "no attribution recorded" is expressed by
    absence rather than by a value a reader might mistake for one.
    """
    normalised = _actors.normalise(actor) if actor is not None else None
    if normalised is not None:
        predicate["actor"] = normalised


def _statement(claim_id: str, claim_text: str, predicate_type: str, predicate: dict) -> dict:
    """An in-toto Statement v1 binding a predicate to a claim as its subject."""
    return {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": claim_id, "digest": {"sha256": claim_digest(claim_text)}}],
        "predicateType": predicate_type,
        "predicate": predicate,
    }


def write_research(run_dir: Path, evidence_id: str, claim_id: str, claim_text: str, *,
                   source: str, kind: str, citation: str, result: str, ts: str,
                   actor: dict | None = None) -> Path:
    """Record a Fold-1 research leaf. `ts` is caller-stamped (never generated here).

    Raises ValueError on a malformed record: a research record that cannot be validated is
    worse than none, because it looks like evidence to a reader.

    `actor` (ADR-24 §2) records WHO produced this evidence, and is written by the dispatcher —
    never by the actor, which cannot report its own identity. Optional and additive: omitting it
    yields exactly the predicate this function produced before, so no existing leaf, digest, or
    gate behaviour changes. A malformed actor is dropped rather than raising, for the same reason
    the graph drops one: attribution is a record, and losing a record must not invalidate real
    evidence.
    """
    if kind not in RESEARCH_KINDS:
        raise ValueError(f"research kind must be one of {sorted(RESEARCH_KINDS)}: {kind!r}")
    if result not in (SUPPORTS, REFUTES):
        raise ValueError(f"research result must be {SUPPORTS!r} or {REFUTES!r}: {result!r}")
    if not (isinstance(citation, str) and citation.strip()):
        raise ValueError("a research record needs a non-empty citation")
    if not (isinstance(source, str) and source.strip()):
        raise ValueError("a research record needs a non-empty source")
    predicate = {"fold": FOLD1, "kind": kind, "source": source, "citation": citation,
                 "result": result, "ts": ts}
    _attach_actor(predicate, actor)
    stmt = _statement(claim_id, claim_text, PREDICATE_RESEARCH, predicate)
    path = _leaf_path(run_dir, evidence_id)
    with _io.lock(path):
        _io.atomic_write_json(path, stmt)
    return path


def write_spike(run_dir: Path, evidence_id: str, claim_id: str, claim_text: str, *,
                cmd: list[str], gate: str, result_hash: str, files: list[Path],
                ts: str, actor: dict | None = None) -> Path:
    """Record a Fold-2 spike leaf. SOLE CALLER: spike_harness.py, from a real exit code.

    Nothing here can tell a forged call from a genuine one — that property comes from the
    fact that the only code path invoking it derives `gate` from a subprocess's returncode.
    Keep it that way: never call this from a model-driven path.
    """
    if gate not in (GATE_PASS, GATE_FAIL):
        raise ValueError(f"gate must be {GATE_PASS!r} or {GATE_FAIL!r}: {gate!r}")
    predicate = {"fold": FOLD2, "kind": "spike", "command": list(cmd),
                 "command_hash": command_digest(cmd), "gate": gate,
                 "hashes": {"result": result_hash, "files": files_digest(files)},
                 "files": [str(p) for p in files], "ts": ts}
    # A spike's actor is CODE, not a model: its verdict is a subprocess exit code, which is the
    # one approver in the whole system (ADR-13). Recording that explicitly matters — it is what
    # distinguishes "a machine decided" from "a model judged", and the §3 same-actor check must
    # never flag a spike as a model clash.
    _attach_actor(predicate, actor if actor is not None
                  else {"source_type": _actors.CODE, "model": "spike_harness.py",
                        "harness": _actors.HARNESS_BASELINE, "attribution": _actors.WITNESSED})
    stmt = _statement(claim_id, claim_text, PREDICATE_SPIKE, predicate)
    path = _leaf_path(run_dir, evidence_id)
    with _io.lock(path):
        _io.atomic_write_json(path, stmt)
    return path


# --- reading + structural validation ----------------------------------------


def _raise_non_finite(_c):
    raise ValueError("non-finite JSON constant")


def read_leaves(run_dir: Path) -> list[dict]:
    """Every structurally VALID evidence leaf in the store. Malformed or unreadable leaves
    are DROPPED, not tolerated: an unparseable leaf cannot support a claim, and dropping it
    means the claim it was meant to support stays open (fail closed, not fail quiet).
    """
    directory = evidence_dir(run_dir)
    if not directory.is_dir():
        return []
    leaves = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"),
                             parse_constant=_raise_non_finite)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        leaf = validate_leaf(raw)
        if leaf is not None:
            leaves.append(leaf)
    return leaves


def validate_leaf(raw) -> dict | None:
    """A normalised leaf, or None if it is not a valid in-toto Statement for empirica.

    Validation is strict about the in-toto envelope (`_type`, one subject with a sha256
    digest, a known predicateType) because a leaf that does not bind to a claim digest binds
    to nothing — it would be evidence in name only.
    """
    if not isinstance(raw, dict) or raw.get("_type") != STATEMENT_TYPE:
        return None
    ptype = raw.get("predicateType")
    if ptype not in (PREDICATE_RESEARCH, PREDICATE_SPIKE):
        return None
    subject = raw.get("subject")
    if not isinstance(subject, list) or len(subject) != 1 or not isinstance(subject[0], dict):
        return None
    name, digest = subject[0].get("name"), subject[0].get("digest")
    if not isinstance(name, str) or not name or not isinstance(digest, dict):
        return None
    sha = digest.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    predicate = raw.get("predicate")
    if not isinstance(predicate, dict):
        return None
    ts = predicate.get("ts")
    if not isinstance(ts, str):
        return None

    # ADR-24 §2: surface the actor when one is recorded, so the §3 checks can compare
    # assignment against attribution. Normalised through actors.py, so a leaf carrying a
    # policy-excluded or malformed actor reads as "no attribution" — never as a valid one, and
    # never as a reason to reject otherwise-valid evidence.
    common = {"claim_id": name, "claim_digest": sha, "ts": ts,
              "actor": _actors.normalise(predicate.get("actor"))}
    if ptype == PREDICATE_RESEARCH:
        if (predicate.get("kind") not in RESEARCH_KINDS
                or predicate.get("result") not in (SUPPORTS, REFUTES)):
            return None
        for field in ("source", "citation"):
            value = predicate.get(field)
            if not isinstance(value, str) or not value.strip():
                return None
        return {**common, "fold": FOLD1, "kind": predicate["kind"],
                "source": predicate["source"], "citation": predicate["citation"],
                "result": predicate["result"]}

    if predicate.get("gate") not in (GATE_PASS, GATE_FAIL):
        return None
    hashes = predicate.get("hashes")
    if not isinstance(hashes, dict) or not isinstance(hashes.get("files"), str):
        return None
    files = predicate.get("files")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        return None
    return {**common, "fold": FOLD2, "gate": predicate["gate"],
            "files": files, "files_hash": hashes["files"],
            "result_hash": hashes.get("result") if isinstance(hashes.get("result"), str) else None,
            "command_hash": (predicate.get("command_hash")
                             if isinstance(predicate.get("command_hash"), str) else None)}


def _binds(leaf: dict, claim_id: str, claim_text: str) -> bool:
    """Does this leaf bind to THIS claim, as it is worded NOW? The digest check is what makes
    "reword the claim after evidencing it" fail instead of silently inheriting the evidence."""
    return leaf["claim_id"] == claim_id and leaf["claim_digest"] == claim_digest(claim_text)


def _files_intact(leaf: dict) -> bool:
    """Does the spike's file digest still match the working tree? A green spike over a
    since-edited tree tested a different program (ADR-21 M2).

    An EMPTY file list can never fail this check — `files_digest([])` is a constant, so the
    digest matches forever no matter what changes on disk. That made a no-`--file` spike
    permanently fresh and silently voided Fold-2's tamper-evidence for that claim (found by an
    independent coverage review). Such a leaf is therefore treated as NOT intact: a spike that
    binds nothing cannot substantiate a staleness guarantee, so it must not approve a claim.
    """
    if not leaf["files"]:
        return False
    return leaf["files_hash"] == files_digest([Path(f) for f in leaf["files"]])


# --- the two-fold verdict the gate consumes ---------------------------------


def verdict(leaves: list[dict], claim_id: str, claim_text: str, claim_kind: str | None,
            purpose: str) -> tuple[bool, str]:
    """Does this claim have the evidence it owes? Returns (ok, reason).

    `purpose` is "approve" (may this claim reach ≥θ?) or "refute" (may it be discarded?).
    The reason string is written for a human reading a blocked-stop message, so it names the
    missing fold rather than saying "invalid".
    """
    bound = [lf for lf in leaves if _binds(lf, claim_id, claim_text)]
    research = [lf for lf in bound if lf["fold"] == FOLD1]
    spikes = [lf for lf in bound if lf["fold"] == FOLD2]

    if purpose == "refute":
        # A claim is refuted by research that refutes it, or by a spike that FAILED.
        if any(lf["result"] == REFUTES for lf in research):
            return True, "refuted by research evidence"
        if any(lf["gate"] == GATE_FAIL for lf in spikes):
            return True, "refuted by a failing spike"
        return False, ("cannot discard: no evidence refutes this claim (a discard needs a "
                       "refutation, or it is just a bypass)")

    # --- approve -------------------------------------------------------------
    if claim_kind == NEEDS_HUMAN:
        return False, ("needs-decision claims are not agent-resolvable — surface to the "
                       "human as blocked (ADR-20 P3)")

    supporting = [lf for lf in research if lf["result"] == SUPPORTS]
    if not supporting:
        return False, ("FOLD 1 MISSING: no research record cites a source outside the "
                       "model's training data for this claim. Recall is not evidence — "
                       "fetch/read a real source and record the citation before scoring it")

    if claim_kind != NEEDS_SPIKE:
        return True, "Fold 1 satisfied (research citation present)"

    passing = [lf for lf in spikes if lf["gate"] == GATE_PASS]
    if not passing:
        return False, ("FOLD 2 MISSING: a needs-experiment claim needs a passing spike "
                       "record written by the harness from a real exit code")
    intact = [lf for lf in passing if _files_intact(lf)]
    if not intact:
        if all(not lf["files"] for lf in passing):
            return False, ("FOLD 2 UNBOUND: the passing spike names no files, so its "
                           "tamper-evidence is vacuous — a green result that can never go "
                           "stale proves nothing about the tree it ran against. Re-run the "
                           "spike with --file for every file the check depends on")
        return False, ("FOLD 2 STALE: the passing spike's files_hash no longer matches — "
                       "the tree changed after the spike ran, so re-run it")
    # Fold 2 presupposes Fold 1: the research must exist AND predate the spike. This is what
    # stops "spike first, back-fill a citation afterwards" from counting as research-first.
    earliest_research = min(lf["ts"] for lf in supporting)
    if not any(lf["ts"] >= earliest_research for lf in intact):
        return False, ("ORDER VIOLATION: every passing spike predates the research that "
                       "was supposed to inform it — research comes FIRST (ADR-20 P3)")
    return True, "Fold 1 + Fold 2 satisfied"


def oracle(run_dir: Path, graph: dict):
    """Build the `evidence_ok(node_id, purpose)` callable claimgraph.state_of expects.

    Reads the store ONCE and closes over it, so a gate evaluating a whole graph does not
    re-scan the evidence directory per node.
    """
    leaves = read_leaves(run_dir)

    def evidence_ok(node_id: str, purpose: str) -> bool:
        node = graph["nodes"].get(node_id)
        if node is None:
            return False
        ok, _ = verdict(leaves, node_id, node["text"], node["kind"], purpose)
        return ok

    return evidence_ok


def explain(run_dir: Path, graph: dict, node_id: str, purpose: str = "approve") -> str:
    """The human-readable reason a claim is not approvable — used in the gate's block message
    so the agent is told which fold is missing, not merely that it failed."""
    node = graph["nodes"].get(node_id)
    if node is None:
        return "unknown claim"
    _, reason = verdict(read_leaves(run_dir), node_id, node["text"], node["kind"], purpose)
    return reason
