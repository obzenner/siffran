---
name: empirica
description: "Empirical-convergence workflow for non-trivial work whose plan is uncertain. Route before investigating, represent unknowns as claims, require cited research before deterministic spikes, discard refuted claims, and request an independently audited convergence decision. Use for design-and-implement work, architectural uncertainty, competing approaches, and risky assumptions. Host capabilities differ; run the capability preflight before promising convergence. Invoke as /empirica <goal>."
allowed-tools: Read Glob Grep Bash Edit Write Agent TaskCreate TaskUpdate WebFetch
compatibility: Designed for Claude Code >=2.1.278,<2.2.0, Codex CLI >=0.146.0,<0.147.0, and Pi >=0.84.1,<0.85.0 with pi-subagents 0.50.0; requires methodologist and python3 for hook-backed hosts. Exact observed versions remain receipt provenance; live capabilities still gate execution.
argument-hint: "[--auto] [--cli-exec] [--multi-provider] <goal>"
---

# Empirica — empirical convergence

Empirica resolves material unknowns before production implementation. It is a
design-time assurance workflow, not a production security boundary. CI remains
the trust boundary for shipped code.

The host-neutral `empirica/v2` service owns policy and state. Host adapters only
translate host events and expose capabilities. Never edit operational state under
`~/.empirica-plugin/` or knowledge under `refs/empirica/*` directly. Never create Empirica state under `.claude/`, `.codex/`, or `.pi/`.

## 0. Adopt the stance and preflight the host

Before anything else, emit this line verbatim:

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are resolved until blocked, then surfaced with what was tried.

Then identify the exact active host surface from the tools and lifecycle already
present in context. **Do not read any file, including a skill reference, before
route acknowledgement.** Use this inline bootstrap matrix:

- **Claude Code capability profile (qualified on `2.1.278`, compatible
  `>=2.1.278,<2.2.0`):** continue when the Empirica hooks and
  public MCP tools are active and trusted; record route first. Canonical audits
  are host-owned async children; a current pending audit settles the parent turn
  until Claude's native completion notification resumes it.
- **Pi capability profile (qualified on `0.84.1`, compatible
  `>=0.84.1,<0.85.0`, with packaged `pi-subagents@0.50.0`):** continue when `/empirica` injected an
  opaque handle and `empirica_observe`, `empirica_read`, `report_convergence`,
  and the structured `subagent` tool are present.
- **Codex CLI observational profile (qualified on `0.146.0`, compatible
  `>=0.146.0,<0.147.0`):** continue when activation injected the opaque
  handle, the public MCP tools are present, and the Stop hook is trusted; Stop
  owns the managed foreground audit.
- **Unknown, partial, or outside a qualified compatibility range:** stop as unsupported pending
  qualification rather than borrowing another profile's capabilities.

The exact observed harness version is provenance recorded in an installed-host receipt; it is not
itself the capability-profile identity and need not equal the qualification baseline.

A runnable convergence workflow requires all of these capabilities:

1. read the complete public run view, including obligations;
2. submit author actions for route, graph, research, spike request, and freeze;
3. obtain the current audit argument;
4. launch and observe a bound independent auditor;
5. submit the observed verdict through private host ingress;
6. request the guarded convergence decision;
7. obtain exact proposal approval through supported host UI (or explicit bounded auto).

If any capability is absent, stop before investigation and report the exact
unsupported capability. Do not imitate the missing operation in prose or write
runtime artifacts by hand.

The base Pi surface without `pi-subagents`, a Codex session with untrusted hooks,
or any host missing one of the three public tools is unsupported. Report the exact
missing capability; do not imitate it in prose or write runtime artifacts by hand.

The user invocation is `$ARGUMENTS`. Leading `--` values are mode flags, not part
of the goal. The host adapter owns parsing. Surface unknown flags and read the
resolved goal and modes only from the public run view when that view is available.

## 1. Route before investigating

Do this before reading files, searching, browsing, or running commands.

1. Restate the goal without mode flags.
2. List each dependency as:
   - **known** — already fixed by evidence you can cite now;
   - **unknown** — requires observation, experiment, or human judgment.
3. Announce the route and record the exact public action
   `{"kind":"route","reason":"<non-empty routing reason>"}` through the active
   author-action surface. `reason` is a top-level action field; do not put it in
   `payload` and do not rename the action kind.
4. Before investigation, propose the exact graph from supplied context only. Claims can name
   discovery uncertainty. Inline shape: `{"root":"G0","claims":[{"id":"G0","text":"<uncertainty>",
   "kind":"ordinary","gating":true}],"edges":[]}`. Larger graphs must be root-connected DAGs
   with edges `{"from":"G0","to":"C1","type":"SupportedBy"}`.
5. Read `empirica_read(operation="GetRun")`. Propose a concrete reviewer with
   `configure_run` when one is not already selected. Empirica never reads or displays a configured
   model catalog: the core only checks that the host-observed main model and reviewer are known,
   normalized, and different. Unknown or same-model pairs block without an exception or fallback.
   Reviewer selection is not proof of availability, separate authorization, or execution; the host
   must later observe the actual reviewer matching the approved selection. Claude and Pi use bounded
   scalar reviewer fields and open a host-owned read-only locked final confirmation after edits.
   Never resend stale configuration fields to reopen review; omitted fields retain the human's
   current proposal. Explicit `--auto` accepts only an author-proposed known-distinct reviewer within
   existing budgets, modes, and revision limits. Do not read project files or skill references to
   prepare the proposal.
   The host-owned human deadline is 900s, with `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` in 1..1500s;
   at most 3 presentations per revision and 128 per run are durably reserved before UI. Timeout
   is dismissal, not human rejection. Do not loop on refusal or exhaustion.
6. On `governance.changes_requested`, read `run.governance.change_request`, draft the requested
   graph/configuration revision, and call `configure_run` for fresh review. The request is guidance,
   never consent or evidence. Do not repeat approval prompts instead of addressing the request.
   Wait for `run.governance.state == "approved"` bound to the exact displayed proposal. Amendments
   need a second review. Both supported hosts own that confirmation; no author action intervenes.
   Approval and change-request text use separate controls. The locked confirmation offers only
   Confirm or Decline; decline keeps edits pending, and feedback remains available on the next
   ordinary review. Human numeric/mode/auditor edits need no prose rationale and are not
   corruption. Preserve them when revising scope. If re-review is needed, call `configure_run`
   with only `kind`, not stale budgets/modes. After cancellation/timeout, wait for the human;
   Claude can settle the turn nonterminally while investigation and convergence remain blocked.
   Then record `{"kind":"investigate"}` before any native read, search,
   command, evidence submission, or child launch. Approval does not supply either witness.
7. Read [references/governance.md](references/governance.md) and
   [references/host-capabilities.md](references/host-capabilities.md), then investigate. Public
   reads/corrections and explicit honest stop remain available without approval.

If either witness cannot be recorded, the workflow is unsupported. The core and
supported host adapters fail closed before investigative tools, evidence, child
budget, or harness execution; do not continue with an unrecorded substitute.

## 2. Refine the approved claim graph

After approval and investigation admission, read
[references/claim-graph.md](references/claim-graph.md). Construct the smallest
closed graph whose root is the goal and whose gating claims cover every material
unknown.

Submit the graph through the active author-action surface. Never persist a claim's
state or confidence: claim state is derived from current evidence on every read.
A malformed, detached, missing, or corrupt selected graph fails closed.

Material graph or configuration changes revoke approval, even after investigation has begun.
Request new host approval through `configure_run`; never reuse old consent. Freeze is not a
substitute for consent.

After the graph is approved, use the returned `run.contract` obligations as the
worklist. Counts and reason strings are telemetry, not authority.

## 3. Earn or discard every gating claim

Read [references/evidence.md](references/evidence.md) before adding evidence.
Apply the folds in order:

1. **Research first for every gating claim.** Record a concrete code, docs,
   runtime, or web source that supports or refutes the exact current claim text.
2. **Spike second only for `needs-experiment`.** After supporting research,
   request a deterministic command and its non-empty dependent-file set through
   the service. The service captures immutable bytes, runs the harness once, and
   derives the gate solely from the process exit code.
3. **Re-gate stale spikes.** If a bound file changes, request the spike again.
4. **Derive the result:**
   - supporting research satisfies Fold 1;
   - refuting research refutes the claim;
   - a current passing spike satisfies Fold 2;
   - absent or stale evidence leaves the claim open;
   - `needs-decision` remains a human residual.

Refuted claims are discarded or replaced with narrower claims. Never preserve a
preferred design by grading contrary evidence away. New claims must attach to the
root and pass through both folds when applicable.

Use only author actions exposed by the current host. Evidence leaves, attribution,
child events, and audit verdicts are trusted host ingress; the author must never
submit or fabricate them.

## 4. Assess one fixed-point pass

After each evidence batch:

1. reread the public run view and obligations;
2. remove refuted branches and add only claims forced by the evidence;
3. identify the next unmet witness;
4. perform that action;
5. end the pass and let the service evaluate the new snapshot.

Do not loop on unchanged state. Do not infer convergence from confidence-like
language. When budgets, a stall, or scope closure matter, read
[references/budget-freeze.md](references/budget-freeze.md).

## 5. Obtain independent audit

When every in-scope gating claim is approved, read
[references/audit.md](references/audit.md).

The author never grades its own convergence. On Claude, invoke the exact
`empirica:empirica-auditor` once and let the parent turn settle while that bound async child is
pending; Claude's native completion notification resumes the workflow after `SubagentStop`
admits the terminal result. On Pi, invoke the exact packaged
`empirica.empirica-auditor`. The host protocol—not `empirica_observe`—owns concrete
reservation, dossier replacement, correlation, identity observation, and terminal admission.
On Codex, do not launch an ordinary child: finish the turn only when all non-audit
obligations are closed so the trusted Stop hook can run its bounded managed auditor.
In every case the host—not the author—binds the dossier, observes the final output,
and admits the candidate verdict.

Audit may block but cannot manufacture deterministic machine evidence. Model
independence is reported as observed, same-model, or unverified; never guaranteed
without host evidence.

If the host lacks bound child observation or private verdict ingress, true
convergence is unsupported. Do not substitute an ordinary model response.

## 6. Request the terminal decision

Call the host's guarded convergence operation exactly once after the graph,
evidence, and scope are current. On Claude and Pi this is `report_convergence`
after the bound audit. On Codex the trusted Stop hook performs the bound managed
audit when due and then requests the guarded decision before permitting completion.

- Only a schema-guarded `Allow` permits reporting the result.
- `Allow(converged=true)` permits a convergence claim.
- `Allow(converged=false)` permits an honest non-converged terminal report.
- `Block`, `Inert` with an active handle, `Fault`, malformed output, or transport
  failure never permits convergence.
- A terminal run is reported, never re-judged or reopened.

Use the default `report_convergence` intent only for convergence. When explicitly accepting current
residual/deferred scope or an exhausted pass budget, call it once with `intent: "stop"`; only the
resulting `Allow(converged=false)` authorizes an honest stopped report.

Do not repeatedly call the gate hoping for a different answer. Follow the typed
residual obligation or next action returned by the service.

## 7. Finalize and hand off

Read [references/handoff.md](references/handoff.md) before producing the final
artifact. Distinguish:

- **converged** — current scoped argument and evidence have a passing bound audit;
- **stopped residual** — unresolved human/data obligations remain;
- **stopped frozen** — committed scope closed, later claims are deferred;
- **stopped budget** — the bounded process ended without convergence;
- **unsupported** — the host lacked a required capability;
- **faulted** — state, artifacts, transport, or protocol failed closed.

Commit only the goal's product artifacts, tests, and accepted decisions. Runtime
state, claim history, evidence records, and audit bindings remain in Empirica's
stores, not in the product tree.

## Non-negotiable invariants

- Route and exact host-mediated proposal approval before investigation.
- Public configuration proposes; only private host decisions approve.
- Explicit auto never raises ceilings and stops at its finite revision limit.
- Research precedes a spike.
- Process exit code is the sole machine approver.
- Claim state is derived; it is never author-assigned.
- Knowledge is append-only and selected history is manifest-reachable.
- Corrupt or missing selected state fails closed.
- Stale file-bound spikes require deterministic re-gating.
- Author-controlled input never becomes trusted ingress.
- First child terminal event wins; a terminal run never later converges.
- Audit can block but cannot approve a machine claim.
- Only the guarded service decision authorizes the final status.
