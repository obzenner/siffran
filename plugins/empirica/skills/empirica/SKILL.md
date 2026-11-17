---
name: empirica
description: "Empirical-convergence workflow for non-trivial work whose plan is uncertain. Route before investigating, represent unknowns as claims, require cited research before deterministic spikes, discard refuted claims, and request an independently audited convergence decision. Use for design-and-implement work, architectural uncertainty, competing approaches, and risky assumptions. Host capabilities differ; run the capability preflight before promising convergence. Invoke as /empirica <goal>."
allowed-tools: Read Glob Grep Bash Edit Write Agent TaskCreate TaskUpdate WebFetch
compatibility: Designed for Claude Code >=2.1.278,<2.2.0 and Pi >=1.0.0,<2.0.0 with the external pi-subagents runtime (not bundled); requires python3 for hook-backed hosts. The exact observed versions are recorded in the run; live capabilities still gate execution.
argument-hint: "[--auto] <goal>"
---

# Empirica — empirical convergence

Empirica resolves material unknowns before production implementation. It is a
design-time assurance workflow, not a production security boundary. CI remains
the trust boundary for shipped code.

The host-neutral `empirica/v2` service owns policy and state. Host adapters only
translate host events and expose capabilities. Never edit operational state under
`~/.empirica-plugin/` or knowledge under `refs/empirica/*` directly. Never create Empirica state under `.claude/`, `.codex/`, or `.pi/`.

## 1. Non-negotiables

Treat training-weight knowledge as hypothesis: discharge load-bearing claims against evidence or surface them as unverified.

**Experiments go through Empirica only.** During an active run, every experimental workload is an
`empirica_observe` `spike_request`; never run one through the ambient host shell, `docker run`, or `docker exec`,
including after an unavailable or refused result. An operator's separate approval to acquire an image does not
authorize running a workload, and read-only inspection is neither a substitute for one nor an experimental result.
The binding rules are the public contract's `evidence/spike/isolation` and `evidence/spike/outcomes` clauses (the limits list is `evidence/spike/limits`).

**Do not read any file, including a skill reference, before the `investigate` witness is admitted
(route → graph → approved configuration).**

Do delegated work through Empirica's children: work by an external agent CLI is neither budgeted nor attributed to a producer.

Claim discipline; the two folds named here are defined in section 8 below.

Refuted claims are discarded or replaced with narrower claims. Never preserve a
preferred design by grading contrary evidence away. New claims must attach to the
root and pass through both folds when applicable.

Use only author actions exposed by the current host. Evidence leaves, attribution,
child events, and audit verdicts are trusted host ingress; the author must never
submit or fabricate them.

## 2. The loop: assess one fixed-point pass

At invocation, begin with section 6 (route, graph, configuration, investigate); sections 2–5 apply once investigation is admitted.

The three public tools return one deterministic plain-text author view on Claude and Pi.
Read its first-line status/governance summary, `Reasons`, `Open obligations`, and rendered `Next`
calls directly; do not parse it as JSON or look for hidden digests/governance internals. Pi retains
validated structured details for host UI only. The host binds and injects the private audit dossier;
`GetArgument` is text for authors recovering a graph, while `GetContract` is text for inspection.
`missing: <code> via claim:<id>` on an obligation means that descendant claim blocks it: discharge the
named claim, not the listed one.

After each evidence batch:

1. reread the public run view and obligations;
2. remove refuted branches and add only claims forced by the evidence;
3. identify the next unmet witness;
4. perform that action;
5. end the pass and let the service evaluate the new snapshot.

Do not loop on unchanged state. Do not infer convergence from confidence-like
language. When budgets, a stall, or scope closure matter, read
[references/budget-freeze.md](references/budget-freeze.md).

## 3. Obtain independent audit

Once confirmations are done (when evidence waits on a human, `report_convergence` presents it) and every in-scope gating claim is approved, read [references/audit.md](references/audit.md).
Approval is not convergence: it means the evidence passed the service's rules, not that it
convinces. Before requesting the audit, read `GetArgument`, the argument the auditor will receive,
and check that each claim follows from its cited sources alone.
Follow its exact host procedure once: Claude launches `empirica:empirica-auditor`, Pi calls the
packaged auditor. The host owns dossier
binding, correlation, identity observation, and verdict admission. Audit may block but cannot
manufacture deterministic machine evidence.

If the host lacks bound child observation or private verdict ingress, true
convergence is unsupported. Do not substitute an ordinary model response.

## 4. Request the terminal decision

Call the host's guarded convergence operation when the graph, evidence, and
scope are current. On Claude and Pi this is `report_convergence`: when evidence
waits on a human, `report_convergence` presents it; after the bound audit its
`Allow` is the terminal decision.

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
residual obligation or next action returned by the service. A sole `budget.exhausted` blocker on
an active run settles the turn with a host notice rather than blocking it: stop honestly with
`intent: "stop"` (or, in deliberative mode, propose a raise for human approval) instead of
retrying the gate.

For any other reason code, [references/recovery.md](references/recovery.md) gives the next actions and one action to take.

## 5. Finalize and hand off

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

## 6. Invocation, and route before investigating

The user invocation is `$ARGUMENTS`. Leading `--` values are invocation flags, not part
of the goal. The host adapter owns parsing. Surface unknown flags and read the
resolved goal only from the public run view when that view is available.

Do this before reading files, searching, browsing, or running commands.

1. Restate the goal without invocation flags.
2. List each dependency as:
   - **known** — already fixed by evidence you can cite now;
   - **unknown** — requires observation, experiment, or human judgment.
3. Announce the route and record the exact author action
   `{"kind":"route","reason":"<non-empty routing reason>"}` through the active
   author-action surface; `reason` is a top-level field.
4. Before investigation, propose the smallest claim graph from supplied context
   only; claims can name discovery uncertainty. Write it by the rules in section 7.
   Take exact action and graph shapes from the `empirica_observe`
   tool input schema, never from contract reads or guesses.
5. Read `empirica_read(operation="GetRun")`, then size the run from the supplied task and selected graph. Count the gating claims, identify how many need experiments, and allow for expected audit rounds (normally 2: an initial audit and one retry). Propose all three ceilings explicitly with `configure_run`; the author agent chooses these ceilings, never the adapter or core. Include a rationale explaining that sizing. The rationale is explanation only—not a command, authority claim, evidence, or approvable conclusion. This requests approval of run configuration only: budget ceilings and control mode. Preserve the human's edited values rather than resending stale fields, and note explicit `--auto` has one up-front human approval episode and then stays within approved ceilings. Delegated auto has no dialog and stays within the fixed 8/1/2 envelope. Investigation children draw on `max_spawns` and the auditor on `max_audit_spawns`; neither borrows
   from the other, so size each for its own work. A pass is charged only when the derivation changes.
   Timeout is dismissal, not rejection. Do not loop on refusal or exhaustion.
6. Wait until the text view's governance summary says `approved` for the exact displayed
   configuration; do not expect or parse a `run.governance.state` JSON field.
   Configuration amendments need a second review; human numeric edits are not corruption.
   After dismissal, timeout, or rejection, wait for the human; Claude can settle the turn nonterminally while
   investigation and convergence remain blocked. When they say how to go on, take the view's `next:`
   (`configure_run` again, with the same or human-edited ceilings); record `investigate`
   only once the view says `approved`. Then record the `investigate` witness `{"kind":"investigate"}` before any
   native read, search, command, evidence submission, or child launch. Approval does not supply
   either witness.
7. Investigate. Public reads/corrections and explicit honest stop remain available without approval.

If either witness cannot be recorded, the workflow is unsupported;
do not continue with an unrecorded substitute.

## 7. Refine the claim graph

Construct the smallest closed graph whose gating root is the goal and whose gating claims cover every material
unknown. Write the first graph before approval, from the supplied context, by these rules:

- One gating claim per material unknown or invariant; scope you want to defer goes in non-gating claims below the root.
- `needs-experiment` only when a deterministic spike plan can falsify the claim.
- `needs-decision` only for an irreducible human choice: the human answers it when you report, it needs no research,
  and where nobody can be asked it stays open and the run can only stop as a residual.
- Every other claim is `ordinary`.
- Rewording a claim stales its evidence and audit coverage, so keep claims stable once evidence binds; state no verdict or confidence in a claim's text.

Submit the graph through the active author-action surface. Once investigation is admitted,
[references/claim-graph.md](references/claim-graph.md) adds the detail for amending it.

Graph changes do not revoke configuration approval. They do change the argument binding and
invalidate prior audit coverage, so obtain a fresh bound audit after any graph change. Configuration
changes require a fresh host decision through `configure_run`
([references/governance.md](references/governance.md) has the host-owned recovery details). Freeze is not a substitute for either
configuration authority or current audit coverage.

After the graph is recorded, use the returned open obligations and rendered next actions as the
worklist. Counts and reason strings are telemetry, not authority.

## 8. Earn or discard every gating claim

Read [references/evidence.md](references/evidence.md) before adding evidence.
Apply the folds in order:

1. **Research first for every gating claim, except a `needs-decision` claim**. Record a concrete code, docs,
   runtime, or web source that supports or refutes the exact current claim text.
   `source_ref` and the verbatim `citation` must identify the file or runtime observation that
   bears on this claim; generic precedent is not evidence for a repository-specific claim.
   One source per record, quoted exactly: the service refuses a workspace citation that is not
   verbatim in the cited file and lines (`research.citation_unverified`); a URL or an absolute path
   is retrieved by the auditor where its host is observed to, otherwise confirmed by a human; a `runtime`
   observation must say why no spike can establish it (`spike_unavailable`) and is always confirmed by a
   human. What still needs a confirmation, who asks, when, and how often is owned by
   [references/evidence.md](references/evidence.md) ("What still needs confirming"). A summary of what a source says is not a citation.
2. **Spike second only for `needs-experiment`.** After supporting research, first read the RunView's
   immutable `spike_backend`. If it is `spikes_unavailable`, do not submit an unchanged request:
   continue non-spike research or refine the graph, or stop with an honest residual. If it is
   `qualified`, construct exactly `{"kind":"spike_request","claim_id":...,"plan":...}` with a
   closed `empirica.spike-plan-draft/2` containing exactly `schema`, `image`, `argv`, `inputs`,
   and `environment`. Name one already-local Docker image by familiar name/tag, repository digest,
   full image ID, or unambiguous ID prefix; Empirica never inventories, pulls, builds, or installs it.
   Supply one exact nonempty executable/argument vector, zero or more repository-relative files or
   directories, and at most 128 environment overrides. `HOME` and `TMPDIR` are framework-owned.
   Never put credentials or secrets in environment values: values persist in the immutable request,
   and content-addressed plan identity can confirm guessed low-entropy values even though public
   views expose names only. Never add authority, profile, resources, timeout, user, workdir, network,
   topology, service, alias, host path, Docker option, image acquisition, or derived capture facts.

   The service captures only the declared immutable files and runs the harness
   at most once within that application call; a result-CAS retry never reruns it, but a crash after
   execution or a separate call may execute the same sealed request again, so the probe must be safe
   under duplicate execution. `qualified` means bound, not currently healthy.

   Any tool works as argv (`cargo build`, `npm run build`, `pytest -k load`, `go test ./...`) inside
   one host-fixed container: uid/gid 65534, `/work` = `HOME`, read-only `/inputs`, 1 MiB per stream,
   and the host-selected network profile. The 120-second workload runs inside the 210-second
   operation; Pi's 300-second and Claude MCP's 360-second limits are outer transport margins. The
   one complete list of what you choose, what the host fixes, what is never available, and every
   refusal `reason` with its hint is public-contract clause `evidence/spike/limits` (`GetContract`
   section `evidence/spike`).

   Only the verdict container's inspected natural exit produces uppercase `PASS`/`FAIL`: zero is
   `PASS`, and nonzero is `FAIL` except natural 137, which is unavailable. Any other outcome is a
   spike Fault `unavailable` and admits **no spike result evidence**. The Fault carries one closed
   `reason` whenever the cause is attributable: act on its hint. A Fault without a reason is an
   internal host failure: retry once, then ask the operator. Never treat it as FAIL, use a host
   fallback, or claim evidence that was not admitted. Operator requalification can affect only a future run.
3. **Re-gate stale spikes.** If a bound file changes and the backend remains qualified, request the
   bounded spike again; otherwise follow the unavailable guidance above.
4. **Derive the result:**
   - supporting research satisfies Fold 1;
   - refuting research refutes the claim;
   - a current passing spike satisfies Fold 2;
   - absent or stale evidence leaves the claim open.

## 9. Preflight the host

Stop before investigation and report the exact unsupported capability when one of the three public tools or the auditor launch is absent, when the start refuses the host, or when the host is unknown; never borrow another host's capabilities, imitate a missing operation in prose, or write runtime artifacts by hand. After admission, [references/host-capabilities.md](references/host-capabilities.md) helps diagnose a capability failure.
