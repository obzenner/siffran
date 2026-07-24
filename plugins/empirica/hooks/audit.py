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


def record_spawn(run_dir: Path, run_id: str, pass_no: int) -> str:
    """Called from the PreToolUse spawn gate when an auditor subagent is spawned. Appends a
    ticket and returns its nonce. This is the harness-proven half of the audit gate."""
    path = tickets_path(run_dir)
    with _io.lock(path):
        data = _read_tickets(path)
        nonce = spawn_nonce(run_id, len(data) + 1)
        data.append({"nonce": nonce, "pass": pass_no})
        _io.atomic_write_json(path, {"tickets": data})
    return nonce


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
            out.append({"nonce": t["nonce"], "pass": t["pass"]})
    return out


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
    return {
        "verdict": raw["verdict"],
        "nonce": nonce,
        "auditor": raw.get("auditor") if isinstance(raw.get("auditor"), str) else None,
        "claims_reviewed": ([c for c in raw["claims_reviewed"] if isinstance(c, str)]
                            if isinstance(raw.get("claims_reviewed"), list) else []),
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


def check(run_dir: Path, approved_claims: list[str],
          current_pass: int | None = None) -> tuple[bool, str]:
    """May this run report `converged`? Returns (ok, reason).

    Requirements, each of which closes an observed or plausible bypass:
      1. an auditor spawn ticket exists           — the audit was actually performed
      2. a verdict exists and is readable         — its result is on disk, not in the transcript
      3. the verdict's nonce matches a ticket     — it is bound to a real spawn of THIS run
      4. the verdict is `pass`                    — a failing audit blocks (ADR-13: may veto)
      5. every approved claim was reviewed        — an auditor cannot pass a run by reviewing
                                                    one claim and ignoring the rest
      6. the audit is not STALE                   — the ticket's pass is not older than the work
                                                    it certifies (only when `current_pass` given)

    Requirement 6 exists because the ticket recorded a `pass` number that nothing ever compared
    (found by an independent doc audit). Without it, an audit from pass 1 certified a graph that
    kept changing for seven more passes — a real staleness hole, and the same class of bug as an
    unbound spike record.
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
                       f"artifact (verdict: pass|fail, nonce, claims_reviewed)")

    if verdict["nonce"] not in {t["nonce"] for t in tickets}:
        return False, ("the audit verdict's nonce does not match any auditor spawn recorded "
                       "for this run — the verdict must carry the nonce issued to the auditor "
                       "at spawn time")

    if verdict["verdict"] != VERDICT_PASS:
        findings = "; ".join(verdict["findings"][:5]) or "no findings recorded"
        return False, f"the independent audit FAILED: {findings}"

    reviewed = set(verdict["claims_reviewed"])
    missed = [c for c in approved_claims if c not in reviewed]
    if missed:
        return False, (f"the audit did not review {len(missed)} approved claim(s) "
                       f"({', '.join(missed[:5])}) — every approved claim's Fold-1 citation "
                       f"must be re-read by the auditor")

    if current_pass is not None:
        # The audit must certify the CURRENT state of the run, not an earlier one. A verdict from
        # a spawn several passes ago was written against a different claim graph.
        matching = [t for t in tickets if t["nonce"] == verdict["nonce"]]
        stale = [t for t in matching if t["pass"] < current_pass]
        if matching and len(stale) == len(matching):
            return False, (f"the audit is STALE: it was performed at pass {stale[0]['pass']} but "
                           f"the run is now at pass {current_pass}, so the claim graph has "
                           f"changed since it was reviewed — re-run the audit")

    return True, "independent audit passed"
