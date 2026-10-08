---
number: 68
title: Record host boundaries as data and end every turn by control mode, on every host
status: accepted
date: 2026-10-08
tags:
- empirica
- hosts
- claude
- pi
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

# Record host boundaries as data and end every turn by control mode, on every host

## Context and Problem Statement

Host-lifecycle cases were found one live test at a time: a surface not yet observed, a host failure, a turn end that waits for a human. Each was fixed for the feature at hand, and no list of the host boundaries and their closed outcomes existed. One fix regressed another need.

* F5/ADR-67 (A2-1/A2-2), until 2026-10-08: the Claude Stop never settled on an unshown evidence batch, because the human is asked only inside `report_convergence`; it blocked with a directive in every mode (a deliberative turn end now pauses instead).
* Qualification Q2 (Claude 2.1.294): at an operator checkpoint the agent stopped to wait. Empirica's Stop blocked nine times ("Run a deterministic spike for this claim") until Claude overrode it ("A hook blocked the turn from ending 9 consecutive times … check stop_hook_active"). The Stop treated every turn end as an attempt to finish and could not tell a hand-off from a premature "done". This is the platform override that ADR-0019 (G2) says termination must not rely on.
* Qualification Q3 (Claude 2.1.294, deliberative): the loop was ended on the second Stop, but the **first** Stop of each state still blocked with the next obligation, and the agent acted on it instead of pausing at the operator's checkpoint (finding F-Q3-1). In deliberative mode every change returns to the human (ADR-0063), so a turn end that the agent chose is a hand-off, not an attempt to finish.
* The two supported hosts end a turn through different surfaces, and only Claude had any mode logic: Pi registered no turn-end handler at all. (Codex is not a supported host and is not part of this table.)

Claude documents `stop_hook_active` for the loop case: check it "to avoid blocking on a condition that will never resolve", and it caps consecutive continuations at eight (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). Experiment `2026-10-08-exp-first-stop` showed that a **first** Stop that exits 0 with `systemMessage` ends the turn and shows the notice, exactly as a continuation does.

## Decision Drivers

* ADR-0019 G2: termination is Empirica's own, not the platform's forced override.
* ADR-0051 (option 2 rejected): a Stop block that creates a host-level reasoning loop is wrong; the settled Stop returns the typed state with the run still active and non-converged.
* ADR-0063: a human wait settles nonterminally with an explicit not-converged notice and no automatic re-prompt. Deliberative returns every change to the human; interactive auto never presents again after its first approval; delegated auto has no human.
* The `fb1106b` interim (ADR-0064 interim, adopted for 4.0): "Hosts must not spam the author with a recovery that the core knows is inadmissible"; an unattended run must end honestly and promptly. `intent=stop` is always admissible.
* ADR-0067 A2-1/A2-2: in interactive `auto`, a turn end must not settle on an unshown evidence batch, because nobody else would ask. (A deliberative run's human is present, so its turn end pauses and hands them the step.)
* No turn-end path terminalizes a run (ADR-0051: a settlement is not a terminal fact).
* One decision, not one per host: the same run state in the same control mode must end the same way wherever it runs.
* The host boundaries should be one list that tests and qualification rows are checked against, not prose that drifts.

## Considered Options

* **Keep the Claude Stop literal ("never pauses") and rely on Claude's cap.** Rejected: Q2 shows nine blocks and an override, and the cap resets each tool call.
* **Pause only on a continuation (`stop_hook_active`).** Chosen in the first version of this ADR and rejected by Q3: the first Stop already carries the agent past the checkpoint, and the flag exists on Claude only.
* **Honour the same rule in every mode.** Rejected: an unattended run has no human to pause for; handing it back leaves it stalled with nobody reading.
* **Terminalize from the hook.** Rejected (ADR-0051): a settlement cannot mark a run terminal.
* **A counter or note in run state.** Rejected: the host's own signal is the memory; a counter is a second owner of a fact (and A2-1 removed the surface note for that reason). Pi, which has no such signal, keeps a bound in the adapter (below) because the host gives it nothing to read.
* **One host-neutral turn-end table, mapped by each host, and the boundaries recorded as data.** Chosen.

## Decision Outcome

### The turn-end table

A turn end of an active run has an outcome fixed by the run's **control mode** and **run state**, the same on every host. The control mode is read only from the run view the core returned (`governance.control_mode`). The table is data (`turn_end` in `contracts/empirica/v2/host-boundaries.json`); the design document renders it.

| Run state | deliberative | interactive auto (before and after the first approval) | delegated auto |
|---|---|---|---|
| converged | allow | allow | allow |
| exactly one async audit pending | settle (the view) | settle | settle |
| the sole blocker is a human wait (`human_wait`) or an exhausted budget (`budget_exhausted`) | settle with that notice | settle with that notice | settle with that notice |
| the sole blocker is an evidence confirmation a human can be asked about | **pause**: `paused_for_evidence` | **continue**: the `evidence_report` directive | continue (the route is `unavailable`, so it is an ordinary state) |
| any other active state | **pause**: `paused_for_human` with the real next step | **continue**: the next obligation | **continue**: the next obligation |

A **pause** ends the turn with a contract notice and no convergence claim, whether this is the first turn end or a later one: nobody was asked, nothing was approved or confirmed, the run stays active. A **continue** hands the agent the core's own reason (for evidence in interactive auto, the directive: the report is what asks the human) and, on a later turn end of the same chain, `unattended_continue`: "act on it, or end honestly with `report_convergence intent=stop`". An approvable first approval is settled earlier by `human_wait`, which comes first; a first approval that cannot be asked here (a spent interaction, an error, no MCP ingress) is an ordinary active state. `Allow`, `Inert` and `Fault` are unchanged. No run state is kept: the control mode and the run view are the only inputs. (Pi's bound below is in-process adapter memory, not run state.)

### Each host maps the table to its own surface

* **Claude (`Stop`).** `stop_result(response, *, stop_hook_active)`. Pause = exit 0 with `systemMessage`. Continue = exit 2 with the reason on stderr, and `unattended_continue` when `stop_hook_active` is true. The loop bound is the host's: `stop_hook_active` plus Claude's cap of eight consecutive continuations, whose outcome is `host-capped` (`report-limitation`) and is never the design. `stop_hook_active` has no role in deliberative mode, because a pause ends the turn. Only the literal `true` counts.
* **Pi (1.0.0 and later).** The adapter acts at `agent_before_settle` only, never at `turn_end`, so a settle carries one notice. Pause = append a `custom_message` entry (`display: true`) holding the notice, with no `continue`. Continue = `continue: true` with the next obligation as the appended message. Pi has **no** loop bound of its own, so the adapter keeps one: it requests a continuation only when the run's observation basis changed since its previous continuation; otherwise it ends with the `unattended_continue` notice and records `no-progress`. The remembered continuation lives in adapter memory only, never in the run, so a restart can allow at most one extra continuation. Pi 1.0.0 is the floor (ADR-0069); there is no fallback for a Pi without `agent_before_settle`.

### The notices

The contract owns every notice (`settlement_notices`); an adapter renders only those. A pause notice names the next step the human can relay to the agent. `paused_for_human` carries the core's real reason in one `{reason}` placeholder, rendered by one function (`render_notice`); `paused_for_evidence` says to tell the agent to call `report_convergence`, and that the human is asked in that call, if this host can show a dialog. No notice says a human *will be* asked. The reason is the core's own text for every blocker, several reasons joined in one sentence.

### Host boundaries as data

`contracts/empirica/v2/host-boundaries.json` (schema `host-boundaries.schema.json`) is the authoritative list of host boundaries, the turn-end table, the expectations (with a `control_mode` axis where the outcome depends on it) and the `EHB-*` scenarios. It keeps only what is checked: ids, boundary, outcomes per host and per control mode, scenario rows, `covered_by`, `live_only`, `ledger_rows` and sources. The prose (trigger, obligation, failure policy, recovery, visibility, forbidden substitutions, vocabularies) lives in `doc/design/empirica-host-boundary-registry.md`, whose registry-owned regions (each expectation's outcomes and scenarios, the scenario matrix, the turn-end table) are generated by `make registry-doc`. `scripts/validate_contracts.py` (in `make contract-check`) requires: unique ids; every scenario names `covered_by` tests that exist, or a `live_only` row that exists in the native-qualification ledger (`.agents/skills/native-qualification/references/scenarios.md`); the design document's generated regions equal what `make registry-doc` writes; and the turn-end table is closed: every cell names an outcome of its vocabulary and notices the contract defines, and every row is proved for every host by scenarios that together cover every control mode. Each host also names the scenario whose test enumerates every cell of the table and reads the expected outcome from the registry.

### Consequences

* Good, because an operator checkpoint in deliberative mode ends on the first turn end with an honest "paused, not converged" and the real next step, where Q2 took nine blocks and Q3 carried the agent past the checkpoint.
* Good, because the same state ends the same way on Claude and Pi, and a table-driven test per host reads the expected outcome from the registry, so the hosts cannot drift apart.
* Good, because every auto run is told the way out (`intent=stop`) and none is ever handed a human pause; a Pi auto run cannot loop without progress.
* Good, because earlier invariants hold for the cells they own: F5, the F7 evidence directive, F8's two-call bound, ADR-0051 and the ADR-0063 settlements.
* Good, because a missing test or a missing qualification row for a host boundary fails `make contract-check`.
* Bad, because a deliberative author no longer gets an automatic nudge: the human relays "go" after each pause. That is the point of the mode, and the notice names the step to relay.
* Bad, because the turn-end handler is now a second input to three adapters and the contract carries one more notice (`paused_for_evidence`).
* Neutral: whether Claude renders an exit-0 `systemMessage` the same on a first Stop in a real session, whether `stop_hook_active` stays true across tool calls within an auto chain, Claude's cap, and Pi's rendering of the appended entry are host behaviour only an operator can show; they are `live_only` ledger rows (`stop_first_pause`, `stop_flag_reset`, `stop_host_cap`, `pi_settle_notice`, `pi_auto_continue_bound`). If Claude resets the flag after a tool call, an auto run's post-tool Stop blocks again and the outcome is `host-capped`.
