#!/usr/bin/env python3
"""Pure independent-audit coverage decision — host-neutral (ADR-30).

Extraction of the COMPARISON in `hooks/audit.check` into a package that reads nothing from disk:
the caller reads the auditor's spawn tickets and verdict (both persistence, hence the adapter's
job) and passes them here as already-normalised data. This module only decides whether that
recorded audit COVERS the run's current converged state (ADR-20 P6, ADR-25, ADR-27).

Trust direction is unchanged (ADR-13): a passing audit is a NECESSARY condition for `converged`,
never a sufficient one; the deterministic spike remains the only thing that APPROVES an experiment
claim. This decides only "may the run report converged as far as the audit is concerned".
"""

VERDICT_PASS = "pass"


def coverage_check(tickets: list[dict], verdict: dict | None,
                   approved: dict[str, dict], argument_digest: str | None = None) -> tuple[bool, str]:
    """May this run report `converged`, as far as the independent audit is concerned? (ok, reason).

    `tickets`   — the auditor spawns the harness observed: [{"nonce": str, ...}]. Its presence is
                  the harness-proven half — an audit was actually performed.
    `verdict`   — the auditor's normalised verdict, or None when absent/unreadable:
                  {"verdict": "pass"|"fail", "nonce": str, "argument_digest": str|None,
                   "claims_reviewed": [{"claim_id","claim_digest","evidence_digest"}], "findings": [...]}.
    `approved`  — each approved claim id → the digests it must have been reviewed at:
                  {claim_id: {"claim_digest": ..., "evidence_digest": ...}}. The CALLER computes
                  these from the graph and the evidence store, so this module never has to know how
                  either is stored.
    `argument_digest` — the SHAPE of the claim graph as it stands now (ADR-27); when given, the
                  verdict must carry the same value, because per-claim coverage over the approved
                  set cannot see a claim LEAVING that set.

    Each check closes an observed or plausible bypass; requirement 5 is per (claim, claim_digest,
    evidence_digest) rather than per graph, which is what lets a verdict age gracefully as claims
    are added or reworded (ADR-25).
    """
    if not tickets:
        return False, ("no independent audit was performed: spawn the independent auditor to "
                       "verify this run before converging (ADR-20 P6 — the author cannot grade "
                       "its own convergence)")

    if verdict is None:
        return False, ("an auditor was spawned but no readable verdict is present; the auditor "
                       "must write its verdict artifact (verdict: pass|fail, nonce, and "
                       "claims_reviewed as {claim_id, claim_digest, evidence_digest} entries)")

    if verdict["nonce"] not in {t["nonce"] for t in tickets}:
        return False, ("the audit verdict's nonce does not match any auditor spawn recorded for "
                       "this run — the verdict must carry the nonce issued to the auditor at "
                       "spawn time")

    if verdict["verdict"] != VERDICT_PASS:
        findings = "; ".join(verdict.get("findings", [])[:5]) or "no findings recorded"
        return False, f"the independent audit FAILED: {findings}"

    if argument_digest is not None and verdict.get("argument_digest") != argument_digest:
        # Checked BEFORE per-claim coverage: "the argument changed" is the broader fact, and
        # listing individually-fine claims would hide that the structure holding them together is
        # not the one that was reviewed.
        return False, ("the audit reviewed a DIFFERENT argument than the one on record: the claim "
                       "graph's shape has changed since the verdict was written (a claim was "
                       "added, deleted, re-parented, detached from the goal, blocked or "
                       "discarded), so the whole argument must be re-audited (ADR-27)"
                       if verdict.get("argument_digest") else
                       "the audit verdict does not record which argument it reviewed "
                       "(argument_digest missing or malformed) — it cannot be matched against the "
                       "claim graph on record (ADR-27)")

    reviewed = {e["claim_id"]: e for e in verdict.get("claims_reviewed", [])}
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
        # Each bucket gets its own clause: "not reviewed", "reworded since review" and
        # "re-evidenced since review" call for different work.
        parts = []
        if unreviewed:
            parts.append(f"{len(unreviewed)} approved claim(s) were never reviewed "
                         f"({', '.join(sorted(unreviewed)[:5])})")
        if restated:
            parts.append(f"{len(restated)} claim(s) were REWORDED after review "
                         f"({', '.join(sorted(restated)[:5])}), so the audit answered a different "
                         f"question")
        if re_evidenced:
            parts.append(f"{len(re_evidenced)} claim(s) had their EVIDENCE changed after review "
                         f"({', '.join(sorted(re_evidenced)[:5])}), so the citation the auditor "
                         f"re-read is not the one now supporting the claim")
        return False, ("the audit does not cover the run's current state: " + "; ".join(parts)
                       + " — re-audit these claims (the rest stay reviewed)")

    return True, "independent audit passed"
