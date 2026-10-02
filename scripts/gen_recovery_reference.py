#!/usr/bin/env python3
"""Generate ``references/recovery.md`` for the Empirica skill from the public contract.

Every reason code and its ``next_actions`` are read from
``contracts/empirica/v2/public-contract.json``; next actions are rendered exactly as the author
view renders them. The one author sentence per reason lives in ``AUTHOR_GUIDANCE`` below and must
cover the contract's reason codes exactly, so a new or removed code fails here until it is written.

Usage: gen_recovery_reference.py [--check]   (default writes the file)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "empirica"))

from adapters.author_view import render_surface  # noqa: E402

CONTRACT = ROOT / "contracts" / "empirica" / "v2" / "public-contract.json"
OUTPUT = ROOT / "plugins" / "empirica" / "skills" / "empirica" / "references" / "recovery.md"

AUTHOR_GUIDANCE = {
    "run.no_active": "Start a fresh run; nothing from a missing run can be reused.",
    "run.goal_required": "Restate the task as a non-empty goal and start again; no run was created.",
    "run.terminal": "The run is finished: read its residuals with `GetRun` and report them; submit no more actions.",
    "run.corrupt": "Stop using this run and start a fresh one; corrupt state is never repaired.",
    "graph.missing": "Submit a claim graph from the supplied context before sizing the run; no investigation is needed first.",
    "graph.invalid": "Read the run, then resubmit a closed, root-connected graph that matches the tool schema; do not change claims inside a frozen scope.",
    "route.required": "Record the route before any investigation.",
    "investigation.required": "Once the run is approved, record the investigate witness before any native read, command, evidence, or child.",
    "route.late": "The route came after investigation began and cannot be repaired: read the residuals, then stop or start a fresh run.",
    "claim.research_missing": "Record cited research for the named claim; it may support or refute it.",
    "claim.research_unbound": "The research cites an earlier claim text: record new research for the current claim text.",
    "claim.spike_missing": "Request a deterministic spike for the named `needs-experiment` claim.",
    "claim.spike_prerequisite_missing": "Record supporting research for the claim first, then request the spike.",
    "claim.spike_stale": "The spike's inputs changed: request it again against the current research and dependent files.",
    "claim.human_decision": "Ask the human for the decision; no author action settles a `needs-decision` claim.",
    "claim.refuted": "Active evidence refutes the claim: read the run, then discard or restate the claim in a new graph.",
    "evidence.conflict": "Supporting and refuting research both exist for the claim's current text, and more research cannot clear it: revise or discard the contradicted claim (a frozen claim cannot be rewritten) and research the revised claim, or stop with the residual; never edit a claim cosmetically to evade adverse evidence.",
    "budget.exhausted": "A ceiling is spent: stop honestly with `report_convergence intent=stop`; in deliberative mode you may propose a raise for human approval, in auto mode start a fresh run.",
    "audit.required": "Launch the packaged auditor as your host's instructions say and never reserve the child yourself; on Codex, end the evidence-complete turn so Stop rejects the unsupported audit.",
    "audit.pending": "Do nothing: the host is settling the audit child; never poll or respawn.",
    "audit.unreadable": "The auditor returned no valid verdict: retry within the audit ceiling (normally once), otherwise stop.",
    "audit.failed": "Address every finding listed under `Audit:`, then retry within the audit ceiling (normally once); stop if it fails again.",
    "audit.stale": "The graph or evidence changed after the audit: launch a fresh audit of the current scope.",
    "audit.same_model": "The auditor matched a covered producer's model: launch an auditor from a different model, or accept the residual.",
    "audit.independence_unverified": "The host could not verify auditor independence: relaunch under a distinct observed model, or accept the residual.",
    "child.terminal": "A child ended in a terminal state: retry within the ceiling (normally once), or accept the residual and stop.",
    "host.async_unsupported": "This host cannot run the audit asynchronously: run it in the foreground, or accept the residual and stop.",
    "host.audit_output_unobservable": "This host cannot observe the auditor's output, so the audit cannot pass: accept the residual and stop.",
    "host.subagents_missing": "No external pi-subagents runtime is active: install one supported runtime and restart the host, or accept the residual and stop.",
    "host.subagents_tool_inactive": "The `subagent` tool is registered but not active: enable it (`subagents_enable`, or `toolActivation: eager`) and retry; a host restart does not change this. Or accept the residual.",
    "host.subagents_owner_unverified": "The active audit runtime cannot be proven (owner, package, or version unobservable, or a subagent child process): do not retry blindly; restart the host with one supported runtime, or accept the residual.",
    "host.subagents_duplicate_owner": "Two extensions register the `subagent` tool: remove the duplicate, restart the host, and start a fresh run, or accept the residual.",
    "host.subagents_version_unsupported": "The active pi-subagents is not a reviewed version or lacks the launch preflight: install a reviewed version and restart, or accept the residual.",
    "host.subagents_launch_unsupported": "The runtime admits a launch form Empirica cannot correlate: use only the canonical foreground auditor launch, or accept the residual.",
    "host.subagents_provenance_missing": "The run or receipt lacks the exact pi-subagents version and owner path: recapture a provenance-complete run; this one is not evidence.",
    "freeze.deferred": "Deferred claims stay residual: accept them when you stop, or investigate them before freezing.",
    "governance.auto_invocation_required": "Auto needs an interactive invocation or operator-set `EMPIRICA_AUTO_DELEGATION=1`: run interactively or have the operator set it, then start fresh.",
    "governance.approval_required": "Submit a task-sized `configure_run` (all three ceilings plus a 1-600 character rationale) and wait for host approval; nothing is authorized before it.",
    "governance.revision_required": "The configuration changed: resubmit `configure_run` for a new host decision, or stop honestly.",
    "governance.approval_unavailable": "The host could not obtain a decision: do not retry blindly; have the host refresh its governance context or use an approval-capable host, or accept the residual.",
    "governance.auto_ceiling": "Auto never raises ceilings after approval (interactive auto included), and a delegated proposal must fit its actual envelope (8/1/2, or narrower if the operator limited it): reduce scope, or start a fresh run with a larger approved size.",
    "governance.budget_contradictory": "StartRun budgets or operator limits exceed the fixed 8/1/2 delegated-auto policy, or a StartRun budget widens an operator limit (budgets may only narrow): correct the offending source and start a fresh run.",
    "governance.revision_exhausted": "Auto's material-revision allowance is spent: start a fresh run with a larger approved size, or use deliberative mode.",
    "governance.budget_invalid": "A proposed ceiling is below what is already used: resubmit `configure_run` with ceilings at least equal to usage.",
    "governance.stale_proposal": "The decision was bound to an older revision: read the run, then resubmit the current proposal for a new decision.",
    "governance.receipt_replay": "A superseded or conflicting decision was replayed and changed nothing: read the current proposal and act on that.",
    "governance.interaction_limit": "The dialog allowance is spent (3 per configuration, 128 per run): stop honestly or start a fresh run.",
    "governance.decision_conflict": "The submitted control and edited fields disagreed and nothing was approved: use one explicit decision path.",
    "audit.producers_mixed": "Covered evidence has mixed producers: if only the latest spike differs, re-run it under the research producer, otherwise accept the residual.",
}

HEADER = """\
# Recovery reference

Generated from `contracts/empirica/v2/public-contract.json` by `scripts/gen_recovery_reference.py`
(`make empirica-recovery-reference`); do not edit by hand. Reason codes and next actions are the
contract's; the author guidance column is the one sentence to act on. Next actions are shown as the
text view renders them.

| Reason | Next actions | Author guidance |
|---|---|---|
"""


def render(contract: dict) -> str:
    """The reference file content for one contract; guidance must cover its reasons exactly."""
    reasons = contract["reasons"]
    missing, extra = set(reasons) - set(AUTHOR_GUIDANCE), set(AUTHOR_GUIDANCE) - set(reasons)
    if missing or extra:
        raise SystemExit(f"AUTHOR_GUIDANCE drifted from the contract: missing={sorted(missing)} "
                         f"extra={sorted(extra)}")
    rows = (
        f"| `{code}` | " + "; ".join(f"`{render_surface(action)}`" for action in row["next_actions"])
        + f" | {AUTHOR_GUIDANCE[code]} |"
        for code, row in reasons.items())
    return HEADER + "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    args = parser.parse_args()
    want = render(json.loads(CONTRACT.read_text(encoding="utf-8")))
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != want:
            print(f"  FAIL {OUTPUT.relative_to(ROOT)} is stale: run make empirica-recovery-reference",
                  file=sys.stderr)
            return 1
        print("  ok: recovery reference matches the contract")
        return 0
    OUTPUT.write_text(want, encoding="utf-8")
    print(f"  wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
