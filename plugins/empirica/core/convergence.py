#!/usr/bin/env python3
"""The convergence adjudication — the pure decision core (ADR-30).

`adjudicate(...)` is the extraction of the DECISION in `hooks/convergence_gate.main`, with every
host concern removed: no stdin/stdout, no exit codes, no stderr, no filesystem paths, no manifest
writes, no pass-budget bookkeeping. It consumes host-neutral facts and injected pure verdicts and
returns exactly one typed `Decision` (Allow | Block | Inert | Fault).

What is DELIBERATELY not here, and why:
  * Pass-budget termination (record a pass, stop at the cap). That is a persisted loop-bound; the
    core returns `Block` for both "still converging" and "audit failed", and the ADAPTER decides
    whether an exhausted budget turns a `Block` into a non-converged `Allow` instead.
  * The evidence verdict. Its two-fold check reads the live working tree (a spike's file-staleness
    test), so it is inherently host-coupled; it is INJECTED as `evidence(node_id, purpose) ->
    (ok, reason)` rather than reimplemented.
  * The audit verdict, route (P1) verdict, and attribution report. Each reads the run record or the
    evidence store; the adapter computes them with the existing pure hook logic and passes them in.

Behavioural fidelity: composed with an adapter that supplies these injections and applies the pass
cap, this reproduces `convergence_gate.main` exactly — verified by the focused suite in
`tests/test_core.py`.
"""
from . import claims
from .decisions import Allow, Block, ClaimReason, Decision, Fault, Inert

CORRUPT_STATUS = "__corrupt__"  # a run manifest that could not be parsed → fail CLOSED

_BLOCK_GUIDANCE = (
    "Each claim needs FOLD 1 — a research citation to a source outside the model's training data "
    "(fetched docs, code actually read, runtime output) — plus FOLD 2 for a needs-experiment claim: "
    "a passing deterministic spike recorded from a real exit code. Recall is not evidence (ADR-20 "
    "P3). If a claim is genuinely unresolvable, tag it blocked (needs-decision|needs-data|"
    "needs-experiment) to surface it to the human instead of looping; if the budget is exhausted, "
    "needs-budget. If evidence REFUTES a claim, record the refutation and discard it."
)


class RunState:
    """Host-neutral facts about the run's manifest that the adjudicator needs — the small subset of
    the run record, with no paths, session ids, or pass counters attached.

    `status`        — "active" | "converged" | "stopped_residual" | "stopped_frozen" | ... , or
                      CORRUPT_STATUS when the manifest could not be parsed.
    `is_legacy`     — a pre-substrate manifest (spec_path, no graph_path): its state cannot be read.
    `frozen_claims` — the claims that were gating when the run committed its scope (ADR-26), or None
                      when the run is not frozen. `deferred` is derived from this and the graph.
    """

    __slots__ = ("status", "is_legacy", "frozen_claims")

    def __init__(self, status: str, is_legacy: bool = False,
                 frozen_claims: tuple[str, ...] | None = None):
        self.status = status
        self.is_legacy = is_legacy
        self.frozen_claims = frozen_claims


def adjudicate(*, run: RunState | None, graph, theta: float,
               evidence=None, audit=None, route_verdict=("ok", ""),
               digest_of=None, attribution=None) -> Decision:
    """Decide whether an empirica run may stop, and whether it converged. Returns a `Decision`.

    Injections (all pure, all supplied by the adapter):
      `evidence(node_id, purpose) -> (ok, reason)` — the two-fold evidence verdict, or None for a
          bare structural read (nothing can then approve/discard → fails closed).
      `audit(approved_digests, argument_digest) -> (ok, reason)` — the independent-audit coverage
          decision (see `core.audit.coverage_check`). Called ONLY when an audit is owed.
      `route_verdict` — the P1 ordering verdict `(verdict, reason)` where verdict is
          "ok"|"violation"|"inconclusive".
      `digest_of(node_id) -> {"claim_digest", "evidence_digest"}` — the digests an approved claim
          must have been reviewed at. Called only for approved claims when an audit is owed.
    """
    if run is None:
        return Inert()
    if run.status == CORRUPT_STATUS:
        return Fault("the active-run manifest is corrupt; run state cannot be read (fail closed)")
    if run.status != "active":
        # Already stopped/converged — don't re-judge a finished run.
        return Allow(converged=(run.status == "converged"), status="finished")

    # --- active run: classify the graph (ADR-19 fail matrix) ---
    if run.is_legacy and graph is None:
        return Allow(converged=False, status="legacy",
                     note=("NON-CONVERGED: this run predates the claim-graph substrate (ADR-22) "
                           "and cannot be evaluated by the current core. Start a fresh run on the "
                           "new substrate."))
    if graph is None:
        return Fault("active run but the claim graph is missing; refusing to stop (fail closed)")
    if graph == claims.CORRUPT:
        return Fault("the claim graph is unreadable or structurally invalid; refusing to stop "
                     "until convergence can be evaluated (fail closed)")

    ev_ok = (lambda nid, purpose: evidence(nid, purpose)[0]) if evidence is not None else None
    gating = claims.gating_goals(graph, theta, ev_ok)
    open_claims = claims.pending(graph, theta, ev_ok)

    # ADR-26 freeze: claims derived AFTER the run committed its scope do not gate; they are reported
    # as deferred instead. Only claims already gating at freeze time must reach a terminal state.
    frozen = run.frozen_claims
    deferred = [nid for nid in gating if nid not in set(frozen)] if frozen is not None else []
    if deferred:
        deferred_set = set(deferred)
        open_claims = [nid for nid in open_claims if nid not in deferred_set]

    if open_claims:
        return _blocked_converging(graph, theta, evidence, open_claims)
    return _decide_terminal(graph, theta, ev_ok, gating, deferred,
                            audit, route_verdict, digest_of, attribution)


def _blocked_converging(graph, theta, evidence, open_claims) -> Block:
    """Claims are still open → keep going. Names, per claim, WHICH fold is missing, because "your
    confidence is too low" is not actionable while "no research citation exists" is."""
    details = []
    for nid in open_claims[:10]:
        node = graph["nodes"][nid]
        why = evidence(nid, "approve")[1] if evidence is not None else "no evidence oracle supplied"
        details.append(ClaimReason(claim_id=nid, text=node["text"],
                                    confidence=node["confidence"], reason=why))
    more = f" (+{len(open_claims) - 10} more)" if len(open_claims) > 10 else ""
    reason = (f"Convergence not reached: {len(open_claims)} claim(s) not yet terminal "
              f"(θ={theta}){more}. {_BLOCK_GUIDANCE}")
    return Block(kind="converging", reason=reason, open_claims=tuple(details))


def _decide_terminal(graph, theta, ev_ok, gating, deferred, audit, route_verdict,
                     digest_of, attribution) -> Decision:
    """No claim is still gating. Decide whether that is convergence, a residual/frozen/refuted stop,
    or — when an audit is owed but fails — a block. Mirrors `_allow_converged` + the audit branch of
    `convergence_gate.main`.
    """
    if claims.root_is_refuted(graph, theta, ev_ok):
        # The run disproved its own intent. Allow the stop — looping on a refuted goal is pointless
        # — but NEVER as convergence: this is a finding for the human, not a green run.
        status = "stopped_frozen" if deferred else "stopped_residual"
        return Allow(converged=False, status=status, root_refuted=True,
                     deferred=tuple(sorted(deferred)),
                     note=("NON-CONVERGED: the run's TOP GOAL was refuted by evidence. The intent "
                           "as stated cannot be established — surface the refutation and its "
                           "evidence to the human. This is a result, not a green run."))

    deferred_set = set(deferred)
    blocked = [nid for nid in claims.blocked_residuals(graph, theta, ev_ok)
               if nid not in deferred_set]
    budget_blocked = [nid for nid in blocked
                      if graph["nodes"][nid]["blocked"] == "needs-budget"]
    converged = not blocked and not deferred
    # A blocked residual claims nothing, so there is nothing for an auditor to certify. A FROZEN run
    # is NOT exempt: it asserts it discharged its committed scope, and that assertion is what the
    # auditor judges (ADR-26) — so an audit is owed whenever nothing is blocked, converged or not.
    audit_owed = not blocked

    note = None
    if deferred:
        note = (f"NON-CONVERGED (frozen): the run discharged the scope it committed to at freeze "
                f"time; {len(deferred)} claim(s) derived AFTER the freeze are deferred to a next "
                f"run, not resolved: {', '.join(sorted(deferred)[:10])}.")
    elif budget_blocked:
        note = (f"NON-CONVERGED: budget exhausted, {len(budget_blocked)} claim(s) unresolved "
                f"(blocked: needs-budget). Raise the budget to continue.")
    elif blocked:
        note = f"{len(blocked)} claim(s) surfaced to human (blocked), not gated"

    audit_field = None
    attr = None
    p1_violation = p1_unverified = None
    if audit_owed:
        approved = [nid for nid in gating
                    if claims.state_of(graph, nid, theta, ev_ok) == claims.STATE_APPROVED]
        approved_digests = {nid: digest_of(nid) for nid in approved} if digest_of else {}
        audit_ok, audit_reason = (audit(approved_digests, claims.argument_digest(graph))
                                  if audit is not None else
                                  (False, "no audit verdict oracle supplied"))
        route_v, route_r = route_verdict
        route_issue = None if route_v == "ok" else route_r
        if not audit_ok:
            p1_note = (f"Also flag for the auditor (ADR-20 P1): {route_issue}."
                       if route_issue else None)
            reason = (f"Claim graph is converged, but the run may not report converged: "
                      f"{audit_reason}. The auditor must re-read each approved claim's Fold-1 "
                      f"citation, confirm the source supports the claim, and write a verdict "
                      f"carrying the nonce from its spawn (ADR-20 P6, ADR-25).")
            return Block(kind="audit_failed", reason=reason, audit_reason=audit_reason,
                         p1_note=p1_note, frozen=tuple(sorted(deferred)),
                         frozen_count=len(deferred))
        audit_field = "passed"
        if attribution and (attribution.get("findings")
                            or attribution.get("coverage", {}).get("vacuous")):
            attr = attribution
        if route_issue:
            # A passing audit must not launder a P1 problem: report it in the RESULT, under distinct
            # keys for a proven inversion vs. an unverifiable ordering, and qualify — never overwrite
            # — a note the frozen path already set.
            if route_v == "violation":
                p1_violation = route_issue
                p1_text = (f"Audit passed, but ADR-20 P1 was violated: {route_issue}. Routing is a "
                           f"commitment made up front, not a label applied retroactively.")
            else:
                p1_unverified = route_issue
                p1_text = f"Audit passed. ADR-20 P1 could not be verified: {route_issue}."
            note = f"{note} {p1_text}" if note else p1_text

    status = "converged" if converged else ("stopped_frozen" if deferred else "stopped_residual")
    return Allow(converged=converged, status=status, note=note,
                 deferred=tuple(sorted(deferred)), blocked=tuple(sorted(blocked)),
                 budget_blocked=tuple(sorted(budget_blocked)), audit=audit_field,
                 attribution=attr, p1_violation=p1_violation, p1_unverified=p1_unverified)
