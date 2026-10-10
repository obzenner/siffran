---
number: 69
title: Admit hosts and audit runtimes by range and observed capability, not by exact version lists
status: accepted
date: 2026-10-08
tags:
- empirica
- hosts
- compatibility
- pi
- registry
links:
- target: 52
  kind: Amends
- target: 67
  kind: Amends
- target: 68
  kind: Amends
---

# Admit hosts and audit runtimes by range and observed capability, not by exact version lists

## Context and Problem Statement

Every Pi, Claude or pi-subagents release forced an edit of several files and a new contract digest even when nothing it does had changed: the profile ids carried the host version (`claude-code@2.1.294`, `pi@1.1.0+...`), the Pi profile listed the exact pi-subagents versions it would accept (`reviewed_versions`, anything else refused), and the host-boundary registry carried the same prose twice (in the JSON and in the design document) behind a set of mandatory fields. The cost was real and it bought little: the facts that make a runtime safe to use are observable at run time (its own parameter schema, its launch preflight), and the facts that need a human review are recorded as evidence, not as a gate.

## Considered Options

* Keep exact version lists and version-bearing ids (every release is an edit and a repin).
* Admit anything the host reports (no review signal at all).
* Admit a half-open range, review by evidence, and let a version without evidence run only on its own observed capability, with every unreviewed action refused (chosen).

## Decision Outcome

Four decisions, G5-R1 to G5-R4, and the host range (G9-R1). G5-R3 is recorded as not built (below).

### G5-R1 Profile ids never carry a version

A profile id is the host id (`claude-code`, `codex-cli`) or, for a host with an external audit runtime, `<host_id>+<policy_id>` (`pi+pi-subagents-foreground-audit-v1`). The qualification version stays in the profile's `version` field and receipts keep recording the exact observed host version. `application.protocol.host_profile_id(host_id)` resolves the id of a host (exactly one profile per host); no code splits an id on `@`. Stored runs, receipts and fixtures changed in place (ADR-0067: 5.0.0 is unreleased and carries no compatibility).

### G5-R2 pi-subagents is admitted in a range, by observed capability

`subagents_compatibility` is `{package, policy_id, minimum, maximum_exclusive}` (`pi-subagents` `[0.50.0, 1.0.0)`); `reviewed_versions` and `unreviewed_action` are removed. At `session_start` the adapter resolves the single owner of the `subagent` tool and:

* refuses a version outside the range (`version-unsupported`, `host.subagents_version_unsupported`) before loading any of its code;
* *reviewed*: a version with a checked-in `compat/pi-subagents-<v>.json` uses that inventory (launch forms, actions, contract version), exactly as before;
* *not reviewed*: any other version in the range is admitted only if the owner's own `parameters.properties` declare every field the audit launch sends (`AUDIT_LAUNCH_PARAMETERS`: `agent task model agentScope async context acceptance timeoutMs toolBudget`) and its live launch preflight passes every existing check (packaged canonical auditor, configured model, `context: "fresh"`, contract version in `[2, 3]`, package version equal to the owner's). Its action list is empty here, so every `subagent` action is refused while a run is active (the G6 amendment below widens this to the names some reviewed version has; the classifier still decides). Launches are still classified by the launch forms its schema declares.

`invocation.host_runtime.subagents` records `reviewed: boolean`. It is the only member of `host_runtime` the run view reads (`StartRun` checks the rest): `reviewed: false` adds `host.unreviewed_runtime {package, version}` to the run view and the author view shows one line under the header, `host: pi-subagents <v> (not reviewed; preflight passed)` on every run view while the runtime stays unreviewed. The live receipt records `reviewed` too. `make empirica-subagents-matrix` still proves every version that has a record; a record outside the range fails `make contract-check`. Reviewing a release is adding its record, fixture and classifier cases, not editing the contract.

**Trade-off.** A release inside the range that changes what its preflight does not report, or what an action means, is admitted without review. This is bounded: the audit still needs a distinct independent auditor with a verdict bound to the canonical agent, the run view says the runtime is not reviewed, and no action runs beyond the reviewed read-only ones (G6 amendment). The range's upper end is the reviewing mechanism: a major release is refused until a human widens it.

### G5-R3 Web evidence confirmed by the auditor's observed WebFetch: not built

The decision was to let a `web` record be confirmed by the audit when the auditor's own transcript shows a successful `WebFetch` for that exact `source_ref`. Its premise was checked first and did not hold well enough to build: the transcript path and WebFetch's availability to a background subagent are documented, but no captured child transcript exists with a `WebFetch` result, and the tools reference says a cross-host redirect is returned as an ordinary text result that names the target instead of following it, so a successful result is not proof the named URL was retrieved. ADR-0067's rule stays: a web record is confirmed by the audit only when the start-time `audit_retrieval` contains `web`, which no host grants, so web evidence goes to a human or ends residual. The evidence and the proposed capture are in PROGRESS ("G5 item 3"). Nothing in this ADR depends on it.

### G9-R1 Pi is supported from 1.0.0, and nothing older is kept

The Pi interval is `>=1.0.0,<2.0.0` and the qualified version stays `1.1.0`. Empirica acts at `agent_before_settle` (ADR-0068), whose `BoundaryResult` is not in every older Pi, and a single floor lets the adapter use it without a fallback. 5.0.0 is unreleased and carries no compatibility (ADR-0067), so every artifact that served a Pi below 1.0.0 goes: the profile's `compatibility.minimum`, the versions of `make empirica-pi-api-typecheck` (it keeps 1.0.0 and 1.1.0), shims and version branches in the Pi adapters, and the fixtures and prose that named 0.84.1, 0.86 or 0.87. A Pi below 1.0.0 is outside the interval; there is no run-start refusal for it (ADR-0052: compatibility is enforced at receipt capture and verification), but no code or test supports it. Methodologist declares the same Pi interval and follows the same rule. pi-subagents is a different package with its own range and is not affected. ADR-0052's two 2026-10-08 amendments carry the same floor.

### G10-R1 Empirica has no Codex host in 5.0.0

The `codex-cli` profile was never completed: Codex cannot observe the resolved auditor model, so convergence could not be supported, and the package was only ever an adapter-development aid. Empirica is therefore withheld from the Codex marketplace (`.agents/plugins/marketplace.json` lists Methodologist only), `scripts/validate_codex_adapter.py` fails if the entry returns, and the user-facing documents say Empirica does not run on Codex. The Codex package, adapter, hooks file, profile and their tests stay in the tree, dormant, until a later round removes them. Methodologist's Codex package is unchanged.

### G5-R4 The registry is data, the design document is prose, and the tables are generated

`contracts/empirica/v2/host-boundaries.json` keeps only what is checked: boundary ids and names, expectation ids with their boundary, title, control-mode flag, `outcomes` (per host) and `outcomes_by_mode`, scenario ids and `sources`; and for each scenario its modes, setup, action, expected result, `covered_by`, `live_only` and `ledger_rows`. The prose that duplicated the design document (trigger, obligation, failure policy, recovery, visibility, forbidden substitutions, boundary definitions and mechanisms, the closed vocabularies) lives only in `doc/design/empirica-host-boundary-registry.md`. The parts of the document that come from the registry (each expectation's outcomes, scenario list and sources, and the acceptance matrix) sit between `BEGIN GENERATED` / `END GENERATED` markers and are written by `make registry-doc` (`scripts/registry_doc.py`); `make contract-check` fails if a region differs from what the generator writes. The validator keeps unique ids, resolving references, existing `covered_by` tests, existing ledger rows, the control-mode axis on the Stop, governance, report and surface-observation boundaries, and a negative for each of those rules. The mandatory fields and closed vocabularies of the earlier draft are not enforced.

## Consequences

* Good, because a new host or pi-subagents release inside its range needs no contract edit, no digest repin and no id change; only a review adds evidence.
* Good, because the registry and its document cannot drift, and the prose exists once.
* Bad, because an unreviewed pi-subagents release runs with only the reviewed read-only `subagent` actions allowed during a run; the run view says so on every view.
* Bad, because the range's upper end is a judgment (`1.0.0`) that a human must move.
* Neutral: Claude Code and Pi host ranges were already ranges (ADR-0052); the Pi range moved to 1.0.0 and later (G9-R1).

## Confirmation

`make contract-check` (host-profiles schema and digest, range rule, record-in-range rule, registry references, generated regions and their negatives), `plugins/empirica/adapters/pi/test/{host-profile,runtime-owner,host-runtime,gate}.test.ts` (range, observed admission, refusal by missing field, `reviewed:false` recorded and every action refused), `plugins/empirica/tests/test_host_runtime.py`, `scripts/tests/test_empirica_live_receipts.py`, and the author-view golden `host-unreviewed-runtime`.

## Amendment (2026-10-08, round G6): receipt floor, read-only auditor, unreviewed actions, trade-offs

### G6-R1 A receipt must be from the qualified version or later

`scripts/empirica_live_receipts.py:require_compatible_version` requires a receipt's host version to be inside the profile's interval **and** at or above the profile's `version` (the qualified version). The hard-coded `_WIDENED_END_RELEASE = {"pi": "1.1.0"}` and its function are removed: the profile's `version` now carries that floor, so raising it in `host-profiles.json` asks for exactly one new receipt, from that release or a later one in the interval. This is the check G5-R1 removed implicitly when the version left the profile id. A qualified-version bump still costs three files (the contract, its vendored copy, the pinned digest in `scripts/validate_contracts.py`); the repin is the human review of the edit. Statements above and in ADR-0052 that name the Pi 1.1.0 floor as a constant are superseded by this one.

### G6-R2 The auditor holds no writing tool at run time, for every version

`audit_launch_policy.audit_child_tools` (the field is the owner: user-facing text names it and the refusal lists the tools from it; the current values are in `host-profiles.json`) is the only set of tools the auditor child may be given. The preflight seam refuses `tools-not-read-only` when the preflight's `contract.tools.effectiveAllowlist` holds a tool outside it, at `/empirica` start and at audit launch (the one resolver does both), for reviewed and observed versions alike; a reviewed version no longer relies on the review for this. `tool_budget.block` is unchanged. `contact_supervisor` is the one messaging tool admitted: it writes no file and runs no code, and, in the foreground audit, the author is blocked inside the audit tool call and cannot answer it (pi-subagents `docs/workflows.md` "Supervisor coordination"; `docs/extension-api.md`: an active bridge adds `contact_supervisor` to the declared tools). The `subagent` tool schema has no per-launch `intercomBridge` override in 0.76.1 (only the delegation and preflight API), so the bridge cannot be switched off for the audit. No author-side gate is added. A child that waits on the supervisor fails closed either way (a timeout is an error; a detached child is non-terminal, so no verdict is admitted); the qualification (`auditor_contact_supervisor`) records which one happens. `tool_budget.block` lists the budgeted file tools only, so `contact_supervisor` is in `audit_child_tools` and not in `block`; `make contract-check` closes the vocabulary of `audit_child_tools`. The refusal surfaces with the contract message of `host.subagents_tools_not_read_only` (`residual.accept`; recovery.md). The definition digest is not compared at run time (the agent file path is already checked to be the packaged auditor); `make empirica-subagents-matrix` checks it for reviewed versions.

### G6-R3 An unreviewed version runs the reviewed read-only actions

The refusal of every `subagent` action on an unreviewed version protected nothing beyond what the action classifier already protects. Its observed inventory now lists the union of the actions of every checked-in inventory (`reviewedActionNames`); the classifier decides as before (only the reviewed read-only management actions `list`, `status`, `models`, `children.list`, `doctor`, ... pass, and `validate` only where a version documents it as offline, which an observed version does not). A name no reviewed version has is refused with `host.subagents_launch_unsupported`.

### Trade-offs stated

* The audit timeout and tool budget are declared on the launch request but the preflight does not echo them, so for an unreviewed version they are enforced on the request only.
* An in-range unreviewed pi-subagents is the user's own installed extension, and its preflight module is imported at session start before anyone has reviewed that version. The earlier claim "no package code runs before review" is no longer made; only an out-of-range version is refused before its code is loaded.
* The owner's parameter schema is read at session start and not compared again; a same-version package whose schema changes keeps its start-time admission.
* The `reviewed` flag in `host_runtime` is the adapter's claim; the run view reads it and the core does not verify it.
* Pi print and JSON mode have no UI, so `ctx.ui.notify` shows nothing there. Every `/empirica` notice then goes to stderr and a refusal asks for exit code 1 (`process.exitCode`, which Pi keeps when its own result is 0), and the command does not return until the run it started is idle: Pi ends a print session when the command's promise settles, and `pi.sendUserMessage` does not wait for the turn it queues, so without the wait the session was disposed under the first turn (a stale ctx and `turn_end could not resolve the persisted assistant entry ID` per turn) (G14).
