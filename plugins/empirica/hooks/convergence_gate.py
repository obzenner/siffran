#!/usr/bin/env python3
"""Stop-hook convergence gate (ADR-7, ADR-8).

Blocks completion while any claim in the run's claim graph is unresolved. Covered by the
committed regression suite in `tests/test_hooks.py`.

Contract (verified against code.claude.com/docs/en/hooks + plugins-reference,
re-verified 2026-07-22 — the canonical Stop-block mechanism is exit code 2, NOT a
stdout `decision` field, which the current Stop spec does not honor):
  stdin  : JSON, at least {"cwd": str, ...}
  block  → write the reason to STDERR and exit 2 (Claude reads stderr, keeps going).
  allow  → exit 0 (stdout {"continue": true} is informational only).

State substrate (ADR-22, superseding the markdown spec of ADR-15): the run's internal working
memory is a JSON CLAIM GRAPH — a GSN argument with in-toto evidence leaves — held in the run
directory (`.claude/empirica/<run_id>/claims.json`) and located via the manifest's
`graph_path`. Claim state is DERIVED from evidence, never read from the file, so a model can
no longer reach convergence by typing a confidence number: see claimgraph.py's module docs.
A claim the agent genuinely cannot resolve is surfaced to the human with a residual tag from
the closed set {needs-decision, needs-data, needs-experiment, needs-budget}; blocked claims
stop gating (they are a residual for the human, not a loop to spin on) but they are NEVER
reported as convergence.

Evidence (ADR-20 P3): a claim may only be approved when it has the evidence it owes — Fold 1
(a research citation to a source outside the model's training data) for every claim, plus
Fold 2 (a harness-written passing spike) for `needs-experiment` claims. evidence.py is the
judge of that; this gate consumes its verdict and reports WHICH fold is missing.

Independent audit (ADR-20 P6): a converged claim graph is NECESSARY but not SUFFICIENT for a
run to report `converged` — a separate principal must also have written a passing verdict
(audit.py). A run that stops with residuals or an exhausted budget is exempt: it is not
claiming convergence, so there is nothing to certify.

Convergence reporting (ADR-17): when the gate allows the stop, it reports whether the run
truly CONVERGED (no unknowns blocked) or merely STOPPED with residuals. A budget-exhausted
run (`blocked: needs-budget`) allows the stop but is flagged `converged: false` — the gate
never lets budget exhaustion fabricate a green result.

Identity and fail direction (ADR-19 active-run manifest): the manifest is the sole signal
that a session is an empirica run. This matrix is load-bearing and unchanged by the substrate
move — only the file it points at changed.
  - no manifest         → not an empirica run → fail OPEN (never wedge an unrelated session)
  - manifest corrupt    → fail CLOSED (corruption of the record that proves a run is live)
  - active run, graph missing/corrupt → fail CLOSED (the graph was deleted/tampered)
  - status ≠ active (already stopped/converged) → fail OPEN (done, don't re-block)
  - LEGACY manifest (pre-ADR-22: spec_path, no graph_path) → fail OPEN with a note. A run
    that started under the markdown substrate cannot be evaluated by this code, and ADR-19's
    "never wedge a session" outranks gating a run we cannot read.
  - unscored / malformed / out-of-range confidence → treated as 0.0 → BLOCKS
  - confidence ≥ θ but the required evidence folds are missing → BLOCKS
    (absence of proof is not proof of convergence)

Termination (ADR-19, refines ADR-9): on an active run the gate ticks a monotone pass
counter each time it would block. The well-founded variant `max_passes - passes` over
(ℕ, <) guarantees the loop stops in ≤ max_passes passes whether or not it converges — at
the cap the gate records `stopped_residual` and allows the stop as honestly non-converged,
rather than grinding to the platform's forced 8-block override.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

DEFAULT_THETA = 0.8


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


manifest = _load("manifest")
claimgraph = _load("claimgraph")
evidence = _load("evidence")
audit = _load("audit")

# The CLOSED set of residual tags that legitimately stop gating (ADR-9/17) now lives with the
# schema that owns it. Re-exported here because state_restore.py and the tests read it from
# the gate, and one definition beats two that can drift.
VALID_BLOCKED_TAGS = claimgraph.VALID_BLOCKED_TAGS


def theta() -> float:
    """θ from env, guarded: a malformed EMPIRICA_THETA falls back to the default,
    never crashes the hook at import (adversarial review — env-var crash surface)."""
    try:
        value = float(os.environ.get("EMPIRICA_THETA", str(DEFAULT_THETA)))
    except ValueError:
        return DEFAULT_THETA
    return value if 0.0 <= value <= 1.0 else DEFAULT_THETA


def graph_path_for(cwd: Path, session_id: str, run: dict | None) -> Path:
    """The claim graph's path. The graph is the run's internal working memory and lives in the
    run directory (`.claude/empirica/<run_id>/claims.json`); the manifest records it in
    `graph_path`. The run directory is the graph's ONLY home, so a manifest-provided path is
    honoured only when it resolves inside that directory — a `graph_path` pointing elsewhere
    (a corrupt manifest, or one rewritten to aim the gate at a pre-'converged' file outside
    the run) is rejected in favour of the canonical default. The graph is never a repository
    file."""
    default = manifest.default_graph_path(cwd, session_id)
    recorded = run.get("graph_path") if run else None
    if isinstance(recorded, str) and recorded:
        candidate = Path(recorded)
        run_dir = manifest.locate_run_dir(cwd, session_id).resolve()
        try:
            candidate.resolve().relative_to(run_dir)
            return candidate
        except ValueError:
            pass  # outside the run directory → not trusted; fall back to the canonical graph
    return default


def _resolve_run(cwd: Path, session_id: object) -> tuple[Path | None, dict | None]:
    """Locate and read this session's active-run manifest (ADR-19).

    Returns (manifest_path, run). run is:
      * None            — no session id OR no manifest file → NOT an empirica run
      * {"__corrupt__"} — a manifest exists but is unparseable → caller fails CLOSED
      * a normalised dict — a well-formed manifest
    The Stop payload carries `session_id` as a common hook field; when it is absent (e.g.
    a bare unit invocation) we behave exactly as before ADR-19 — no manifest, no identity.
    """
    if not isinstance(session_id, str) or not session_id:
        return None, None
    path = manifest.locate_run(cwd, session_id)
    return path, manifest.read_run(path)


def _allow_converged(graph: dict, th: float, evidence_ok) -> tuple[int, dict]:
    """The allow-path payload: truly converged ⇔ no residuals; residuals ⇒ stopped.

    A blocked residual is never reported as convergence — ADR-17's "never fabricate green".
    """
    if claimgraph.root_is_refuted(graph, th, evidence_ok):
        # The run disproved its own intent. Allow the stop — looping on a refuted goal is
        # pointless — but never as convergence. This is a finding to hand to the human, and
        # without this branch a single forged refutation of the top goal converged the run
        # vacuously (adversarial review finding).
        return 0, {"continue": True, "converged": False,
                   "note": ("NON-CONVERGED: the run's TOP GOAL was refuted by evidence. The "
                            "intent as stated cannot be established — surface the refutation "
                            "and its evidence to the human. This is a result, not a green run.")}
    blocked = claimgraph.blocked_residuals(graph, th, evidence_ok)
    budget_blocked = [nid for nid in blocked
                      if graph["nodes"][nid]["blocked"] == "needs-budget"]
    out: dict[str, object] = {"continue": True, "converged": not blocked}
    if budget_blocked:
        out["note"] = (f"NON-CONVERGED: budget exhausted, {len(budget_blocked)} claim(s) "
                       f"unresolved (blocked: needs-budget). Raise the budget to continue.")
    elif blocked:
        out["note"] = f"{len(blocked)} claim(s) surfaced to human (blocked), not gated"
    return 0, out


def _block_reason(graph: dict, run_dir: Path, open_claims: list[str], th: float,
                  passes_note: str) -> str:
    """The message the agent reads when the gate blocks.

    It names, per claim, WHICH fold of evidence is missing rather than only reporting a score,
    because "your confidence is too low" is not actionable while "no research citation exists
    for this claim" is. This is the gate teaching the protocol at the point of failure.
    """
    lines = []
    for nid in open_claims[:10]:
        node = graph["nodes"][nid]
        why = evidence.explain(run_dir, graph, nid)
        lines.append(f"  - [{nid}] {node['text'][:80]} (confidence {node['confidence']:.2f}) "
                     f"→ {why}")
    more = (f"\n  … (+{len(open_claims) - 10} more)" if len(open_claims) > 10 else "")
    return (
        f"Convergence not reached: {len(open_claims)} claim(s) not yet terminal "
        f"(θ={th}){passes_note}.\n" + "\n".join(lines) + more +
        "\n\nEach claim needs: FOLD 1 — a research citation to a source outside your training "
        "data (fetched docs, code you actually read, runtime output); plus FOLD 2 for a "
        "needs-experiment claim — a passing spike recorded by spike_harness.py from a real "
        "exit code. Recall is not evidence (ADR-20 P3). If a claim is genuinely unresolvable, "
        "tag it blocked: needs-decision|needs-data|needs-experiment to surface it to the human "
        "instead of looping (ADR-9); if budget is exhausted, blocked: needs-budget (ADR-17). "
        "If evidence REFUTES a claim, record the refutation and discard it — do not park it "
        "at low confidence."
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}
    cwd = Path(str(payload.get("cwd") or "."))
    session_id = payload.get("session_id")
    run_path, run = _resolve_run(cwd, session_id)
    is_active = bool(run) and run.get("status") == "active"

    # --- Identity + fail direction (ADR-19) ---------------------------------
    # The active-run manifest is the sole signal that this is an empirica run. A session with
    # no manifest is not an empirica run — fail OPEN, never wedge it.
    if run is None:
        print(json.dumps({"continue": True}))
        return 0

    if run.get("status") == "__corrupt__":
        # An active run whose manifest is corrupt → fail CLOSED: corruption of the record
        # that proves a run is live is exactly when you want the gate.
        print("empirica: active-run manifest is corrupt; refusing to stop until run state "
              "can be read (fail-closed, ADR-19).", file=sys.stderr)
        return 2

    if not is_active:
        # Manifest says this run already stopped/converged — don't re-block a finished run.
        print(json.dumps({"continue": True, "converged": run.get("status") == "converged"}))
        return 0

    graph_path = graph_path_for(cwd, str(session_id), run)
    graph = claimgraph.load(graph_path)

    if manifest.is_legacy(run) and graph is None:
        # A run that started under the pre-ADR-22 markdown substrate: legacy-shaped manifest
        # AND no claim graph to evaluate. This code cannot judge its state, and wedging a live
        # session is the one thing ADR-19 forbids outright — so fail OPEN, loudly, and let the
        # user restart the run.
        #
        # The `graph is None` conjunct is load-bearing. Testing the manifest SHAPE alone made
        # this an escape hatch: drop `graph_path` and add `spec_path` in the manifest and any
        # actively-blocking run walked free, defeating the fail-closed gate (reproduced, then
        # found again by an independent doc audit). A run that HAS a claim graph is evaluated on
        # that graph no matter what shape its manifest is in.
        print(json.dumps({
            "continue": True, "converged": False,
            "note": ("NON-CONVERGED: this run predates the claim-graph substrate (ADR-22) and "
                     "cannot be evaluated by the current gate. Re-invoke /empirica to start a "
                     "run on the new substrate."),
        }))
        return 0

    if graph is None:
        # The run's claim graph vanished (deleted/renamed to bypass convergence) → CLOSED.
        print(f"empirica: active run but the claim graph is missing ({graph_path}); refusing "
              f"to stop — restore it in the run directory (fail-closed, ADR-19).",
              file=sys.stderr)
        return 2

    if graph == claimgraph.CORRUPT:
        # Present but unreadable/structurally invalid → CLOSED. A malformed argument is not a
        # converged one, and this is exactly the tamper case the sentinel exists for.
        print(f"empirica: the claim graph ({graph_path.name}) is unreadable or structurally "
              f"invalid; refusing to stop until convergence can be evaluated (fail-closed, "
              f"ADR-19/22).", file=sys.stderr)
        return 2

    th = theta()
    run_dir = graph_path.parent
    # The two-fold evidence verdict (ADR-20 P3). Claim state is derived through this oracle,
    # so a typed confidence with no evidence cannot reach `approved`.
    evidence_ok = evidence.oracle(run_dir, graph)
    open_claims = claimgraph.pending(graph, th, evidence_ok)

    if not open_claims:
        code, out = _allow_converged(graph, th, evidence_ok)
        if out["converged"]:
            # P6: a run may only REPORT convergence after an independent principal has
            # verified it. Residual/budget-exhausted stops are exempt — they do not claim
            # convergence, so there is nothing for an auditor to certify, and requiring one
            # would wedge a run that has honestly given up.
            approved = [nid for nid in claimgraph.gating_goals(graph, th, evidence_ok)
                        if claimgraph.state_of(graph, nid, th, evidence_ok)
                        == claimgraph.STATE_APPROVED]
            # Pass the current pass count so a verdict from an earlier pass is rejected as
            # stale — it reviewed a claim graph that has since changed.
            audit_ok, audit_reason = audit.check(run_dir, approved, run.get("passes"))
            # P1 is evaluated on BOTH paths. It used to be computed only in the failure branch,
            # which meant a route-before-investigate violation vanished the moment the audit
            # passed — falsifying ADR-20's fitness function 3 ("a run whose route was declared
            # after investigation is flagged") in exactly the case that matters: a rubber-stamped
            # audit over an inverted run. Found by an independent coverage review.
            route_verdict, route_issue = audit.stamps_route_verdict(run)
            route_issue = None if route_verdict == "ok" else route_issue
            if not audit_ok:
                # P1 is surfaced to the auditor rather than hard-blocked here: the stamp can be
                # coarse, and a coarse signal should not be the sole reason a run wedges.
                p1_note = (f"\n\nAlso flag for the auditor (ADR-20 P1): {route_issue}."
                           if route_issue else "")
                print(f"Claim graph is converged, but the run may not report `converged`: "
                      f"{audit_reason}.\n\nThe auditor must re-read each approved claim's "
                      f"Fold-1 citation and confirm the cited source actually supports the "
                      f"claim, then write its verdict to "
                      f"{audit.verdict_path(run_dir).name} carrying the nonce from its spawn "
                      f"(ADR-20 P6).{p1_note}", file=sys.stderr)
                if is_active:
                    manifest.record_pass(run_path)  # an audit round is a pass; keep terminating
                    if manifest.at_cap(manifest.read_run(run_path)):
                        manifest.set_status(run_path, "stopped_residual")
                        print(json.dumps({
                            "continue": True, "converged": False,
                            "note": ("NON-CONVERGED: claim graph converged but the independent "
                                     "audit never passed within max_passes."),
                        }))
                        return 0
                return 2
            out["audit"] = "passed"
            if route_issue:
                # The audit passed but P1 is not clean. Report it in the RESULT, not just in a
                # block message the agent may never see: a passing audit must not launder a P1
                # problem. Not fatal (the stamp can be coarse — see route_note), so the stop is
                # allowed, but it goes on the record and `converged` is qualified, not clean.
                #
                # A proven inversion and an unverifiable ordering are reported under DIFFERENT
                # keys. Filing "could not tell" as `p1_violation` would accuse a compliant run,
                # while calling it clean would hide that nothing was checked — both are lies of
                # the kind this gate exists to prevent.
                if route_verdict == "violation":
                    out["p1_violation"] = route_issue
                    out["note"] = (f"Audit passed, but ADR-20 P1 was violated: {route_issue}. "
                                   f"Routing is a commitment made up front, not a label applied "
                                   f"retroactively.")
                else:
                    out["p1_unverified"] = route_issue
                    out["note"] = (f"Audit passed. ADR-20 P1 could not be verified: "
                                   f"{route_issue}.")
        if is_active:  # record the terminal status so a later Stop fails open, not re-blocks
            manifest.set_phase(run_path, "converged" if out["converged"] else "assess")
            manifest.set_status(run_path, "converged" if out["converged"] else "stopped_residual")
        print(json.dumps(out))
        return code

    # --- Still converging → BLOCK, but tick the termination variant (ADR-19) ---
    if is_active:
        run = manifest.record_pass(run_path)  # monotone +1; variant strictly decreases
        if manifest.at_cap(run):
            # Pass budget exhausted: stop HONESTLY as non-converged rather than grind to the
            # platform's forced 8-block override. The variant guarantees we reach here.
            manifest.set_status(run_path, "stopped_residual")
            print(json.dumps({
                "continue": True, "converged": False,
                "note": (f"NON-CONVERGED: reached max_passes={run['max_passes']} with "
                         f"{len(open_claims)} claim(s) still not terminal (θ={th}). Loop "
                         f"terminated by the pass-count variant (ADR-19). Raise "
                         f"EMPIRICA_MAX_PASSES or resolve/blocked-tag the remaining claims."),
            }))
            return 0

    passes_note = (f" [pass {run['passes']}/{run['max_passes']}]" if is_active else "")
    print(_block_reason(graph, run_dir, open_claims, th, passes_note), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
