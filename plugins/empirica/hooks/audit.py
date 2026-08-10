#!/usr/bin/env python3
"""Independent audit — the author cannot grade its own convergence (ADR-20 P6, ADR-21 M3).

Before a run may report `converged`, a SEPARATE PRINCIPAL must write a passing verdict. The
auditor's most important job is the one no hook can do: re-read each approved claim's Fold-1
citation and confirm the cited source actually supports the claim. A hook can check that a
citation was recorded; only a reader can check that it is true.

WHAT THIS GATE DOES AND DOES NOT PROVE — read before trusting it (ADR-19 G3, ADR-21's
no-overclaim rule). An earlier version of this docstring overstated the guarantee; the honest
version is narrower:

  * WHAT IT CATCHES: an audit that was never performed at all. Convergence requires a ticket
    file plus a verdict file, and an agent following the workflow produces neither unless it
    actually spawns the auditor. This is the failure observed in the wild, and it is now blocked.
  * WHAT IT DOES NOT CATCH: an author that deliberately fabricates the artifacts. Both the
    ticket and the verdict are unsigned JSON in a directory the authoring agent holds Write on,
    and no hook intercepts Write/Edit. An author that chooses to forge them can converge a run.
    Verified by an independent adversarial review, which reproduced exactly that end to end.
  * THE NONCE IS NOT A SECRET. It is `sha256(run_id:audit:seq)[:32]` with `run_id` sitting in
    plaintext in `run.json` and `seq` starting at 1 — so it is fully computable by anything that
    can read the run directory. Its purpose is BINDING (tying a verdict to a specific recorded
    spawn of this run), never authentication. Do not describe it as cryptographic attribution or
    as proof of authorship.

So the accurate framing is: this converts "forgot to audit" — the common, accidental failure —
into "deliberately forged an audit", which is a different and more visible kind of act, and one
that leaves artifacts on disk for a human or a later reviewer to find. That is a real
improvement and it is all this mechanism claims. It is the same trust level as the manifest and
the ledger (ADR-19 G3): the model has no instruction to tamper and tampering is visible, but
this is not OS isolation and not a security boundary.

REVIEWED-NESS IS PER CLAIM, AND CONTENT-ADDRESSED (ADR-25). A verdict records, for each approved
claim, the `claim_digest` and `evidence_digest` it was reviewed at — so it invalidates under exactly
the conditions that invalidate the evidence it reviewed, and no others. Adding a claim leaves the
rest reviewed; rewording one, or swapping its citation, un-reviews that one. The whole-graph pass
counter this replaced could only say "something changed", which made the normal audit-fail →
fix → re-audit loop invalidate its own previous work every round.

Trust direction (ADR-13, unchanged): agentic review may BLOCK but never APPROVE. The auditor is
a necessary veto, not the approver — the deterministic spike remains the only thing that
approves an experiment claim. A passing audit is therefore a NECESSARY condition for
`converged`, never a sufficient one.
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
_stamps = _load("stamps")
_actors = _load("actors")

# The auditor's identity as the harness sees it: the subagent_type a spawn must name for its
# ticket to count. Kept as a NAME, never a model id — tiers resolve in the agent definition
# (ADR-23), so a model rename never touches this logic.
AUDITOR_AGENT = "empirica-auditor"
VERDICT_PASS, VERDICT_FAIL = "pass", "fail"
_HEX = re.compile(r"[0-9a-f]{16,64}")


def tickets_path(run_dir: Path) -> Path:
    """Where the PreToolUse hook records auditor spawns it observed."""
    return run_dir / "audit-tickets.json"


def verdict_path(run_dir: Path) -> Path:
    """Where the auditor writes its verdict."""
    return run_dir / "audit-verdict.json"


def spawn_nonce(run_id: str, seq: int) -> str:
    """A per-spawn nonce. Derived (not random) because hooks must stay deterministic for a
    resumable run — the ADR-19 rule that also forbids datetime.now() here. Its job is binding,
    not secrecy: it ties a verdict to a spawn the harness actually saw."""
    return hashlib.sha256(f"{run_id}:audit:{seq}".encode("utf-8")).hexdigest()[:32]


def record_spawn(run_dir: Path, run_id: str, pass_no: int,
                 actor: dict | None = None) -> str:
    """Called from the PreToolUse spawn gate when an auditor subagent is spawned. Appends a
    ticket and returns its nonce. This is the harness-proven half of the audit gate.

    `actor` (ADR-24 §2) is the auditor's identity as the DISPATCHER understood it — read from the
    agent definition's `model:` field, never from anything the auditor says about itself, because
    a model cannot report its own identity (ADR-24 finding 3). Recorded as `declared`, not
    `witnessed`: `spawn_gate.py` sees `subagent_type` and the model resolves from frontmatter
    AFTER this hook fires, so the harness never observes the resolved model. Marking it witnessed
    would be the overclaim ADR-21 forbids.

    Optional and additive: a ticket without an actor is exactly the ticket this wrote before, so
    the audit gate's behaviour is unchanged for anyone who does not record one.
    """
    path = tickets_path(run_dir)
    with _io.lock(path):
        data = _read_tickets(path)
        nonce = spawn_nonce(run_id, len(data) + 1)
        ticket = {"nonce": nonce, "pass": pass_no}
        normalised = (_actors.normalise(actor, attribution=_actors.DECLARED,
                                       force_attribution=True) if actor else None)
        if normalised is not None:
            ticket["actor"] = normalised
        data.append(ticket)
        _io.atomic_write_json(path, {"tickets": data})
    return nonce


def audit_actor(run_dir: Path) -> dict | None:
    """The actor recorded for the most recent auditor spawn, or None.

    The LAST ticket, because that is the spawn whose verdict the gate is about to evaluate; an
    earlier round's auditor may legitimately have been a different model.
    """
    tickets = _read_tickets(tickets_path(run_dir))
    for ticket in reversed(tickets):
        if ticket.get("actor"):
            return ticket["actor"]
    return None


def _raise_non_finite(_c):
    raise ValueError("non-finite JSON constant")


def _read_tickets(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_raise_non_finite)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    tickets = raw.get("tickets") if isinstance(raw, dict) else None
    if not isinstance(tickets, list):
        return []
    out = []
    for t in tickets:
        if (isinstance(t, dict) and isinstance(t.get("nonce"), str)
                and _HEX.fullmatch(t["nonce"])
                and isinstance(t.get("pass"), int) and not isinstance(t.get("pass"), bool)):
            ticket = {"nonce": t["nonce"], "pass": t["pass"]}
            # ADR-24 §2. Normalised on read as well as on write, so a hand-edited ticket claiming
            # a policy-excluded model or `attribution: witnessed` for an in-session spawn cannot
            # smuggle a stronger attribution than the dispatch path actually earned.
            actor = _actors.normalise(t.get("actor"), attribution=_actors.DECLARED,
                                      force_attribution=True)
            if actor is not None:
                ticket["actor"] = actor
            out.append(ticket)
    return out


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _review_entry(raw) -> dict | None:
    """One normalised `claims_reviewed` entry, or None if it is not a usable review record.

    The shape is `{claim_id, claim_digest, evidence_digest}` (ADR-25). Both digests are required
    and must be real sha256 hex: an entry missing one cannot be compared against the claim it
    names, so it is not a review of anything. Dropped rather than tolerated — an entry that
    cannot be checked must not count as coverage.

    The pre-ADR-25 flat form (a bare claim-id string) is NOT accepted, deliberately and with no
    fallback. Accepting both would keep the weaker form reachable, and a gate that honours a
    legacy shape is the exploit `convergence_gate.py` already had to close once.
    """
    if not isinstance(raw, dict):
        return None
    claim_id = raw.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id:
        return None
    digests = {}
    for field in ("claim_digest", "evidence_digest"):
        value = raw.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            return None
        digests[field] = value
    return {"claim_id": claim_id, **digests}


def read_verdict(run_dir: Path) -> dict | None:
    """The auditor's verdict, normalised, or None when there is none / it is unusable.

    A malformed verdict is treated as ABSENT rather than failing: either way the run cannot
    report `converged`, and "absent" is the honest description of an unreadable verdict.
    """
    path = verdict_path(run_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_raise_non_finite)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("verdict") not in (VERDICT_PASS, VERDICT_FAIL):
        return None
    nonce = raw.get("nonce")
    if not isinstance(nonce, str) or not _HEX.fullmatch(nonce):
        return None
    findings = raw.get("findings")
    reviewed = raw.get("claims_reviewed")
    return {
        "verdict": raw["verdict"],
        "nonce": nonce,
        "auditor": raw.get("auditor") if isinstance(raw.get("auditor"), str) else None,
        "claims_reviewed": ([e for e in (_review_entry(r) for r in reviewed) if e]
                            if isinstance(reviewed, list) else []),
        "findings": ([f for f in findings if isinstance(f, str)]
                     if isinstance(findings, list) else []),
        "ts": raw.get("ts") if isinstance(raw.get("ts"), str) else None,
    }


def route_note(run: dict) -> str | None:
    """A note for the run's report when P1 was not cleanly satisfied, or None when it was.

    The P1 ordering is reported to the AUDITOR rather than hard-blocked by the Stop gate: the
    stamp can be coarse (a pass-relative marker when the harness supplies no timestamp), and a
    coarse signal should not be the sole thing that wedges a run. The auditor, which reads both
    stamps and the transcript, is the right judge — this function is how the gate tells it where
    to look.

    Returns a note for INCONCLUSIVE as well as VIOLATION. An unverifiable ordering is precisely
    what the auditor needs pointed out; treating it as clean would restore the vacuum this check
    exists to close. The ordering logic itself lives in stamps.py — this module used to carry its
    own copy, and the copy meant one comparison bug had to be fixed in two places.
    """
    verdict, reason = _stamps.route_verdict(run)
    return None if verdict == _stamps.OK else reason


def stamps_route_verdict(run: dict) -> tuple[str, str]:
    """The full three-way P1 verdict: `("ok"|"violation"|"inconclusive", reason)`.

    Re-exported here so the Stop gate reaches the ordering logic through the module it already
    loads, without loading stamps.py a second time under a separate module object. The gate
    needs the three-way answer because it reports a proven inversion and an unverifiable
    ordering under different keys.
    """
    return _stamps.route_verdict(run)


def check(run_dir: Path, approved: dict[str, dict]) -> tuple[bool, str]:
    """May this run report `converged`? Returns (ok, reason).

    `approved` maps each approved claim id to the digests it must have been reviewed at:
    `{claim_id: {"claim_digest": ..., "evidence_digest": ...}}`. The CALLER computes them from
    the graph and the evidence store (see `convergence_gate`), so this module never has to know
    how either is stored.

    Requirements, each of which closes an observed or plausible bypass:
      1. an auditor spawn ticket exists     — the audit was actually performed
      2. a verdict exists and is readable   — its result is on disk, not in the transcript
      3. the verdict's nonce matches a ticket — it is bound to a real spawn of THIS run
      4. the verdict is `pass`              — a failing audit blocks (ADR-13: may veto)
      5. every approved claim was reviewed AT ITS CURRENT DIGESTS — an auditor cannot pass a run
         by reviewing one claim and ignoring the rest, NOR by reviewing an older version of one

    Requirement 5 is per (claim, claim_digest, evidence_digest) rather than per graph (ADR-25).
    That is what lets a verdict age gracefully: adding a 23rd claim leaves the other 22 reviewed,
    while rewording a claim or swapping its citation un-reviews exactly that claim. The previous
    scheme compared one pass number for the whole graph, so any change invalidated everything —
    and because `convergence_gate` ticks a pass on every audit-fail round, the intended
    fix-and-loop rhythm invalidated the verdict mechanically, on compliant behaviour.

    The `current_pass` staleness proxy that used to live here is GONE, not merely relaxed. It
    stood in for "the graph changed since review"; both digests now measure that directly, so the
    proxy's only remaining behaviour was that false positive.
    """
    tickets = _read_tickets(tickets_path(run_dir))
    if not tickets:
        return False, ("no independent audit was performed: spawn the "
                       f"`{AUDITOR_AGENT}` subagent to verify this run before converging "
                       "(ADR-20 P6 — the author cannot grade its own convergence)")

    verdict = read_verdict(run_dir)
    if verdict is None:
        return False, (f"an auditor was spawned but no readable verdict is present at "
                       f"{verdict_path(run_dir).name}; the auditor must write its verdict "
                       f"artifact (verdict: pass|fail, nonce, and claims_reviewed as "
                       f"{{claim_id, claim_digest, evidence_digest}} entries)")

    if verdict["nonce"] not in {t["nonce"] for t in tickets}:
        return False, ("the audit verdict's nonce does not match any auditor spawn recorded "
                       "for this run — the verdict must carry the nonce issued to the auditor "
                       "at spawn time")

    if verdict["verdict"] != VERDICT_PASS:
        findings = "; ".join(verdict["findings"][:5]) or "no findings recorded"
        return False, f"the independent audit FAILED: {findings}"

    reviewed = {e["claim_id"]: e for e in verdict["claims_reviewed"]}
    unreviewed, restated, re_evidenced = [], [], []
    for claim_id, digests in approved.items():
        entry = reviewed.get(claim_id)
        if entry is None:
            unreviewed.append(claim_id)
        elif entry["claim_digest"] != digests["claim_digest"]:
            restated.append(claim_id)
        elif entry["evidence_digest"] != digests["evidence_digest"]:
            re_evidenced.append(claim_id)

    if unreviewed or restated or re_evidenced:
        # Each bucket gets its own sentence: "not reviewed", "reworded since review" and
        # "re-evidenced since review" call for different work, and a message that lumped them
        # together would send the auditor looking for the wrong thing.
        parts = []
        if unreviewed:
            parts.append(f"{len(unreviewed)} approved claim(s) were never reviewed "
                         f"({', '.join(sorted(unreviewed)[:5])})")
        if restated:
            parts.append(f"{len(restated)} claim(s) were REWORDED after review "
                         f"({', '.join(sorted(restated)[:5])}), so the audit answered a "
                         f"different question")
        if re_evidenced:
            parts.append(f"{len(re_evidenced)} claim(s) had their EVIDENCE changed after review "
                         f"({', '.join(sorted(re_evidenced)[:5])}), so the citation the auditor "
                         f"re-read is not the one now supporting the claim")
        return False, ("the audit does not cover the run's current state: " + "; ".join(parts)
                       + " — re-audit these claims (the rest stay reviewed)")

    return True, "independent audit passed"
