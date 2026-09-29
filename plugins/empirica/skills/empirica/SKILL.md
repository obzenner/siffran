---
name: empirica
description: "Empirical-convergence workflow for non-trivial work whose plan is uncertain. Route before investigating, represent unknowns as claims, require cited research before deterministic spikes, discard refuted claims, and request an independently audited convergence decision. Use for design-and-implement work, architectural uncertainty, competing approaches, and risky assumptions. Host capabilities differ; run the capability preflight before promising convergence. Invoke as /empirica <goal>."
allowed-tools: Read Glob Grep Bash Edit Write Agent TaskCreate TaskUpdate WebFetch
compatibility: Designed for Claude Code >=2.1.278,<2.2.0, Codex CLI >=0.146.0,<0.147.0, and Pi >=0.84.1,<0.85.0 with pi-subagents 0.50.0; requires python3 for hook-backed hosts. Exact observed versions remain receipt provenance; live capabilities still gate execution.
argument-hint: "[--auto] <goal>"
---

# Empirica — empirical convergence

Empirica resolves material unknowns before production implementation. It is a
design-time assurance workflow, not a production security boundary. CI remains
the trust boundary for shipped code.

The host-neutral `empirica/v2` service owns policy and state. Host adapters only
translate host events and expose capabilities. Never edit operational state under
`~/.empirica-plugin/` or knowledge under `refs/empirica/*` directly. Never create Empirica state under `.claude/`, `.codex/`, or `.pi/`.

## 0. Adopt the stance and preflight the host

Treat training-weight knowledge as hypothesis: discharge load-bearing claims against evidence or surface them as unverified.

Identify the exact active host surface from the tools and lifecycle already present in context.
**Do not read any file, including a skill reference, before the `investigate` witness is admitted
(route → graph → approved configuration).** Use this inline bootstrap matrix:

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
  handle, the public MCP tools are present, and the Stop hook is trusted; audit
  remains explicitly unsupported because verdict-producing identity is unobservable.
- **Unknown, partial, or outside a qualified compatibility range:** stop as unsupported pending
  qualification rather than borrowing another profile's capabilities. After admission, consult
[references/host-capabilities.md](references/host-capabilities.md) only to diagnose a capability failure.

The three public tools return one deterministic plain-text author view on Claude, Codex, and Pi.
Read its first-line status/governance summary, `Reasons`, `Open obligations`, and rendered `Next`
calls directly; do not parse it as JSON or look for hidden digests/governance internals. Pi retains
validated structured details for host UI only. The host binds and injects the private audit dossier;
`GetArgument` is text for authors recovering a graph, while `GetContract` is text for inspection.

A runnable convergence workflow requires all of these capabilities:

1. read the complete public run view, including obligations;
2. submit author actions for route, graph, research, spike request, and freeze;
3. obtain the current audit argument through the host-bound workflow;
4. launch and observe a bound independent auditor;
5. submit the observed verdict through private host ingress;
6. request the guarded convergence decision;
7. obtain exact run-configuration approval through supported host UI (or explicit bounded auto).

If any capability is absent — including the base Pi surface without `pi-subagents`, a Codex
session with untrusted hooks, or any host missing one of the three public tools — stop before
investigation and report the exact unsupported capability. Do not imitate the missing operation
in prose or write runtime artifacts by hand.

The user invocation is `$ARGUMENTS`. Leading `--` values are invocation flags, not part
of the goal. The host adapter owns parsing. Surface unknown flags and read the
resolved goal only from the public run view when that view is available.

External actor CLIs are not metered or attributed in 4.0 (ADR 0061).

## 1. Route before investigating

Do this before reading files, searching, browsing, or running commands.

1. Restate the goal without invocation flags.
2. List each dependency as:
   - **known** — already fixed by evidence you can cite now;
   - **unknown** — requires observation, experiment, or human judgment.
3. Announce the route and record the exact author action
   `{"kind":"route","reason":"<non-empty routing reason>"}` through the active
   author-action surface; `reason` is a top-level field.
4. Before investigation, propose the smallest claim graph from supplied context
   only; claims can name discovery uncertainty. Larger graphs must be
   root-connected DAGs. Take exact action and graph shapes from the `empirica_observe`
   tool input schema, which carries every closed action shape (including the graph
   payload); do not guess field names or nesting. Contract reads are optional
   explanation, never the source of a shape.
5. Read `empirica_read(operation="GetRun")`, then call `configure_run` to request approval of
   run configuration only: budget ceilings and control mode. The goal is displayed read-only;
   the claim graph and reviewer are not approvable. Preserve the human's edited values rather than
   resending stale fields, and note explicit `--auto` stays within existing budgets and
   cannot raise ceilings.
   The host-owned timeout and presentation limits are fixed by the contract and displayed read-only.
   Timeout is dismissal, not rejection. Do not loop on refusal or exhaustion. The optional
   [governance reference](references/governance.md) documents host-owned recovery details; it is
   not a prerequisite read.
6. Wait until the text view's governance summary says `approved` for the exact displayed
   configuration; do not expect or parse a `run.governance.state` JSON field.
   Configuration amendments need a second review; human numeric edits are not corruption.
   After cancellation/timeout, wait for the human; Claude can settle the turn nonterminally while
   investigation and convergence remain blocked. Then record the `investigate` witness `{"kind":"investigate"}` before any
   native read, search, command, evidence submission, or child launch. Approval does not supply
   either witness.
7. Investigate. Public reads/corrections and explicit honest stop remain available without approval.

If either witness cannot be recorded, the workflow is unsupported. The core and
supported host adapters fail closed before investigative tools, evidence, child
budget, or harness execution; do not continue with an unrecorded substitute.

## 2. Refine the claim graph

After approval and investigation admission, read
[references/claim-graph.md](references/claim-graph.md). Construct the smallest
closed graph whose root is the goal and whose gating claims cover every material
unknown.

Submit the graph through the active author-action surface. Never persist a claim's
state or confidence: claim state is derived from current evidence on every read.
A malformed, detached, missing, or corrupt selected graph fails closed.

Graph changes do not revoke configuration approval. They do change the argument binding and
invalidate prior audit coverage, so obtain a fresh bound audit after any graph change. Configuration
changes require a fresh host decision through `configure_run`. Freeze is not a substitute for either
configuration authority or current audit coverage.

After the graph is recorded, use the returned open obligations and rendered next actions as the
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

When every in-scope gating claim is approved, read [references/audit.md](references/audit.md).
Follow its exact host procedure once: Claude launches `empirica:empirica-auditor`, Pi calls the
packaged auditor, and Codex ends the turn without launching an ordinary child. The host owns dossier
binding, correlation, identity observation, and verdict admission. Audit may block but cannot
manufacture deterministic machine evidence.

If the host lacks bound child observation or private verdict ingress, true
convergence is unsupported. Do not substitute an ordinary model response.

## 6. Request the terminal decision

Call the host's guarded convergence operation exactly once after the graph,
evidence, and scope are current. On Claude and Pi this is `report_convergence`
after the bound audit. On Codex the trusted Stop hook rejects the unsupported audit attempt and
continues to block completion.

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

