---
number: 68
title: Record host boundaries as data and end the Claude Stop loop by control mode
status: accepted
date: 2026-10-08
tags:
- empirica
- hosts
- claude
- stop-hook
- control-mode
- conformance
links:
- target: 67
  kind: Amends
- target: 19
  kind: Relates to
- target: 51
  kind: Relates to
- target: 63
  kind: Relates to
- target: 69
  kind: Amended by
---

# Record host boundaries as data and end the Claude Stop loop by control mode

## Context and Problem Statement

Host-lifecycle cases were found one live test at a time: a surface not yet observed, a host failure, a turn end that waits for a human. Each was fixed for the feature at hand, and no list of the host boundaries and their closed outcomes existed. One fix regressed another need.

* F5/ADR-67 (A2-1/A2-2): the Claude Stop never settles on an unshown evidence batch, because the human is asked only inside `report_convergence`; it blocks with a directive.
* Qualification Q2 (Claude 2.1.294): at an operator checkpoint the agent stopped to wait. Empirica's Stop blocked nine times ("Run a deterministic spike for this claim") until Claude overrode it ("A hook blocked the turn from ending 9 consecutive times … check stop_hook_active"). The Stop treated every turn end as an attempt to finish and could not tell a hand-off from a premature "done". This is the platform override that ADR-0019 (G2) says termination must not rely on.

The Stop hook read neither `stop_hook_active` nor the run's control mode. Claude documents `stop_hook_active` for exactly this: check it "to avoid blocking on a condition that will never resolve", and caps consecutive continuations at eight (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`).

## Decision Drivers

* ADR-0019 G2: termination is Empirica's own, not the platform's forced override.
* ADR-0051 (option 2 rejected): a Stop block that creates a host-level reasoning loop is wrong; the settled Stop returns the typed state with the run still active and non-converged.
* ADR-0063: a human wait settles nonterminally with an explicit not-converged notice and no automatic re-prompt. Deliberative returns every change to the human; interactive auto never presents again after its first approval; delegated auto has no human. `_human_approval_wait` already encodes this per mode.
* The `fb1106b` interim (ADR-0064 interim, adopted for 4.0): "Hosts must not spam the author with a recovery that the core knows is inadmissible"; an unattended run must end honestly and promptly. `intent=stop` is always admissible.
* ADR-0067 A2-1/A2-2: the *first* Stop with an unshown batch must not settle; nobody would ask.
* No Stop path terminalizes a run (ADR-0051: a settlement is not a terminal fact).
* The host boundaries should be one list that tests and qualification rows are checked against, not prose that drifts.

## Considered Options

* **Keep the Stop literal ("never pauses") and rely on Claude's cap.** Rejected: Q2 shows nine blocks and an override, and the cap resets each tool call.
* **Honour `stop_hook_active` the same way in every mode.** Rejected: an unattended run has no human to pause for; handing it back leaves it stalled with nobody reading.
* **Terminalize from the hook.** Rejected (ADR-0051): a settlement cannot mark a run terminal.
* **A counter or note in run state.** Rejected: the host's own flag is the memory; a counter is a second owner of a fact (and A2-1 removed the surface note for that reason).
* **End the loop by control mode on a continuation, and record the boundaries as data.** Chosen.

## Decision Outcome

### The Claude Stop by control mode

`completion.stop_result(response, *, stop_hook_active)` is given Claude's `stop_hook_active` (the literal `true` only). With it false or absent, its exit codes and settlements are unchanged in every mode (the reason text is `_reason_text`): `Allow`, `Inert`, `Fault` (closed), the async-audit settlement, `budget_exhausted`, `human_wait`, the evidence directive and the real-reason block.

With it true, a non-converged `Block` of an **active** run that no existing settlement owns (not `human_wait`, `budget_exhausted` or the single pending async audit) ends by the control mode read **only from the run view the core returned** (`governance.control_mode`):

| Mode | Continuation |
|---|---|
| deliberative | exit 0 with `settlement_notices.paused_for_human` (full wording in the contract): "Empirica is paused for you, not converged: *reason* Nothing was approved or confirmed by this pause, and nobody was asked." A hand-off to the human, who is in the loop by definition of the mode. |
| every other `auto` run: interactive before or after its first approval, and delegated | exit 2 with `settlement_notices.unattended_continue`: "Empirica is not converged: *reason* Auto mode does not hand the turn back to you: act on it, or end honestly with report_convergence intent=stop." Never a human pause. An approvable first approval is still settled earlier by `human_wait`, which comes first, so that case is unchanged; a first approval that cannot be asked here (a spent interaction, an error, no MCP ingress) is an ordinary Block and takes this row (G-R1''). |
| `auto` whose sole blocker is the evidence directive case | exit 2 with `settlement_notices.evidence_report` as on the first Stop: the act-on-it action is the report that asks the human. |

The reason is the core's own text for every blocker, rendered by one function. The notice never says a human *will be* asked. No state is kept. Pi has no turn-end gate and Codex is not declared; neither changes. Claude's eight-block cap is the host outcome `host-capped` (`report-limitation`) and is never the design.

The bound "a continuation ends the hook loop (deliberative: in two Stops; auto: one block per chain)" holds **only while Claude keeps `stop_hook_active` true across tool calls within a chain**. That is host behaviour nobody has observed (ledger row `stop_flag_reset`). If the flag resets after a tool call, every post-tool Stop is a first Stop and blocks again, and the outcome is `host-capped`; the rule does not bound that case and the core cannot (an unchanged derivation charges no pass).

### Host boundaries as data

`contracts/empirica/v2/host-boundaries.json` (schema `host-boundaries.schema.json`) is the authoritative list of host boundaries, their expectations (with a `control_mode` axis where the outcome depends on it) and the `EHB-*` scenarios. `scripts/validate_contracts.py` (in `make contract-check`) requires: unique ids; every scenario names `covered_by` tests that exist, or a `live_only` row that exists in the native-qualification ledger (`.agents/skills/native-qualification/references/scenarios.md`); and the design document `doc/design/empirica-host-boundary-registry.md` lists exactly the same scenario ids. The document is the explanation; the JSON is authoritative.

### Consequences

* Good, because an operator checkpoint ends in two Stops with an honest "paused, not converged", where Q2 took nine blocks and an override, provided the flag persists (see the condition above).
* Good, because every `auto` run is told the way out (`intent=stop`) instead of being asked to repeat a recovery that cannot work, and none is ever handed a human pause.
* Good, because earlier invariants hold: the first Stop on any state is unchanged, so F5, the F7 evidence directive, F8's two-call bound, ADR-0051 and the ADR-0063 settlements are unchanged, and the assertions of the A1 test `ClaudeStopNeverPausesForEvidence` are unchanged and green.
* Good, because a missing test or a missing qualification row for a host boundary fails `make contract-check`.
* Bad, because the Stop now has a second input (the flag) and two new contract notices to maintain.
* Neutral: whether `stop_hook_active` stays true across tool calls within one continuation chain, how Claude renders an exit-0 `systemMessage`, and the host's own cap are host behaviour only an operator can show; they are `live_only` ledger rows. If the flag resets after a tool call, the bound does not hold and the outcome is `host-capped`.

## Amendment (2026-10-08): the registry keeps data only and its tables are generated

[ADR-0069](0069-admit-hosts-and-audit-runtimes-by-range-and-observed-capability.md) narrows "Host boundaries as data". The registry JSON keeps ids, boundary, outcomes per host and per control mode, scenario rows, `covered_by`, `live_only`, `ledger_rows` and sources; the prose fields (trigger, obligation, failure policy, recovery, visibility, forbidden substitutions, boundary definitions, vocabularies) live only in the design document, whose registry-owned parts are generated by `make registry-doc` and checked by `make contract-check`. The Stop decision above is unchanged.

The first Stop's exit codes and settlements are unchanged in every mode, but its reason text is the one sentence of `_reason_text` (several reasons are named in one sentence), so "byte-identical" above means the codes and settlements. `stop_result` is `stop_result(response, *, stop_hook_active)`. The evidence directive reads "…the human is asked inside that call, if this host can show a dialog" (`settlement_notices.evidence_report`).

The bound quoted as "auto: one block per chain" above is wrong for `auto`: an auto continuation blocks with `unattended_continue` until the run settles (HX-STOP-01), so only the deliberative bound (two Stops) is a bound of this rule.
