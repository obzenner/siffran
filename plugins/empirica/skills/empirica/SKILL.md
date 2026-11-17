---
name: empirica
description: "Empirical-convergence workflow for non-trivial work whose plan is uncertain: route first, represent unknowns as claims, require cited research before spikes, and request an independently audited convergence decision. Use for architectural uncertainty, competing approaches, and risky assumptions. Invoke as /empirica <goal>."
allowed-tools: Read Glob Grep Bash Edit Write Agent TaskCreate TaskUpdate WebFetch
compatibility: Claude Code >=2.1.278,<2.2.0; Pi >=1.0.0,<2.0.0 with the external pi-subagents; python3 for hook-backed hosts.
argument-hint: "[--auto] <goal>"
---

# Empirica — empirical convergence

Never edit operational state under `~/.empirica-plugin/` or knowledge under `refs/empirica/*` directly, and never create Empirica state under `.claude/`, `.codex/`, or `.pi/`.

## 1. Non-negotiables

Treat training-weight knowledge as hypothesis: discharge load-bearing claims against evidence or surface them as unverified.

**Experiments go through Empirica only.** During an active run, every experimental workload is an `empirica_observe` `spike_request`; never run one through the ambient host shell, `docker run`, or `docker exec`, including after an unavailable or refused result. Neither an image-acquisition approval nor read-only inspection substitutes for one.

**Do not read any file, including a skill reference, before the `investigate` witness is admitted
(route → graph → approved configuration).**

A run exists only if `/empirica` injected a handle; without one, nothing was started.

Discard refuted claims or replace them with narrower ones; never preserve a preferred design by grading contrary evidence away. New claims attach to the root and pass through both folds (section 8).

Evidence leaves, attribution, child events, and audit verdicts are trusted host ingress; submit only the author actions the host exposes. Do delegated work through Empirica's children: work by an external agent CLI is neither budgeted nor attributed to a producer.

Stop before investigation and report the exact unsupported capability when a public tool or the auditor launch is absent, when the start refuses the host, or when the host is unknown; never borrow another host's capabilities, imitate a missing operation in prose, or write runtime artifacts by hand. [references/host-capabilities.md](references/host-capabilities.md) helps diagnose a capability failure.

## 2. The loop

At invocation, begin with section 6; the loop, audit and terminal steps in sections 2–5 apply once investigation is admitted.

The three public tools return a plain-text author view whose `Reasons`, `Open obligations` and `Next` calls are the worklist. After each evidence batch, reread it, drop refuted branches, add only claims the evidence forces, and take the next call it names. Do not loop on unchanged state or infer convergence from confidence-like language; [references/budget-freeze.md](references/budget-freeze.md) covers budgets, stalls and scope closure.

## 3. Obtain independent audit

When every in-scope gating claim is approved, read [references/audit.md](references/audit.md). Approval is not convergence: it means the evidence passed the service's rules, not that it convinces. Check `GetArgument`, the argument the auditor will receive: each claim must follow from its cited sources alone. The view prints the auditor launch. Never substitute an ordinary model response for the audit.

## 4. Request the terminal decision

Call `report_convergence` when the graph, evidence, and scope are current; after the bound audit its `Allow` is the terminal decision.

- `Allow(converged=true)` permits a convergence claim.
- `Allow(converged=false)` permits an honest non-converged report; `Block`, `Inert` with an active handle, `Fault`, malformed output, or transport failure never permits convergence. A terminal run is reported, never re-judged.

To accept residual or deferred scope, or an exhausted pass budget, call it once with `intent: "stop"`; only the resulting `Allow(converged=false)` authorizes an honest stopped report. Follow the view's `next:` rather than re-calling the gate; [references/recovery.md](references/recovery.md) covers other reason codes.

## 5. Finalize and hand off

Read [references/handoff.md](references/handoff.md) before producing the final artifact, and report one of:

**converged** — a passing bound audit covers the current argument; **stopped** as residual (human or data obligations remain), frozen (scope closed, later claims deferred) or budget (the process ended); **unsupported** — the host lacked a capability; **faulted** — state, artifacts, transport, or protocol failed closed.

Commit only the goal's product artifacts, tests, and accepted decisions.

## 6. Invocation, and route before investigating

The user invocation is `$ARGUMENTS`.

1. Restate the goal without the flags; list each dependency as **known** or **unknown**.
2. Record `{"kind":"route","reason":"<why>"}`, then propose the smallest claim graph from supplied context only, by the rules in section 7.
3. Read `empirica_read(operation="GetRun")`, then size the run from the gating claims, how many need experiments, and the expected audit rounds (normally 2: an initial audit and one retry). Propose all three ceilings with `configure_run` and a rationale explaining the sizing. Investigation children draw on `max_spawns` and the auditor on `max_audit_spawns`; neither borrows from the other. A pass is charged only when the derivation changes.
4. Wait until the view says `approved`, taking its `next:` after any dismissal. Then record `{"kind":"investigate"}` before any native read, search, command, evidence submission, or child launch; approval supplies neither witness.

## 7. Refine the claim graph

Build the smallest closed graph whose gating root is the goal and whose gating claims cover every material unknown:

- One gating claim per material unknown or invariant; scope you want to defer goes in non-gating claims below the root.
- `needs-experiment` only when a deterministic spike plan can falsify the claim.
- `needs-decision` only for an irreducible human choice: the human answers it when you report, it needs no research, and with nobody to ask it stays open and the run can only stop as a residual.
- Every other claim is `ordinary`.
- Rewording a claim stales its evidence and audit coverage, so keep claims stable once evidence binds; state no verdict or confidence in a claim's text.

Once investigation is admitted, see [claim-graph](references/claim-graph.md) and [governance](references/governance.md).

## 8. Earn or discard every gating claim

Read [references/evidence.md](references/evidence.md) before adding evidence. Apply the folds in order (Fold 1, then Fold 2):

1. **Fold 1: research first, for every gating claim except a `needs-decision` claim.** Cite a concrete code, docs, runtime, or web source that supports or refutes the exact current claim text; generic precedent is not evidence, and a summary of a source is not a citation.
2. **Fold 2: spike second, only for `needs-experiment`, after supporting research.** Derive the falsifier from the claim: a plan whose verdict container would fail if the claim were false.
