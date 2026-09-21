---
name: empirica
description: "Empirical-convergence workflow for non-trivial work whose plan is uncertain. Route before investigating, represent unknowns as claims, require cited research before deterministic spikes, discard refuted claims, and request an independently audited convergence decision. Use for design-and-implement work, architectural uncertainty, competing approaches, and risky assumptions. Host capabilities differ; run the capability preflight before promising convergence. Invoke as /empirica <goal>."
allowed-tools: Read Glob Grep Bash Edit Write Agent TaskCreate TaskUpdate WebFetch
compatibility: Designed for Claude Code, Codex CLI 0.146.0+, and Pi; requires methodologist as a companion and python3 for hook-backed hosts. Execution capability depends on the exact host profile.
argument-hint: "[--cli-exec] [--multi-provider] <goal>"
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

- **Claude Code `claude-code@2.1.278`:** continue when the Empirica hooks and
  public MCP tools are active and trusted; record route first. Canonical audits
  are host-owned async children; a current pending audit settles the parent turn
  until Claude's native completion notification resumes it.
- **Pi `pi@0.84.1+pi-subagents@0.50.0`:** continue when `/empirica` injected an
  opaque handle and `empirica_observe`, `empirica_read`, `report_convergence`,
  and the structured `subagent` tool are present.
- **Codex CLI `codex-cli@0.146.0`:** continue when activation injected the opaque
  handle, the public MCP tools are present, and the Stop hook is trusted; Stop
  owns the managed foreground audit.
- **Unknown or partial surface:** stop as unsupported rather than borrowing
  another profile's capabilities.

A runnable convergence workflow requires all of these capabilities:

1. read the complete public run view, including obligations;
2. submit author actions for route, graph, research, spike request, and freeze;
3. obtain the current audit argument;
4. launch and observe a bound independent auditor;
5. submit the observed verdict through private host ingress;
6. request the guarded convergence decision.

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
3. Announce the route and record `ObserveAction(kind="route")` through the active
   author-action surface.
4. Record `ObserveAction(kind="investigate")` after route acknowledgement and
   before any native read, search, command, evidence submission, or child launch.
5. Then read [references/host-capabilities.md](references/host-capabilities.md)
   for the exact host contract and begin investigation.

If either witness cannot be recorded, the workflow is unsupported. The core and
supported host adapters fail closed before investigative tools, evidence, child
budget, or harness execution; do not continue with an unrecorded substitute.

## 2. Seed the claim graph

Before writing production code, read
[references/claim-graph.md](references/claim-graph.md). Construct the smallest
closed graph whose root is the goal and whose gating claims cover every material
unknown.

Submit the graph through the active author-action surface. Never persist a claim's
state or confidence: claim state is derived from current evidence on every read.
A malformed, detached, missing, or corrupt selected graph fails closed.

After the graph is accepted, use the returned `run.contract` obligations as the
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

- Route before investigation.
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
