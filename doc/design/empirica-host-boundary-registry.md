# Empirica host-boundary registry

**Authority.** `contracts/empirica/v2/host-boundaries.json` is the authoritative registry (ADR-0068). This document explains it: it carries the evidence, the reasoning and the open questions. The registry owns each expectation's outcomes, scenario ids and sources and every scenario row; those parts of this document sit between `BEGIN GENERATED` / `END GENERATED` markers, are written by `make registry-doc`, and `make contract-check` fails if any of them differs from the registry, if a scenario names a test that does not exist, or if a live-only scenario names a qualification-ledger row that does not exist (ADR-0069). The prose (trigger, obligation, failure policy, recovery, visibility, forbidden substitutions) lives only here.

**Status:** maintained through round G9, phase A (2026-10-08); the authority is the JSON, this document explains it. Round G9 replaces the Claude-only mode-aware Stop by **one turn-end table** that every host maps to its own surface (`turn_end`, ADR-0068), adds consent before a deliberative run ends without converging (`HX-REPORT-02`, ADR-0063) and moves Pi support to 1.0.0 and later (ADR-0069). Phase A is the decisions: the scenarios that no test covers yet are listed in section 8.2 and fail `make contract-check` until phase B writes their tests. Identities below are stable.

**Why it exists.** Host-lifecycle cases were found one live test at a time (not-yet-observed surface, host failure, a turn end that pauses for the human). Scenarios had been defined per feature and never as a complete model of the host boundaries and their closed outcomes. A fix (F5, "the Claude Stop never pauses for evidence") then regressed a behaviour that was legitimately needed. This registry is the single list of boundaries, their closed outcomes per host, and the sources that fix each one. It collects what is already known; it does not re-discover it.

**Model.** Heath's boundary-and-expectation registry (`heath/eval-heath/docs/contracts/agent-contract-surface.md` AS3, AS7) and its falsifiable acceptance matrix (`docs/decisions/0023-…` AWL-01…17). The fields below follow AS3 (`trigger`, `obligation`, closed `outcomes`, `failure_policy`, `recovery`, `visibility`, `forbidden_substitutions`, `conformance_scenarios`) plus Empirica's `sources`. AS7 is a rule of this registry: **a failing mechanism reports explicit non-delivery, non-observation or non-enforcement for its boundary and never falls back silently to a weaker one.**

## 0. How to read this document

- **Identities.** Boundary `HB-<NAME>`; expectation `HX-<NAME>-<nn>`; scenario `EHB-<NAME>-<nn>`. A scenario id is permanent: the conformance test, the qualification ledger row and the review brief all use it. A changed meaning needs a new id, never a reused one.
- **`UNSOURCED`.** An outcome or rule that no source below fixes. It is listed so that the user decides it; it is not a rule.
- **`PROPOSED`.** A new ruling offered for the user's decision. Not implemented, not binding.
- **`CONFLICT`.** Two sources disagree. Listed in section 8.3.
- **Hosts.** Claude = Claude Code, range `>=2.1.278,<2.2.0`, qualified 2.1.294 (installed). Pi = `>=1.0.0,<2.0.0` (user ruling 2026-10-08; nothing older is supported), qualified 1.1.0 (installed). Codex = CLI 0.146.0, `observational`, `wip_unsupported`. "Not declared" means the host profile and ADRs say nothing for Codex.
- **Paths.** `P/` = `plugins/empirica/`. Test shorthand: `CT` = `P/adapters/claude/tests/`, `PT` = `P/adapters/pi/test/`, `V2` = `P/tests/v2/`, `TT` = `P/tests/`. Research and build documents live outside the repository, under `…/ui/.research/citations/`; they are cited as `research:<path>`.

## 1. Sources

| Key | Source |
|---|---|
| ADR-19, 32, 33, 44, 48, 51, 52, 60, 63, 65, 67, 68, 69 | `doc/adr/00NN-*.md` (69 = *Admit hosts and audit runtimes by range and observed capability*; 68 = *Record host boundaries as data and end the Claude Stop loop by control mode*; 67 = *Verify every research record by its class and surface every confirmation it still needs*, with its Scenarios table; 52 carries the 2026-10 amendment) |
| D1H | `doc/design/empirica-2.0-d1h-host-capabilities.md` (host tiers, Claude and Pi binding protocols, conformance cases, stop conditions) |
| T1, T2, T4 | `research:reports/T1-interaction-trace.md`, `T2-boundary-trace.md`, `T4-unobserved-surface.md` (T4 = the 17-row surface scenario matrix) |
| PROG | `research:build/PROGRESS.md`, by round heading (F7, F8, A7, S5, G1) |
| F5, F7, F8 | `research:build/F5-fixes.md` (rulings A2-1/A2-2: remove the surface note, the Claude Stop never settles as `evidence_wait`), `F7-host-faithful.md`, `F8-host-failure.md` |
| A1…A8 | `research:reviews/A*-final.md` |
| Q1, Q2 | `~/empirica-qualification/2026-10-07-claude/RESULT.md` (FAIL, never-asked defect) and `…-claude-2/RESULT.md` (STOPPED, the Stop-pause gap) |
| QREF | `.agents/skills/native-qualification/references/{claude,pi,scenarios}.md` (ledger row ids) |
| CC | Claude Code hooks reference, `https://code.claude.com/docs/en/hooks`, retrieved 2026-10-08 (Stop, SubagentStop, PreCompact, PreModelSwitch/PostModelSwitch, SessionEnd, Elicitation) |
| PI-T | Pi `dist/core/extensions/types.d.ts` and `CHANGELOG.md`, from the registry tarballs of 0.84.1 … 1.0.4 and 1.1.0 (G1 evidence, PROGRESS "G1", B4) |
| PS | pi-subagents source from registry tarballs 0.50.0 / 0.64.0 / 0.74.0 / 0.75.0 / 0.76.1 (G1 evidence, PROGRESS "G1", B1–B3) |
| CODE | `file:line` in this worktree at HEAD `8ec87ea` plus the uncommitted rounds through G6 |
| HEATH | the two Heath documents named above |

## 2. Closed vocabularies

These are the only values an outcome may take. A host that produces anything else has a defect.

**Human-decision outcomes** (Heath AS3). `approved | declined | no-decision`. **Dismissal, Esc, cancel, timeout, reload, shutdown and loss of the interaction surface are `no-decision`.** They are never recorded as decline or approval. `declined` requires an explicit human choice that the host observed. In Empirica's dialogs:

| Dialog | `approved` | `declined` | `no-decision` |
|---|---|---|---|
| Configuration review (ADR-63) | Approve | Reject (MCP `action: decline`; Pi reject choice) | dismiss, cancel, timeout, malformed answer, invalid content, stale proposal |
| Locked confirmation of an edit | Confirm | — (an MCP `decline` is **dismiss**, `adapters/governance.py:138-139`) | everything else |
| Evidence item, research record (ADR-67) | Confirm | Reject (with optional note) | Later, cancel, timeout, decline at form level, malformed |
| Evidence item, `needs-decision` claim | Approve (with optional answer) | Reject | Later, cancel, timeout, decline at form level, malformed |

**Gate results** (what a core evaluation can say to a host): `Allow`, `Inert`, `Block` (with closed reasons), `Fault` (closed, with `fail_direction`). The core alone decides; hosts observe, enforce and translate (D1H §1, ADR-33, ADR-63 "Host-neutral boundary").

**Failure policies** (AS3): `refuse-transition` (do not move), `withhold-claim` (never claim convergence/approval), `pause` (hand control back, state kept, explicit "not converged"), `report-limitation` (say what is not delivered, observed or enforced).

**Visibility** (AS3): `model-context` (the author model sees it), `human-ui` (the human sees it), `approval-disclosure` (inside what a human is asked to approve).

**Host failure values** (F8): `surface_unrecorded`, `presentation_failed`, a closed tool error (no `next:`), a double fault. Host-neutral facts, never stored: `EvaluateRun.host_failure` (ADR-67 "A host that fails to ask says so").

**Surface states** (ADR-67, F7): `unobserved | human | unavailable`. **Prompt errors**: `unavailable | unobserved | already_presented | interaction_limit | surface_unrecorded | presentation_failed | null`.

## 3. The boundaries

One entry per lifecycle point. "Claude mechanism" and "Pi mechanism" name the native hook or event that implements it (AS7: every observed or enforced entry names its mechanism).

| Id | Boundary | Definition | Claude mechanism | Pi mechanism | Codex |
|---|---|---|---|---|---|
| `HB-ACT` | Run start / activation | The point at which a human-typed command asks for an Empirica run and the host creates (or refuses) it. | `UserPromptExpansion` `^empirica:empirica$` → `hooks/run_start.py` (`P/hooks/hooks.json`) | `/empirica` command, `registerCommand` (`P/adapters/pi/src/index.ts:676`) | `$empirica` best-effort (`P/adapters/codex/README.md` "Surface") |
| `HB-ADM` | Host and package admission | Whether the observed host version and, on Pi, the observed pi-subagents owner and version are admitted for the run. | `claude --version` range in the host profile; nothing at run start (see UNSOURCED) | Pi range in the profile; at runtime the pi-subagents owner and its version inside the `subagents_compatibility` range (ADR-0069) | exact `codex-cli` profile, `wip_unsupported` |
| `HB-GOV` | Route and configuration approval | `route`, graph, `configure_run` proposal, host presentation of the sized proposal, and the human decision that installs ceilings (ADR-63). | MCP elicitation form, `adapters/governance.py` | `ctx.ui` dialogs, `governance-ui.ts` | no human ingress; delegated envelope 8/1/2 only |
| `HB-TOOL` | Tool call | The moment before any tool executes while a run is active. | `PreToolUse` (`Agent` → `spawn_gate.py`; `.*` → `route_stamp.py`) | `tool_call` (`index.ts:845`) | `PreToolUse` (declared in the Codex adapter; not read in G1) |
| `HB-RESULT` | Tool result | The moment a tool's outcome returns, including the audit child's result. | `PostToolUseFailure` for `Agent` (`agent_failure.py`); `SubagentStop` carries the audit result (see `HB-AUD-RES`) | `tool_result` (`index.ts:766`) | Stop-owned bounded process (D1H §5) |
| `HB-STOP` | Turn end | The host finishes responding and would hand control to the human. | `Stop` → `hooks/convergence_gate.py`; `SubagentStop` | the turn-end table (`turn_end`): Pi acts at `agent_before_settle` (Pi 1.0.0 and later); `agent_settled` only notifies. The report gate is `HB-REPORT` | `Stop` hook: only `Allow(converged=true)` permits stop |
| `HB-REPORT` | `report_convergence` | The author's guarded request to be done. | public MCP tool `report_convergence` (`mcp_server`, `public_tools.py`) | `registerTool` `report_convergence` (`index.ts:543`), gated again in the `tool_call` handler (`index.ts:845`) | public MCP tool, public-only |
| `HB-OBS` | Surface observation | The host records whether it can show a human a dialog (`invocation.human_surface`). | inside `report_convergence`, `HostConfirmation.observe` (`adapters/confirmation.py`) | at start (`ctx.hasUI`) and each report (`observeSurface`) | none (`unavailable` at start) |
| `HB-PRES` | Presentation of a confirmation | The host shows one batch of evidence items and records that it did. | `HostConfirmation` form (MCP elicitation) | `evidence-ui.ts` (`ctx.ui.select/input`) | none |
| `HB-DLG` | Dialog outcome | What a human did with a dialog, or what happened to it. | MCP `accept | decline | cancel`, hook timeout | `ctx.ui` return / `undefined`, timeout option | none |
| `HB-INT` | Esc / interrupt | The human interrupts the running turn. | `Stop` does **not** run on a user interrupt (CC "Stop") | `agent_settled.aborted` (`PI-T`); adapter registers nothing | not declared |
| `HB-COMPACT` | Compaction | The host compacts the context (`/compact`, threshold, overflow). | `SessionStart:compact` → `state_restore.py` (CC `PreCompact`/`PostCompact` exist; unused) | `session_before_compact` (`index.ts:1001`); `session_compact_failed` exists (`PI-T`), unused | `SessionStart` source `compact` (`codex/lifecycle.py:10`) |
| `HB-MODEL` | Model switch | The main model changes, or the run needs the author/reviewer identities again. | `PostModelSwitch` → `route_stamp.py` (`_governance_context`, `to_model`); `_auditor_alias` at audit launch | `model_select` (`index.ts:506`) and `refreshGovernance` on every `tool_call` | none |
| `HB-AUD-LAUNCH` | Audit launch | The reservation, binding and launch of the one canonical auditor. | `PreToolUse:Agent` → `spawn_main`; `SubagentStart` | `tool_call` for the `subagent` tool, `audit_prepare` | Stop-owned `codex exec` |
| `HB-AUD-RES` | Audit result | The auditor's output arrives and its verdict and identity are admitted or failed. | `SubagentStop` (`subagent_stop_main`) | `tool_result` (`index.ts:766-830`) | Stop-owned, unverified identity |
| `HB-FAIL` | Host failure | A host mechanism fails to observe, present, deliver or enforce. | the `surface_unrecorded`/`presentation_failed` machinery | same, in-process | `Fault` closed |
| `HB-SESSION` | Resume / reload / shutdown | Session start, restore, reload, quit. | `SessionStart` (all sources), `SessionEnd` (unused) | `session_start`, `session_shutdown` | `SessionStart` |
| `HB-VIEW` | Observer read | The read-only observer UI shows the run store. | `/empirica:ui` | `/empirica-ui` | `make ui` / CLI |

## 4. Expectations

Each entry lists `trigger`, `obligation`, closed `outcomes` (Claude / Pi / Codex), `failure_policy`, `recovery`, `visibility`, `forbidden_substitutions`, `conformance_scenarios` and `sources`.

### HB-ACT — run start / activation

#### HX-ACT-01 — a human-typed command starts a run with the verbatim goal
- **trigger.** `/empirica:empirica <goal>` (Claude), `/empirica <goal>` (Pi), `$empirica <goal>` (Codex).
- **obligation.** Dispatch `StartRun` with the goal substring verbatim, the trusted invocation provenance and the control mode; inject the opaque handle and the public-tool contract; create no run for an empty goal or an unknown flag.
- **failure_policy.** `refuse-transition`: no run, no handle. An active-run lookup that cannot be proven absent is not "no run" (`CT/test_activation_lifecycle.py::test_inactive_git_marker_with_git_failure_is_not_proven_absent`).
- **recovery.** `StartRun` is idempotent per selector and generation (ADR-19); `ResolveRun` never allocates (ADR-33).
- **visibility.** `human-ui` (block reason); `model-context` (`additionalContext` with the handle).
- **forbidden_substitutions.** A model-invoked skill, a read of the command file, or pasted chat is not activation (QREF claude.md:3-4). A guessed handle is not a run.
<!-- BEGIN GENERATED: HX-ACT-01 (make registry-doc) -->
- **outcomes.**
  - Claude: `started` | `refused` (structural `Block`: empty goal, unknown flag, non-interactive auto without delegation → "Empirica did not start: …", exit 0) | `faulted` (same block, operator-visible code) | `internal-error` (block "internal error"). Start never wedges prompt expansion.
  - Pi: `started` | `refused` (notify "/empirica could not start a run: …") (the `/empirica` command's own refusals: empty goal, unknown flag, owner refusal).
  - Codex: best-effort `started` or refused; non-delegated auto and deliberative refused.
- **conformance_scenarios.** `EHB-ACT-01`, `EHB-ACT-02`, `EHB-ACT-03`
- **sources.** ADR-19 (Creation), ADR-33, ADR-60 ("A run requires a nonblank goal … Trusted host activation records invocation provenance"), QREF scenarios `activation_verbatim_goal`, `empty_goal_refusal`, `noninteractive_auto_refusal`; CODE `P/adapters/claude/lifecycle.py:291-324`, `P/adapters/claude/run_start.py`.
<!-- END GENERATED -->

#### HX-ACT-02 — invocation provenance and the initial human surface
- **trigger.** The `StartRun` provenance is computed.
- **obligation.** Record `interactive` (true/false/unknown), `delegation`, `audit_retrieval` and `human_surface` exactly as the host can observe them at that moment; do not borrow a fact the host has not observed.
- **failure_policy.** `report-limitation`: an unknown fact is carried as unknown, never defaulted to a stronger one.
- **recovery.** The surface is re-observed at each report (`HB-OBS`).
- **visibility.** `model-context` (run view route), `human-ui` (observer).
- **forbidden_substitutions.** Defaulting an unobserved surface to `unavailable` (the live defect Q1) or to `human`.

### HB-ADM — host and package admission
<!-- BEGIN GENERATED: HX-ACT-02 (make registry-doc) -->
- **outcomes.**
  - Claude: `interactive` from `CLAUDE_CODE_ENTRYPOINT` (`cli` true, `sdk-cli` false) then the transcript, else unknown; `human_surface = unobserved` (the MCP client has not yet declared form elicitation); `audit_retrieval = {local, workspace}` (the auditor definition, `WebFetch` never credited).
  - Pi: `interactive` true for `ctx.mode` tui/rpc, false for print/json; `human_surface` = `human` iff `ctx.hasUI`, else `unavailable`; never `unobserved`; `audit_retrieval` from the live preflight.
  - Codex: no human ingress → `unavailable`; `audit_retrieval = []`.
- **conformance_scenarios.** `EHB-ACT-04`, `EHB-ACT-05`
- **sources.** ADR-67 "Confirmation requirement" and "Human surfaces"; T4 §1; Q1; F7 (PROG "F7 host-faithful round"); CODE `P/adapters/invocation.py:provenance`, `P/adapters/claude/run_start.py:invocation_provenance`.
<!-- END GENERATED -->

#### HX-ADM-01 — the host version is inside the interval and a receipt is from the qualified version or later
- **trigger.** A run is started, or a live receipt is captured, on an observed host version.
- **obligation.** A host outside the profile's half-open interval is not admitted, and a receipt from a host older than the profile's qualified `version` does not qualify it. A range proves nothing alone: the full lifecycle trace and a native receipt from the qualified version or later are required (ADR-52, ADR-0069).
- **failure_policy.** `report-limitation`: ADR-52 puts compatibility enforcement at receipt capture and verification so a harness patch update needs no Siffran release; **no new run-start refusal for the host version is added**. The runtime refusal that does exist is a pi-subagents version outside its range (`HX-ADM-02`). The limitation (a run on a host outside the interval is not refused at start; its receipt would be) is part of what the activation text discloses.
- **recovery.** Receipts record the exact observed version (ADR-52).
- **visibility.** `approval-disclosure` and `human-ui` at activation (AS7: limitations are shown at activation), `UNSOURCED` for the wording.
- **forbidden_substitutions.** A `latest` tag or a semver range as proof; a receipt from before the qualified version counted as qualifying it; the receipt-verifier check being described as a runtime refusal.
<!-- BEGIN GENERATED: HX-ADM-01 (make registry-doc) -->
- **outcomes.**
  - Claude: interval `>=2.1.278,<2.2.0`; qualified version `2.1.294`; profile id `claude-code`, which never carries a version (ADR-0069). Compatibility is enforced **at receipt capture and verification** (`scripts/empirica_live_receipts.py:require_compatible_version`), not at run start.
  - Pi: `>=1.0.0,<2.0.0` (user ruling 2026-10-08; nothing older is supported, ADR-0069); qualified version `1.1.0`; profile id `pi+pi-subagents-foreground-audit-v1` (the policy, never a version, ADR-0069). Enforced at receipt time.
  - Both: a receipt is refused unless its host version is inside the interval and at or above the profile's qualified `version` ("a native receipt from <host> >= <qualified version> is required"); raising the qualified version in the profile therefore asks for exactly one new receipt.
  - Codex: exact `0.146.0`.
- **conformance_scenarios.** `EHB-ADM-01`, `EHB-ADM-02`
- **sources.** ADR-52 (decision and its amendments), ADR-0069 (G6 amendment: the receipt floor), `contracts/empirica/v2/host-profiles.json` (`version` and `compatibility` of the claude-code and pi profiles), `scripts/empirica_live_receipts.py` (`require_compatible_version`), PROGRESS "User ruling 2026-10-08". Tests: `scripts/tests/test_empirica_live_receipts.py::test_compatible_harness_patch_versions_pass_and_are_recorded_exactly`, `::test_a_receipt_below_the_qualified_version_is_refused`, `::test_raising_the_qualified_version_in_the_profile_asks_for_exactly_a_new_receipt`, `::test_incompatible_or_misreported_harness_versions_fail`.
<!-- END GENERATED -->

#### HX-ADM-02 — the pi-subagents owner is bound and its version is in the supported range, or the run is refused
- **trigger.** `session_start` observes the `subagent` tool's owner; `/empirica`, `configure_run` and every `subagent` `tool_call` read it.
- **obligation.** Exactly one owner is bound; its package version lies in the profile's half-open range `subagents_compatibility` `[minimum, maximum_exclusive)` (`>=0.50.0,<1.0.0`, ADR-0069). A version with a checked-in compat record (`compat/pi-subagents-<v>.json`: 0.50.0, 0.64.0, 0.74.0, 0.75.0, 0.76.1) is *reviewed* and its inventory classifies every call. Any other version in the range is admitted by observation: its own `subagent` parameter schema, read at session start, declares every field the audit launch sends (`AUDIT_LAUNCH_PARAMETERS`). Every version, reviewed or not, must pass the live launch preflight at start and at launch, which the seam (`preflight-seam.ts`) checks for: a foreground, inline, `context: "fresh"` request; a contract version in `[2, 3]` (and the record's, when there is one); the package version equal to the owner's; the packaged canonical auditor file; the configured model; and an effective allowlist inside `audit_child_tools`, else `tools-not-read-only`. The definition digest is checked by `make empirica-subagents-matrix` for reviewed versions only. An unreviewed version is recorded `reviewed: false`, shown on every run view while it stays unreviewed, and a `subagent` action runs during a run only if some reviewed version has that name and the classifier calls it read-only management. An owner that changes after binding refuses the audit before any reservation; the observed schema is not compared again.
- **failure_policy.** `refuse-transition` before any reservation, investigation record or launch (`host_runtime.rejection` codes `unexpected | missing | malformed | policy | package | version | paths`, `P/application/host_runtime.py`).
- **recovery.** A deactivated/reactivated tool re-admits; a changed owner refuses (`owner-changed`) until the session is re-established.
- **visibility.** `model-context` (refusal reason), `human-ui`.
- **forbidden_substitutions.** A version outside the range, however similar to one inside it; a version admitted without its own parameter schema declaring every audit-launch field; a `latest` tag as proof; an action of a version with no compat record admitted on its own say-so, without a reviewed version carrying that name and the classifier calling it read-only; an auditor given a tool outside `audit_child_tools`; an unreviewed version shown as reviewed.
<!-- BEGIN GENERATED: HX-ADM-02 (make registry-doc) -->
- **outcomes.** Pi: `bound` (reviewed: a compat record exists; or not reviewed: admitted by the owner's own parameter schema and live preflight, shown in the run view; only a `subagent` action some reviewed version has and the classifier calls read-only management runs) | `refused` with one of the closed `OwnerRefusalCode`s (`missing-tool`, `multiple-owners`, `owner-unobservable`, `package-not-pi-subagents`, `version-unobservable`, `child-process`, `preflight-unavailable`, `version-unsupported`, `tool-inactive`, `owner-changed`), each mapped to a public `host.subagents_*` reason (`owner-refusal.ts`) | `unsupported-call` (launch form the owner's version removed, control/mutation action, two launch forms). Claude / Codex: not applicable (no external runtime; a recorded one is `unexpected`). The launch preflight is also refused (`tools-not-read-only`, at start and at launch, any version) when its effective allowlist holds a tool outside `audit_child_tools`; the refusal text carries the contract message of `host.subagents_tools_not_read_only` (`residual.accept`).
- **conformance_scenarios.** `EHB-ADM-03`, `EHB-ADM-04`, `EHB-ADM-05`, `EHB-ADM-07`, `EHB-ADM-08`
- **sources.** ADR-52 amendment; ADR-0069 (G5-R2, capability admission in a range); D1H §4; PROGRESS "S5" and "G5 flexibility round"; `PT/gate.test.ts` owner tests; `scripts/empirica_subagents_matrix.mjs`.
<!-- END GENERATED -->

#### HX-ADM-03 — Claude reviewer derivation resolves, or the audit refuses
- **trigger.** Audit launch on Claude.
- **obligation.** The reviewer alias comes from a family different from the observed main family; an explicit `CLAUDE_CODE_SUBAGENT_MODEL` is left to the host; third-party providers need the `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` pin.
- **failure_policy.** `refuse-transition` (deny the launch); the audit stays owed.
- **recovery.** None needed; the author fixes configuration.
- **visibility.** `model-context` (denial reason with the pin guidance).
- **forbidden_substitutions.** A same-family reviewer; trusting the requested alias as identity (identity is observed from the transcript at result time).

### HB-GOV — route and configuration approval
<!-- BEGIN GENERATED: HX-ADM-03 (make registry-doc) -->
- **outcomes.** Claude: `alias-chosen` | `host-decides` (explicit subagent model) | `refused` (main family unobservable, or no distinct alias resolvable → `AuditProtocolError`, spawn denied). Environment of record: Bedrock, main `opus` (Opus 4.8 [1m]) → reviewer `sonnet` (Sonnet 5.5); the Haiku pin is reached only if the Sonnet pin is absent; main Sonnet 5.5 → `opus` (tests `EHB-MODEL-04`, `-05`, `-06`, S5-R4, no product change). Pi: model chosen by host settings order (canonical agent override, subagent default, main), refused if the effective reviewer identity equals the author's. Codex: not declared (no auditor model).
- **conformance_scenarios.** `EHB-ADM-06`, `EHB-MODEL-02`, `EHB-MODEL-04`, `EHB-MODEL-05`, `EHB-MODEL-06`
- **sources.** ADR-60 "Governance and invocation"; `P/adapters/claude/lifecycle.py:252-272`; PROGRESS "S5" S5-R4; QREF claude.md (settings `env` must reach hooks).
<!-- END GENERATED -->

#### HX-GOV-01 — nothing but preparation runs before an approved configuration, and route precedes investigation
- **trigger.** Any investigative tool call or `report_convergence` while the configuration is unapproved, or before `route` and `investigate` witnesses exist.
- **obligation.** The first successful approval is a human decision (or the fixed delegation policy) on an author-sized proposal with a 1–600 character rationale. Preparation (`route`, `graph`, `configure_run`, the honest stop) is exempt. Route and investigation are strictly ordered witnesses.
- **failure_policy.** `refuse-transition`. An adapter failure on an active run denies (fail closed).
- **recovery.** Re-propose `configure_run`; presentation limits are 3 per revision and 128 per run.
- **visibility.** `model-context` (reason), `human-ui` (the dialog).
- **forbidden_substitutions.** Approval inferred from prose or from a pause notice; an author-submitted trusted ingress.
<!-- BEGIN GENERATED: HX-GOV-01 (make registry-doc) -->
- **outcomes.** Claude: `denied` (PreToolUse exit 2 with the core reason: `governance.approval_required`, `route.required`, `investigation.required`, …) | `admitted` (exit 0). Pi: `{block: true, reason}` | admitted. Codex: delegated acceptance only.
- **outcomes_by_mode.** All four modes: nothing but preparation runs before an approved configuration, and route precedes investigation. What differs is the source of the approval: deliberative and auto-interactive-pre ask a human; auto-interactive-post and auto-delegated approve automatically inside the approved ceilings (auto-delegated: the fixed 8/1/2 envelope).
- **conformance_scenarios.** `EHB-GOV-01`, `EHB-GOV-02`
- **sources.** ADR-48, ADR-63, ADR-56; QREF `sentinel_blocked_before_approval`, `configuration_pending`; CODE `P/adapters/claude/lifecycle.py:390-416` (`route_main`), `P/adapters/pi/src/index.ts:845-880`.
<!-- END GENERATED -->

#### HX-GOV-02 — a configuration dialog ends in approve, reject or no-decision
- **trigger.** The host presents the sized proposal (`present` ingress), then the human acts or does not.
- **obligation.** Record exactly one of the closed outcomes. `approve` installs ceilings only after an exact final approval (edits go through a host-owned locked confirmation); `reject` records a rejection; **everything else records a dismissal**, grants nothing, installs no budget, and never falls back to automatic acceptance.
- **failure_policy.** `withhold-claim` + `pause`: the host settles the wait with the contract notice `human_wait` ("paused for human governance input, not converged … no approval was granted by this pause") only for the sole, legitimate initial-approval blocker; mixed blockers, malformed responses and exhausted interactions block with the real reason.
- **recovery.** A fresh human-directed review may resume within the interaction limits; the dismissal and its presentation are durable; no automatic re-prompt; revision counts are bounded by ADR-63.
- **visibility.** `human-ui`, `approval-disclosure` (the dialog shows ceilings, rationale labelled unverified, and any amendment).
- **decline mapping (C-02 resolved: no change).** One native action, three disclosed meanings, each already written on the dialog itself and each an explicit, disclosed human choice (Heath AS3): the configuration review's decline is the explicit **Reject** (ADR-63 "the human may dismiss, amend, approve, or reject"; `governance.py` review text "Decline = reject"); the locked confirmation's decline is **no-decision** because the dialog says "keep edits pending" (`governance.py:128`); the evidence form's decline is **no-decision** because the form says "Decline/Esc = send none" (`confirmation.py:210`; `HX-DLG-01`), the explicit human decline there being the per-item Reject (ADR-67 "One decision per item"). All three grant nothing (ADR-63 "Dismissal, timeout, cancellation, rejection, or a declined confirmation grants no consent"). Tests: `TT/test_governance_hosts.py::test_decline_rejects_without_admitting_work`, `::test_locked_confirmation_decline_keeps_edits_pending_without_feedback_form`, `V2/test_evidence_hosts.py::MediatorTests::test_cancel_timeout_decline_and_malformed_answers_grant_nothing`.
- **forbidden_substitutions.** Dismissal recorded as decline or approval; an `auto` receipt for an interactive invocation; a pause notice treated as consent.

### HB-TOOL / HB-RESULT — tool calls
<!-- BEGIN GENERATED: HX-GOV-02 (make registry-doc) -->
- **outcomes.**
  - Claude: `approved` | `declined` (MCP `decline` on the review form, which says "Decline = reject · Esc = decide later") | `no-decision` (cancel, timeout, malformed or invalid content, wrong id, stale proposal, no form). **An MCP `decline` on the locked confirmation is `no-decision`** ("Decline/Esc = keep edits pending"); the code is `adapters/governance.py:decision()`, `Dismiss() if confirmation else Reject()`.
  - Pi: `approved` | `declined` | `no-decision` (ui `undefined`, timeout, no UI → `governance.approval_unavailable`).
  - Codex: not declared (no dialog).
- **outcomes_by_mode.** deliberative: a dialog before investigation and for every material change (locked confirmation when amended). auto-interactive-pre: one human approval episode before investigation. auto-interactive-post: no further dialog; non-raising changes are accepted automatically and raises are refused. auto-delegated: no dialog; proposals inside the fixed envelope only. `expected_approval_kind` (`host_ui` or `auto`) decides.
- **conformance_scenarios.** `EHB-DLG-01`, `EHB-DLG-02`, `EHB-GOV-03`, `EHB-GOV-04`
- **sources.** ADR-63 ("Auto mode: one human approval episode", "Receipts"), ADR-60; HEATH AS3; CODE `P/adapters/governance.py:133-160,205-250`, `P/adapters/pi/src/governance-ui.ts:190-215`, `P/adapters/claude/completion.py:_human_approval_wait`; tests `TT/test_governance_hosts.py::test_cancel_timeout_and_wrong_id_never_approve`, `::test_decline_rejects_without_admitting_work`, `::test_dismissals_durable_and_no_fourth_dialog`, `::test_locked_confirmation_decline_keeps_edits_pending_without_feedback_form`.
<!-- END GENERATED -->

#### HX-TOOL-01 — an investigative tool call is gated while a run is active
- **trigger.** `PreToolUse` (Claude), `tool_call` (Pi) for any tool except the public Empirica tools, `ToolSearch` and `AskUserQuestion`.
- **obligation.** Record the investigation witness through the core, deny when the core denies or is unavailable. Unknown tools (including third-party MCP research and writers) fail closed. Subagents cannot admit main-author evidence.
- **failure_policy.** `refuse-transition`; no run → inert (nothing to protect).
- **recovery.** Strict resolution distinguishes an exact "no run" from unavailability; unavailability denies.
- **visibility.** `model-context`.
- **forbidden_substitutions.** Marker text in a Bash command cannot exempt a call; a suffix match of an MCP tool name cannot spoof a public tool.
<!-- BEGIN GENERATED: HX-TOOL-01 (make registry-doc) -->
- **outcomes.** Claude: exit 0 | exit 2 with reason. Pi: permit | `{block:true, reason}`. Codex: declared hook, behaviour not read in G1.
- **conformance_scenarios.** `EHB-TOOL-01`, `EHB-TOOL-02`, `EHB-TOOL-03`
- **sources.** ADR-48; `P/adapters/claude/route.py:INVESTIGATIVE_TOOLS, build_investigation_request`; `CT/test_claude_adapter.py::test_investigation_hook_fails_closed_for_active_run`, `::test_unknown_mcp_research_and_writers_require_admission_but_public_preparation_does_not`, `::test_subagent_cannot_admit_main_author_evidence`; `PT/gate.test.ts` ("gate: an investigative tool records investigation before execution", "… is blocked when route ordering is denied").
<!-- END GENERATED -->

#### HX-TOOL-02 — Pi `tool_result` is judged by `isError` alone
- **trigger.** A tool result returns on Pi (proven at 1.0.0 and 1.1.0; the range is declared, and each later 1.x release needs its own typecheck and receipt).
- **obligation.** A failed result is recognised from `isError === true`. G1 evidence: no in-range Pi version declares or emits `tool_result.error`; the adapter's `event.error !== undefined` branches were dead and are **removed in G2** (S5-R1 outcome; `index.ts`, `audit.ts`, and `error?` from the `pi-types.ts` mirror), proved by `EHB-AUDRES-04 (isError)` and the Pi API typecheck over 1.0.0 and 1.1.0. Redaction must also cover any `structuredContent` a tool returns (declared by every supported Pi).
- **failure_policy.** `refuse-transition` for a failed audit child; `report-limitation` for the redaction gap (none today: the `subagent` tool declares no `outputSchema`).

### HB-STOP — turn end

The host finishes responding and would hand control to the human. **One table decides what happens, by control mode and run state, and it is the same on every host** (ADR-0068). A host only maps the table to its own surface.

<!-- BEGIN GENERATED: turn_end (make registry-doc) -->
| Outcome | Meaning |
|---|---|
| `allow` | End the turn: the run converged and the view says so (`Allow(converged=true)` is the only convergence claim). |
| `settle` | End the turn with the run view and no notice: exactly one audit is pending and its result continues the run. |
| `settle-notice` | End the turn with the contract notice of the settlement; the run stays active and not converged, and nothing was approved or confirmed by the settlement. |
| `pause` | End the turn with a contract notice that is not a convergence claim and names the real next step the human can relay to the agent; the run stays active, nobody was asked, nothing was granted. The same on a first and a later turn end. |
| `continue` | Keep the turn going: the agent is handed the next obligation (the core's real reason; the evidence directive when a human can be asked about the sole blocker; `unattended_continue` on a later turn end of the same chain). A `continue` cell names the notice of a later turn end; the first turn end of the chain gives the core's reason, or the evidence directive where one is named. |
| `end-no-progress` | End the turn with the `unattended_continue` notice because a host with no loop bound of its own has already continued on this observation basis; the adapter records `no-progress`. |

| Run state | `deliberative` | `auto-interactive-pre` | `auto-interactive-post` | `auto-delegated` |
|---|---|---|---|---|
| the run converged (`converged`) | `allow` | `allow` | `allow` | `allow` |
| exactly one async audit is pending (`async-audit-pending`) | `settle` | `settle` | `settle` | `settle` |
| the sole blocker is a human governance wait or an exhausted budget (`settlement`) | `settle-notice` (`human_wait`, `budget_exhausted`) | `settle-notice` (`human_wait`, `budget_exhausted`) | `settle-notice` (`human_wait`, `budget_exhausted`) | `settle-notice` (`human_wait`, `budget_exhausted`) |
| the sole blocker is an evidence confirmation a human can be asked about (`evidence-wait`) | `pause` (`paused_for_evidence`) | `continue` (`evidence_report`) | `continue` (`evidence_report`) | `continue` (`unattended_continue`) |
| any other active, non-converged state (`active-other`) | `pause` (`paused_for_human`) | `continue` (`unattended_continue`) | `continue` (`unattended_continue`) | `continue` (`unattended_continue`) |

| Host | event | allow | settle | pause | continue | loop bound | table-driven test |
|---|---|---|---|---|---|---|---|
| claude | `Stop` (not fired on a user interrupt) | exit 0 with the run view | exit 0; the run view (async audit) or `systemMessage` with the notice | exit 0 with `systemMessage` | exit 2 with the reason on stderr (the real reason, or the `evidence_report` directive), and `unattended_continue` when `stop_hook_active` is true | Claude's `stop_hook_active` plus its own cap of eight consecutive continuations (`host-capped` otherwise). A deliberative pause ends the turn (ADR-0068 owns the `stop_hook_active` rule). | `EHB-STOP-23` |
| pi | `agent_before_settle` only (never `turn_end`, so one notice per settle); Pi 1.0.0 and later | nothing is appended | nothing is appended, or a `custom_message` (`display: true`) with the settlement notice | append a `custom_message` entry (`display: true`) holding the notice, with no `continue` | `continue: true`, with the next obligation as the appended message | None from the host. The adapter keeps its own: it requests a continuation only when the run's observation basis changed since the adapter's previous continuation; otherwise it ends with the `unattended_continue` notice and records `no-progress`. | `EHB-STOP-34` |

| Run state | claude | pi |
|---|---|---|
| `converged` | `EHB-STOP-12` | `EHB-STOP-29` |
| `async-audit-pending` | `EHB-STOP-12` | `EHB-STOP-35` |
| `settlement` | `EHB-STOP-03`, `EHB-STOP-04` | `EHB-STOP-36` |
| `evidence-wait` | `EHB-STOP-02`, `EHB-STOP-08`, `EHB-STOP-22` | `EHB-STOP-31`, `EHB-STOP-32` |
| `active-other` | `EHB-STOP-06`, `EHB-STOP-07`, `EHB-STOP-08`, `EHB-STOP-21` | `EHB-STOP-31`, `EHB-STOP-32` |
<!-- END GENERATED -->

**Why a deliberative turn end pauses.** Deliberative mode returns every change to the human (ADR-0063). A turn end the agent chose is a hand-off, and a gate that answers it with the next obligation pushes the agent past a checkpoint the operator asked for (finding F-Q3-1: the agent acted on each first block and skipped the operator's checkpoints). So in deliberative mode any other active state **pauses** on every turn end, first or later: the turn ends with a notice that names the real next step, so the human can relay it, and the run stays active. Nothing is approved, confirmed or terminalized by a pause and nobody was asked. An auto run has nobody to hand off to, so it **continues**; interactive auto keeps the evidence directive, because the report is what asks the human.

**What the hosts give** (Q3 surface §1, fetched 2026-10-08).

- **Claude Code 2.1.294.** `Stop` fires when Claude finishes responding; it does not run on a user interrupt and API errors fire `StopFailure`. Exit 0 (optionally with `systemMessage`) ends the turn; exit 2 or `decision: block` continues it, and the reason is fed back. `stop_hook_active` is `true` when Claude is already continuing because a Stop hook blocked; Claude caps consecutive continuations at eight (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`) and the count resets each time Claude calls a tool. Experiment `2026-10-08-exp-first-stop`: a **first** Stop that exits 0 with `systemMessage` ends the turn and shows the notice, as a continuation does, so a deliberative pause needs no flag.
- **Codex CLI 0.146.0.** `Stop` returns `continue:false` with `stopReason`, or `systemMessage` (a warning), to end the turn; `decision: block` with a reason continues it; `stop_hook_active` bounds the loop. Activation records the control mode (`adapters/codex/lifecycle.py`); today the Stop never reads it and blocks every non-converged turn end.
- **Pi 1.0.0 and later.** `agent_before_settle` can append a `custom_message` entry (`display: true`) or return `continue: true` for one more model request; `turn_end` fires earlier in the same boundary, so the adapter acts at `agent_before_settle` only and a settle carries one notice. `agent_settled` is a notification. Pi has **no** loop bound of its own ("guard continuation conditions"), so the adapter keeps one: it requests a continuation only when the run's observation basis changed since its previous continuation, and otherwise ends with `unattended_continue` and records `no-progress`.

**What Empirica does today (Claude, before phase B).** `stop_result(response, *, stop_hook_active)` (`P/adapters/claude/completion.py`) maps a core evaluation to `Inert`, `Allow`, the three settlements, the evidence directive or the real reason, and since G3 ends a **continuation** by control mode. It does not yet pause a deliberative first Stop; that is phase B item B1, together with Pi (no handler).

**The live results.** Q2 (deliberative): the agent stopped at the operator's checkpoint, Empirica's Stop blocked nine times ("Run a deterministic spike for this claim"), and Claude overrode it ("A hook blocked the turn from ending 9 consecutive times"). Q3 (deliberative): the second Stop paused correctly (`paused_for_human`), the flag persisted across one tool call, and the first Stop of each state pushed the agent past its checkpoints.
<!-- BEGIN GENERATED: HX-TOOL-02 (make registry-doc) -->
- **outcomes.** Pi: `failed` (`isError`) | `completed`. Claude: `PostToolUseFailure` reconciles an admitted-but-unstarted audit (`agent_failure_main`), observational, exit 0.
- **conformance_scenarios.** `EHB-AUDRES-04`, `EHB-INT-05`, `EHB-TOOL-04`
- **sources.** PI-T (G1 PROGRESS B4); PROGRESS "S5" S5-R1 (the `error` branch is kept only if some in-range Pi declares it, else removed — none does).
<!-- END GENERATED -->

#### HX-STOP-01 — a Stop never claims convergence, and an `Allow(converged=true)` is the only success
- **trigger.** A turn end on any host with an active run.
- **obligation.** A convergence claim only for `Allow(converged=true)`; no run → silent; fail closed on an unavailable gate. The outcome of every other turn end is the turn-end table (`HX-STOP-03`).
- **failure_policy.** `withhold-claim` for every non-`allow` outcome; `pause` for the `settle-notice` and `pause` outcomes (nonterminal notice, "not converged").
- **recovery.** State is durable in the run; a resumed turn re-evaluates.
- **visibility.** `model-context` (stderr reason or `systemMessage`), `human-ui` (the transcript shows hook feedback).
- **forbidden_substitutions.** A settlement that reads as convergence; converting a service `Block` into an `Allow`; a pause notice standing in for a human approval.
<!-- BEGIN GENERATED: HX-STOP-01 (make registry-doc) -->
- **outcomes.** Claude: `allow` (exit 0, view) | `inert` (exit 0, silent) | `settle-notice` (exit 0, `human_wait` or `budget_exhausted`) | `settle` (exit 0, view, one async audit) | `pause` (exit 0, `paused_for_human` or `paused_for_evidence`) | `continue` (exit 2: the real reason, `evidence_report`, or `unattended_continue`) | `fault-closed` (exit 2) | `fault-open` (exit 0, only on an explicit core `fail_direction: open`) | **`host-capped`** (Claude ends the turn after eight consecutive blocks, Empirica not consulted; Q2, `report-limitation`, never relied on). Pi: the same outcomes through its own surface (`HX-STOP-04`).
- **outcomes_by_mode.** The outcome by control mode and run state is the turn-end table (`turn_end`), the same on every host; this expectation owns only that `Allow(converged=true)` is the sole success.
- **conformance_scenarios.** `EHB-STOP-01`, `EHB-STOP-02`, `EHB-STOP-03`, `EHB-STOP-04`, `EHB-STOP-09`
- **sources.** ADR-32, ADR-51 (async audit settlement), ADR-63 ("The Claude Stop gate keeps its exact-reason check"), ADR-67 ("An interactive auto Stop never settles as an evidence wait"), ADR-68, D1H §3 (Claude binding protocol item 4), CC "Stop"/"Stop decision control", Q2; CODE `P/adapters/claude/completion.py`.
<!-- END GENERATED -->

#### HX-STOP-02 — evidence that only a report can show goes through the directive, then the real reason
- **trigger.** A turn end with `evidence.confirmation_required` as the only reason, route `human` or `unobserved`, `prompt_error` null or `unobserved`.
- **obligation.** In interactive auto, hand the agent the single directive `settlement_notices.evidence_report` ("Call report_convergence now: the human is asked inside that call, if this host can show a dialog") on every turn end; in every other state the real reason. The directive covers `unobserved` (F7). In **deliberative** mode the turn ends instead with `settlement_notices.paused_for_evidence`: the human is told to tell the agent to report, and that they are asked in that call, if this host can show a dialog. A delegated run's route is `unavailable`, so it has no directive.
- **failure_policy.** `withhold-claim`. Never `evidence_wait`.
- **recovery.** After the directed report, the response leads to stop (F8).
- **visibility.** `model-context` (the directive); `human-ui` (the pause).
- **forbidden_substitutions.** A turn end that settles as an evidence wait on a batch nobody has been shown: in Claude the human can only be asked inside `report_convergence` (MCP elicitation), so that settlement would end the turn with nobody asked (F5, A2-1/A2-2). A pause that says a human "will be asked".
<!-- BEGIN GENERATED: HX-STOP-02 (make registry-doc) -->
- **outcomes.** Claude: `continue` with the `evidence_report` directive (interactive auto) | `pause` with `paused_for_evidence` (deliberative) | `continue` with the real reason (any other blocker). Pi: not applicable (the Pi gate presents in-process at `report_convergence`).
- **outcomes_by_mode.** Interactive auto, first and later Stop: the directive (the act-on-it action is the report that asks the human). Deliberative, any Stop: `paused_for_evidence`, which tells the human to tell the agent to report, and that they are asked in that call, if this host can show a dialog. Delegated: the route is `unavailable`, so there is no directive.
- **conformance_scenarios.** `EHB-STOP-02`, `EHB-STOP-05`, `EHB-STOP-22`
- **sources.** F5 (rulings A2-1/A2-2), F7, ADR-67 ("Asking at the end of the pass"), `public-contract.json settlement_notices.evidence_report`; tests `V2/test_a1_boundaries.py::ClaudeStopEvidenceByControlMode::test_D_the_stop_blocks_with_the_directive_in_every_cell_and_the_mediator_shows_only_when_recorded`, `::test_the_directive_names_the_call_that_asks_the_human`, `CT/test_claude_adapter.py::test_an_interactive_auto_stop_never_settles_for_evidence_and_directs_the_author_to_report`, `V2/test_host_faithful.py::HostFailure::test_the_stop_directive_may_send_one_report_and_that_report_leads_to_stop`.
<!-- END GENERATED -->

#### HX-STOP-03 — a turn end is decided by the control mode and the run state, the same on every host (the turn-end table)
- **trigger.** A turn end of an active run (Claude `Stop`, Pi `agent_before_settle`).
- **obligation.** The outcome is the cell of the turn-end table for the run's control mode (read **only from the run view the core returned**, `governance.control_mode`) and run state. `allow`, `settle` and `settle-notice` do not depend on the mode. Any other active state: **deliberative pauses** (the turn ends with `paused_for_human` or, for the evidence wait, `paused_for_evidence`; the same on a first and on a later turn end, so `stop_hook_active` has no role in deliberative mode); **every auto run continues** (the next obligation, or the evidence directive in interactive auto, and `unattended_continue` on a later turn end of the same chain). An approvable first approval is settled earlier by `human_wait`; a first approval that cannot be asked here is an ordinary active state. No state is kept: no counter, no note. The loop bound is the host's: Claude's `stop_hook_active` and cap (an auto chain is bounded only by `intent=stop`), and Pi's adapter-owned no-progress rule (`HX-STOP-04`). If Claude resets `stop_hook_active` between continuations (ledger `stop_flag_reset`), an auto run's post-tool Stop blocks again and the outcome is `host-capped` (`report-limitation`).
- **failure_policy.** `pause` (deliberative) with `withhold-claim`: the run view and the observer still show "not converged". Auto: `refuse-transition` with the way out named; `intent=stop` is always admissible, so the block never repeats an inadmissible recovery (ADR-0064 interim, `fb1106b`).
- **recovery.** The next user prompt or `report_convergence` re-evaluates normally. No turn-end path terminalizes a run (ADR-0051).
- **visibility.** `human-ui` (the paused notice) and `model-context` (the reason, and for auto runs the way out).
- **forbidden_substitutions.** A pause that reads as convergence or as consent; a deliberative turn end that hands the agent the next obligation (it would carry the agent past the operator's checkpoint, F-Q3-1); an `evidence_wait` wording or any claim that a human "will be asked"; a human pause for any `auto` run (contradicts `_human_approval_wait`); terminalizing from the hook; a counter or note kept in run state (A2-1 removed exactly such a note); relying on Claude's 8-cap as the design; a per-host table that differs from `turn_end`.
- **earlier invariants (section 5):** F5, F7, F8, ADR-0051, ADR-0063 and ADR-0067 hold for the cells they own; the earlier per-host wording (a first Stop that is the same in every mode, and a Stop that never pauses) is superseded by the table.
- **live-only (host behaviour).** Whether a first Stop's `systemMessage` ends the turn and shows (`stop_first_pause`; the experiment showed it with a stand-in hook), whether `stop_hook_active` stays true across tool calls within an auto chain (`stop_flag_reset`), how Claude renders an exit-0 `systemMessage` (`stop_pause_rendering`), the auto continuation in a real session (`stop_unattended_directive`) and Claude's 8-cap (`stop_host_cap`) are ledger rows, not unit tests.
<!-- BEGIN GENERATED: HX-STOP-03 (make registry-doc) -->
- **outcomes.** Claude: `pause` (exit 0, `systemMessage`, `paused_for_human`/`paused_for_evidence`; the same on the first and on a later Stop; ADR-0068 owns the `stop_hook_active` rule) | `continue` (exit 2; `unattended_continue` when `stop_hook_active` is true) | unchanged settlements (see `HX-STOP-01`). If the host resets `stop_hook_active` between continuations, an auto run's post-tool Stop blocks again and the outcome is `host-capped` (`report-limitation`). Pi: `HX-STOP-04`.
- **outcomes_by_mode.** See the turn-end table (`turn_end`): deliberative pauses on any other active state; every auto mode continues; the settlements apply in every mode.
- **conformance_scenarios.** `EHB-STOP-05`, `EHB-STOP-06`, `EHB-STOP-07`, `EHB-STOP-08`, `EHB-STOP-11`, `EHB-STOP-12`, `EHB-STOP-13`, `EHB-STOP-14`, `EHB-STOP-15`, `EHB-STOP-16`, `EHB-STOP-17`, `EHB-STOP-18`, `EHB-STOP-19`, `EHB-STOP-20`, `EHB-STOP-21`, `EHB-STOP-22`, `EHB-STOP-23`
- **sources.** CC "Stop input" and "Stop decision control"; Q2; ADR-0019 (G2), ADR-0051, ADR-0063, ADR-0064 interim (`fb1106b`), ADR-0067 A2-1/A2-2, ADR-0068; HEATH AS3 (`pause`); CODE `P/adapters/claude/completion.py:_turn_end_mode`.
<!-- END GENERATED -->

#### HX-STOP-04 — Pi ends a turn at `agent_before_settle` by the turn-end table, and bounds its own continuations
- **trigger.** A Pi turn ends (`agent_before_settle`, Pi 1.0.0 and later).
- **obligation.** The adapter reads the run through the guarded read path and applies the table: `allow` and `settle` append nothing; a settlement appends its notice; `pause` appends one `custom_message` entry with `display: true` holding the notice and returns no `continue`; `continue` returns `continue: true` with the next obligation appended. It acts at `agent_before_settle` only (never at `turn_end`), so a settle carries one notice. Pi has no loop bound, so the adapter requests a continuation only when the run's observation basis changed since its previous continuation; otherwise it ends with the `unattended_continue` notice and records `no-progress`. Convergence can still be claimed only through `report_convergence`, which is gated at `tool_call`.
- **failure_policy.** `withhold-claim`. An unreadable run ends the turn silently (the gate at `tool_call` still holds).
- **recovery.** The next `report_convergence` evaluates.
- **visibility.** `human-ui` (the appended entry).
- **forbidden_substitutions.** Describing `agent_settled` as a hard completion gate (ADR-32 says it is not); acting at `turn_end` as well (two notices per settle); an unbounded continuation; support for a Pi without `agent_before_settle` (Empirica supports Pi 1.0.0 and later; there is no fallback).
<!-- BEGIN GENERATED: HX-STOP-04 (make registry-doc) -->
- **outcomes.** Pi (1.0.0 and later): `allow` and `settle` append nothing (a settlement appends its notice) | `pause` (a `custom_message` with `display: true`, no `continue`) | `continue` (`continue: true`, the next obligation appended) | `end-no-progress` (the `unattended_continue` notice, `no-progress` recorded). Only `agent_before_settle` acts; `agent_settled` is a notification and `turn_end` is not used, so a settle carries one notice.
- **outcomes_by_mode.** See the turn-end table (`turn_end`). Deliberative pauses; every auto mode continues while the run's observation basis changes between continuations and ends with `no-progress` otherwise.
- **conformance_scenarios.** `EHB-STOP-29`, `EHB-STOP-35`, `EHB-STOP-36`, `EHB-STOP-31`, `EHB-STOP-32`, `EHB-STOP-33`, `EHB-STOP-34`
- **sources.** ADR-32, ADR-68, `dist/core/extensions/types.d.ts:729-779,805` and `docs/extensions.md:66,117` of Pi 1.1.0 (Q3 surface §1), PI-T (`agent_before_settle`).
<!-- END GENERATED -->

#### HX-STOP-05 — SubagentStop is observational and never blocks
- **trigger.** The canonical auditor's `SubagentStop` (Claude).
- **obligation.** Admit only the verdict from the exact bound native execution; exit 0 always; a failure writes one diagnostic line to stderr. The auditor child's own `stop_hook_active` is not used.
- **failure_policy.** `refuse-transition` (the audit fails, the audit stays owed, Stop still fails closed on an open audit).

<!-- BEGIN GENERATED: HX-STOP-05 (make registry-doc) -->
- **outcomes.** Claude: `verdict-admitted` | `audit-failed` (no valid verdict, ambiguous fences, unreadable transcript, served model unobservable or mixed, verdict rejected) | `inert` (not the auditor, no pending child). Pi: not applicable (`HB-AUD-RES`).
- **conformance_scenarios.** `EHB-AUDRES-01`, `EHB-AUDRES-02`, `EHB-AUDRES-03`
- **sources.** D1H §3 items 5–7, ADR-51, `CT/test_activation_lifecycle.py::test_subagent_stop_is_observational_and_never_blocks`, `CT/test_claude_adapter.py::test_subagent_stop_*`.
<!-- END GENERATED -->

### HB-REPORT — `report_convergence`

#### HX-REPORT-01 — a report is the only way to ask for convergence, and it returns the core's decision unchanged
- **trigger.** The author calls `report_convergence` (default intent) or with `intent: stop`.
- **obligation.** Evaluate through the core; deny or `Block` is a closed error with the real reason and the rendered `next:` list; `intent: stop` is always available and ends the run honestly (`stopped_residual`).
- **failure_policy.** `refuse-transition`; `withhold-claim`.
- **recovery.** A lost response is answered by the next report (state is durable).
- **visibility.** `model-context` (the plain-text author view, no digests or receipt ids).
- **forbidden_substitutions.** A hidden JSON field standing for author text (T2 A1-4); a stale cached evaluation.

<!-- BEGIN GENERATED: HX-REPORT-01 (make registry-doc) -->
- **outcomes.** Claude and Pi: `Allow(converged=true)` | `Block` (closed reasons) | `Fault` | `settled-notice` (Pi gate only, for `human_wait`, `evidence_wait`, `budget_exhausted`). Codex: public-only, never trusted ingress.
- **outcomes_by_mode.** Budget recovery: in deliberative mode a raise is proposed for human approval (`budget.raise`); in every auto mode `budget.raise` is excluded (`recovery_exclusions.auto`) and exhaustion needs a fresh run or an honest `intent=stop`. A sole `budget.exhausted` settles the turn in all modes (`budget_exhausted`). `intent=stop` is available in every mode.
- **conformance_scenarios.** `EHB-REPORT-01`, `EHB-REPORT-02`, `EHB-REPORT-03`
- **sources.** ADR-32, ADR-67, ADR-42, `PT/gate.test.ts` ("direct tool: report_convergence execute rejects on Block with active handle", "gate: report_convergence tool is permitted on Allow"), `V2/test_evidence_hosts.py::test_report_convergence_through_the_public_tool_presents_then_returns_the_refreshed_view`.
<!-- END GENERATED -->

#### HX-REPORT-02 — a deliberative step that would end the run without converging asks the human first
- **trigger.** A `report_convergence` (default intent) or `intent: stop` in a **deliberative** run whose result would be `stopped_frozen`, `stopped_residual` or `stopped_budget`.
- **obligation.** Before the transition commits, ask the human through the evidence-confirmation port (ADR-0067): the terminal step is one more decision item of the batch, presented by the same host mechanism (a reservation-bound presentation, one digest-bound receipt). An accepted receipt lets the transition commit on the next evaluation. Cancel, timeout and decline grant nothing: the run stays `active` and the result is the closed reason `governance.terminal_stop_pending`. A host with no dialog leaves the step blocked with that reason (fail closed). Auto modes are unchanged.
- **failure_policy.** `refuse-transition`; a failed presentation is a host failure and also leaves the step blocked.
- **recovery.** The author keeps working or reports again when the human is ready; the human can also tell the agent to continue.
- **visibility.** `human-ui` (the dialog names the status the run would end with and what it defers), `model-context` (the reason).
- **forbidden_substitutions.** Committing a deliberative `stopped_*` without a receipt; treating a cancel, timeout or decline as consent; a terminal state returned as the answer to a declined dialog.
<!-- BEGIN GENERATED: HX-REPORT-02 (make registry-doc) -->
- **outcomes.** Claude and Pi: `Block` with `governance.terminal_stop_pending` while the human has not agreed | the transition commits (`stopped_frozen`, `stopped_residual`, `stopped_budget`) once an accepted, digest-bound receipt covers it. Cancel, timeout and decline grant nothing.
- **outcomes_by_mode.** Deliberative only. `auto-interactive-pre`, `auto-interactive-post` and `auto-delegated` are unchanged: no dialog, the transition commits as before.
- **conformance_scenarios.** `EHB-TERM-01`, `EHB-TERM-02`, `EHB-TERM-03`, `EHB-TERM-04`, `EHB-TERM-05`
- **sources.** ADR-0063 (amendment 2026-10-08), ADR-0067 (the confirmation batch), ADR-0026 (amendment 2026-10-08), Q3 findings F-Q3-3.
<!-- END GENERATED -->

### HB-OBS / HB-PRES / HB-DLG — surface, presentation, dialog

#### HX-OBS-01 — the surface is observed inside each report, and the latest observation counts
- **trigger.** Each `report_convergence` (Claude) / each gated report and at start (Pi).
- **obligation.** Record `human` (the host can show a dialog now) or `unavailable` through the private `human_surface` ingress before evaluating. A failed observation fails closed: show nothing for that call and send `host_failure: surface_unrecorded`.
- **failure_policy.** `report-limitation`: nobody is told a human "will be asked" when the host could not record; `convergence.report` is not offered.
- **recovery.** The next recorded observation clears `surface_unrecorded`.
- **visibility.** `model-context`, `human-ui` (observer).
- **forbidden_substitutions.** Using the previously stored `human` when the new write failed; deriving the route from governance capability instead of the observed form.
<!-- BEGIN GENERATED: HX-OBS-01 (make registry-doc) -->
- **outcomes.** Claude: `human` | `unavailable` (no form, delegated, non-interactive) | `unrecorded` (write failed). Pi: `human` (`ctx.hasUI` with select/input) | `unavailable` (print/json) | `unrecorded`. Initial value Claude `unobserved`.
- **outcomes_by_mode.** The route `human` is possible in deliberative and interactive auto (before and after the first approval); delegated auto, and any non-interactive run, is `unavailable` at once. Control mode itself is not an input of the route.
- **conformance_scenarios.** `EHB-OBS-01`, `EHB-OBS-02`, `EHB-OBS-03`, `EHB-OBS-04`
- **sources.** ADR-67 ("Human surfaces", "A failed observation fails closed"), T4 rows 1–9, 15, A1-2 (T2), PROG "F7", "F8", "A7"; tests `V2/test_evidence_hosts.py::SurfaceFailsClosedTests::*`, `V2/test_host_faithful.py::ThirdState::*`, `::HostFailure::test_a_surface_that_cannot_be_recorded_ends_in_an_honest_stop_within_two_calls`.
<!-- END GENERATED -->

#### HX-PRES-01 — a batch is presented once per derivation, bound to what is shown
- **trigger.** A report on a run with `confirmation_batch` non-null and `prompt_error` null.
- **obligation.** Read the batch, build the items, record a presentation naming the batch key, then show exactly those items (≤64, decisions first), with the complete verbatim quote and the `spike_unavailable` reason. A changed key means re-read once; a second change shows nothing and spends nothing. A presentation spends one unit of the shared interaction budget (128).
- **failure_policy.** `refuse-transition` (nothing recorded unless shown); `withhold-claim` (the record stays pending).
- **recovery.** A key already presented is not presented again until the derivation changes; the response after any admitted presentation is re-evaluated and fresh.
- **visibility.** `human-ui` (dialog), `model-context` (author view "Waiting on a human"), `approval-disclosure` (the quote is the thing approved).
- **forbidden_substitutions.** Recording ids that were not on screen; truncating the quote with an ellipsis; "approve all"; counting a presentation as a decision.
<!-- BEGIN GENERATED: HX-PRES-01 (make registry-doc) -->
- **outcomes.** Claude and Pi: `presented` | `not-presented` with a closed `prompt_error` (`unavailable`, `unobserved`, `already_presented`, `interaction_limit`, `surface_unrecorded`, `presentation_failed`) | `refused-conflict` (stale key, nothing spent).
- **conformance_scenarios.** `EHB-PRES-01`, `EHB-PRES-02`, `EHB-PRES-03`, `EHB-PRES-04`, `EHB-PRES-05`, `EHB-PRES-06`
- **sources.** ADR-67 ("A presentation bounds the batch", "One decision per item", "Nothing is asked twice"), T2 A1-1, A1-3, m2; tests `V2/test_a1_boundaries.py::PresentationBindsWhatWasShown::*`, `::TheHumanSeesTheWholeQuote::*`, `::TheResponseAfterAPresentationIsFresh::*`, `V2/test_evidence_scenarios.py::BatchSizeCells::*`.
<!-- END GENERATED -->

#### HX-DLG-01 — an evidence dialog ends in confirm, reject, or no-decision
- **trigger.** The human answers, dismisses, lets it time out, or the dialog throws.
- **obligation.** `Confirm`/`Approve` and `Reject` (with optional note or answer) are recorded as receipts bound to the presentation; **Later, cancel, timeout, form-level decline, malformed answers and a dialog that raises grant nothing but spend the derivation**, and the fresh response says so (`already_presented`). Nothing is admitted unless every decision is.
- **failure_policy.** `withhold-claim`; `refuse-transition` for a partly invalid receipt.
- **recovery.** A replay of the same receipt is inert; a different receipt for the same record, a reused id, a stale presentation, an unknown or decided item, or a subject listed twice fails closed.
- **visibility.** `human-ui`, `model-context` (the rejection and note, fenced).
- **forbidden_substitutions.** A form-level `decline` recorded as a reject (see section 8.3, C-02); a note-bearing decline accepted as approval; Later as decline.

### HB-INT — Esc / interrupt
<!-- BEGIN GENERATED: HX-DLG-01 (make registry-doc) -->
- **outcomes.** Claude: MCP `accept` (decoded per item) | `decline` | `cancel` | timeout | exception. Pi: select/input return | `undefined` | timeout | exception. Closed result for both: `confirmed` | `rejected` | `no-decision`.
- **conformance_scenarios.** `EHB-DLG-03`, `EHB-DLG-04`, `EHB-DLG-05`, `EHB-DLG-06`, `EHB-DLG-07`
- **sources.** ADR-67 ("One decision per item", "Human receipt"), HEATH AS3; tests `V2/test_evidence_hosts.py::MediatorTests::test_cancel_timeout_decline_and_malformed_answers_grant_nothing`, `::test_later_grants_nothing_and_an_explicit_report_asks_again_within_the_ceiling`, `::test_a_dialog_that_raises_grants_nothing`, `V2/test_evidence_scenarios.py::BatchSizeCells::test_a_decline_or_cancel_that_carries_content_grants_nothing`, `PT/evidence-ui.test.ts`.
<!-- END GENERATED -->

#### HX-INT-01 — an interrupt ends the turn without a gate, and nothing is granted
- **trigger.** The human presses Esc / Ctrl+C or cancels a running tool or dialog.
- **obligation.** An interrupt never converges, approves or confirms anything. A dialog open at the time is `no-decision`. A turn ended by an interrupt may not run the Stop hook at all.
- **failure_policy.** `withhold-claim`.
- **recovery.** Next prompt or report re-evaluates; an orphaned pending audit is reconciled at the next session start (Pi) or by `reconcile_orphans` (Claude).
- **visibility.** `UNSOURCED`: no "interrupted, not converged" notice is defined on either host.
- **forbidden_substitutions.** Esc recorded as decline; an interrupted audit read as a verdict.

### HB-COMPACT — compaction
<!-- BEGIN GENERATED: HX-INT-01 (make registry-doc) -->
- **outcomes.**
  - Claude: turn end by interrupt = **no `Stop` event** (CC); an open MCP elicitation returns `cancel` → `no-decision`; a cancelled running tool does not fire `PostToolUseFailure`. The run stays active, not converged. A `/model` cancelled with Esc did not change settings (Q2 note).
    - Pi: `agent_settled.aborted === true` (Pi) is observable and unused; a cancelled dialog returns `undefined` → `no-decision`; an interrupted `subagent` run returns exit 0 with a non-error "Run paused after interrupt (<agent>)…" result (0.76.1 `subagent-executor.js:3896`) and `finalOutput` "Interrupted. Waiting for explicit next action." (`execution.js:1385`) → no verdict, `audit_failure` (`EHB-INT-03`); with a complete fence it is still a failure (`EHB-INT-04`, see `HX-AUDRES-01`).
    - Codex: not declared.
- **conformance_scenarios.** `EHB-INT-01`, `EHB-INT-02`, `EHB-INT-03`
- **sources.** CC "Stop" and "PostToolUseFailure input" (`is_interrupt`), PI-T changelog (`agent_settled.aborted`), PS 0.76.1 `src/runs/foreground/execution.js:1307-1313,1385`, `subagent-executor.js:3896`, ADR-63 (dismissal, cancellation, timeout), Q2 notes.
<!-- END GENERATED -->

#### HX-COMPACT-01 — compaction never changes what the run knows
- **trigger.** `/compact`, auto compact, or overflow recovery.
- **obligation.** The run state, usage, obligations, audit coverage, presentation receipts, derivation digest and interaction counters survive; the author gets the handle and a bounded run view back.
- **failure_policy.** `report-limitation`: restore is a usability mechanism; the Claude and Pi Stop/report gates re-evaluate from durable state regardless.
- **recovery.** Durable state, not host memory (ADR-33).
- **visibility.** `model-context`.
- **forbidden_substitutions.** Treating a lost restore message as lost state.

### HB-MODEL — model switch
<!-- BEGIN GENERATED: HX-COMPACT-01 (make registry-doc) -->
- **outcomes.**
  - Claude: `SessionStart` matcher `compact` → `RestoreRun` → bounded run view on stdout, exit 0 always; orphaned pending audits are reconciled first (`reconcile_orphans(…, include_pending=False)`). A restore failure writes one diagnostic line and exits 0. `PreCompact` is not used (it could block compaction, which Empirica does not).
  - Pi: `session_before_compact` dispatches `RestoreRun` best-effort and returns a compaction summary carrying the handle; failure is not a gate. `session_compact` and `session_compact_failed` are not handled.
  - Codex: `SessionStart` source `compact` re-evaluates.
- **conformance_scenarios.** `EHB-COMPACT-01`, `EHB-COMPACT-02`
- **sources.** QREF `restore_after_compaction`, ADR-33, T1 §1 ("Restore/compaction must preserve …"), `V2/test_evidence_scenarios.py::RestoreCells::test_everything_the_human_gate_depends_on_survives_a_reload_and_a_restore`, `CT/test_activation_lifecycle.py::test_restore_is_silent_and_exit_zero`, `PT/gate.test.ts` ("session_start reconstructs the handle; compaction carries the handle text").
<!-- END GENERATED -->

#### HX-MODEL-01 — the author identity follows the main model; a switch revokes no approval
- **trigger.** `/model`, the model picker, fast mode, an automatic fallback, a session resume (Claude `PostModelSwitch`, async, cannot block); `model_select` or any `tool_call` (Pi).
- **obligation.** Refresh the read-only governance context (author = the model now serving the author, ingress). Producer attribution is attached to each artifact when it is admitted, not inferred at audit time; a mixed or unobservable producer set, or a same-class reviewer, blocks the audit. A switch revokes no configuration approval.
- **failure_policy.** `refuse-transition` at the next gated call on Pi; `withhold-claim` at the audit (identity unverified).
- **recovery.** The next gated call refreshes again.
- **visibility.** `model-context`; the observer shows the producers.
- **forbidden_substitutions.** Reading identity from the settings alias; trusting the requested reviewer alias as identity.

### HB-AUD-LAUNCH / HB-AUD-RES — the subagent audit
<!-- BEGIN GENERATED: HX-MODEL-01 (make registry-doc) -->
- **outcomes.**
  - Claude: `context-refreshed` (from `to_model`) | `failed-observational` (hook exit 0). Reviewer family is derived at audit launch from the **served** main model, not from the settings key (HX-ADM-03).
  - Pi: `context-refreshed` | `tool_call` blocked "empirica governance context unavailable" when the refresh fails.
- **conformance_scenarios.** `EHB-MODEL-01`, `EHB-MODEL-02`, `EHB-MODEL-03`
- **sources.** ADR-60 ("Identity, evidence, and audit"), QREF `current_author_after_model_switch`, `mixed_producers_block`; `CT/test_activation_lifecycle.py::test_post_model_switch_updates_read_only_context_without_revoking_approval`; CODE `P/adapters/claude/lifecycle.py:274-283`, `P/adapters/pi/src/index.ts:506-508,845-851`.
<!-- END GENERATED -->

#### HX-AUDLAUNCH-01 — the canonical audit is reserved, bound, foreground (Pi) or background (Claude), and inherits nothing from the author
- **trigger.** The author asks for the canonical auditor (`empirica:empirica-auditor` on Claude; `empirica.empirica-auditor` with exactly `{agent, task}` on Pi).
- **obligation.**
  - Reserve exactly one audit child against the audit budget before launch; refuse a second outstanding one; replace the prompt with the immutable dossier; forbid model overrides.
  - **Claude:** `run_in_background: true`, `max_turns: 8`, reviewer alias from a distinct family; `SubagentStart` binds the native `agent_id`.
  - **Pi:** `async: false`, `outputMode` inline, `acceptance: {level: none}`, `agentScope`, `timeoutMs: 900000`, `toolBudget {soft 20, hard 30, block read/grep/find/ls}`, the preflight-vouched model. **S5-R3 (ruled; implemented in G2): the launch carries an explicit `context: "fresh"`** (`audit-launch.ts` `auditLaunchInput`), the preflight is asked with the launch's own context (`index.ts`, read off `preflightLaunch(...)`), and the seam refuses `context-not-fresh` when the launch's or the preflight response's context is anything else (`preflight-seam.ts`); so config `defaultSubagentContext: "fork"` cannot fork the author transcript into the auditor. G1 evidence: `context` is declared by every reviewed version's schema (0.50.0 `schemas.ts:323`, 0.64.0 `schemas.ts:355`, 0.74.0 `schemas.js:215`, 0.75.0 and 0.76.1 `schemas.js:217`), and an omitted `context` is overridden by `defaultSubagentContext` in 0.64.0 … 0.76.1 (0.64.0 `src/shared/fork-context.ts:80-84`; 0.74.0/0.75.0/0.76.1 `src/shared/fork-context.js:14-18`; in 0.50.0 only the agent's own `defaultContext` applies, `subagent-executor.ts:2176-2186`). Before G2 the preflight sent a literal `"fresh"` but the real launch sent none; they now agree by construction. `canonicalAuditorInputError` still forbids the author's own `context` (C-04 resolved: the host adds it, the author may not).
  - **forceTopLevelAsync** (a pi-subagents private config) rewrites `async` to `true` and is invisible to the adapter (`index.ts:189-193`).
- **failure_policy.** `refuse-transition`: a failed admission rejects the reservation and the audit stays owed; any launch failure on an active run denies.
- **recovery.** Reload orphans an unresolved audit (Pi `session_start`/`session_shutdown`; Claude `reconcile_orphans`). A launch rejected while still reserved refunds exactly once; any observed start spends the spawn permanently (D1H §6).
- **visibility.** `model-context` (denial reason, `AUDIT_STILL_OPEN`).
- **forbidden_substitutions.** The author supplying `async`, `model`, `context`, tools or overrides (`canonicalAuditorInputError`); a forked author transcript in the auditor; an async-forced start counted as a foreground audit.
<!-- BEGIN GENERATED: HX-AUDLAUNCH-01 (make registry-doc) -->
- **outcomes.** Claude: `reserved-and-launched` | `denied` (reason). Pi: `reserved-and-launched` | `blocked` (closed code) | `rejected-after-reserve` (`audit_reject`, refunded once). Codex: Stop-owned bounded `codex exec`, identity `unverified`.
- **conformance_scenarios.** `EHB-AUDLAUNCH-01`, `EHB-AUDLAUNCH-02`, `EHB-AUDLAUNCH-03`, `EHB-AUDLAUNCH-04`, `EHB-AUDLAUNCH-05`, `EHB-AUDLAUNCH-06`
- **sources.** ADR-51, ADR-52 (explicit read-only acceptance exemption), ADR-49, D1H §3–4, PROGRESS "S5" (S5-R3, S5-R2), G1 B2/B3, `P/adapters/pi/src/audit-launch.ts:32-42`, `P/adapters/claude/lifecycle.py:327-388`; tests `CT/test_claude_adapter.py::test_auditor_reservation_replaces_author_prompt_and_defers_lifecycle`, `::test_auditor_preflight_failure_does_not_reserve`, `PT/gate.test.ts` ("subagent: canonical auditor is reserved, bound, attributed, and prompt-injected"), `PT/preflight-seam.test.ts` ("the seam refuses forced-async, non-foreground, and non-inline launch requests").
<!-- END GENERATED -->

#### HX-AUDRES-01 — an audit result is a verdict only if it is complete, un-errored, and identity-bound
- **trigger.** The auditor's output arrives (Claude `SubagentStop`; Pi `tool_result` for the correlated `toolCallId`).
- **obligation.** Redact the verdict fence from what the author sees **before** the first await; admit a verdict only from a **completed** run (not errored, and not interrupted, detached, stopped or paused) with exactly one fence and a verdict-bearing final assistant message whose provider and model the host observed; anything else is an audit failure.
- **outcomes (closed).** `verdict-admitted` | `failed` (`isError`, a non-terminal child, no fence, ambiguous or conflicting fences, malformed JSON, unreadable session, served model unobservable or mixed, differs from the admitted verdict) | `orphaned` (reload or shutdown before a result) | `unverified-identity` (verdict kept but identity `pi-child-session-unverified`, so the audit cannot make an independent claim).
  - Pi timed-out child (pi-subagents 0.76.1): `isError: true`, `details.results[0].finalOutput` = `…Partial output before timeout:\n<partial text>`; **an `empirica-verdict` fence inside it is never admitted**: the `isError` branch runs `audit_failure` before any verdict is read. Pinned by `EHB-AUDRES-04`.
  - Pi async-forced start: no per-child result row → `resultText` returns `""` → no verdict → `audit_failure`. Pinned by `EHB-AUDLAUNCH-03`.
  - Pi interrupted, detached or stopped child: exit 0, no `isError`, the row carries `interrupted`/`detached`/`stopped` (resumable, not finished) and may hold a complete fence → `audit_failure`, no identity, no verdict, fence redacted (`childNotTerminal`). Pinned by `EHB-INT-03` (no fence) and `EHB-INT-04` (complete fence, with a positive control).
- **failure_policy.** `refuse-transition` for the audit (`audit_failure state: failed|orphaned`); `withhold-claim` for convergence.
- **recovery.** A failed audit within the audit budget may be retried (seed 2: first audit plus one retry); late results after the first terminal transition are ignored; an identical replay is inert.
- **visibility.** `model-context` (redacted text "[empirica-verdict recorded by host]").
- **forbidden_substitutions.** Parsing a verdict from an errored, partial, async-start or non-terminal (interrupted, detached, stopped, paused) result; identity from configured model metadata or from the requested alias.

### HB-FAIL — host failure
<!-- BEGIN GENERATED: HX-AUDRES-01 (make registry-doc) -->
- **outcomes.**
  `verdict-admitted` | `failed` (`isError`, a non-terminal child, no fence, ambiguous or conflicting fences, malformed JSON, unreadable session, served model unobservable or mixed, differs from the admitted verdict) | `orphaned` (reload or shutdown before a result) | `unverified-identity` (verdict kept but identity `pi-child-session-unverified`, so the audit cannot make an independent claim).
    - Pi timed-out child (pi-subagents 0.76.1): `isError: true`, `details.results[0].finalOutput` = `…Partial output before timeout:\n<partial text>`; **an `empirica-verdict` fence inside it is never admitted**: the `isError` branch runs `audit_failure` before any verdict is read. Pinned by `EHB-AUDRES-04`.
    - Pi async-forced start: no per-child result row → `resultText` returns `""` → no verdict → `audit_failure`. Pinned by `EHB-AUDLAUNCH-03`.
    - Pi interrupted, detached or stopped child: exit 0, no `isError`, the row carries `interrupted`/`detached`/`stopped` (resumable, not finished) and may hold a complete fence → `audit_failure`, no identity, no verdict, fence redacted (`childNotTerminal`). Pinned by `EHB-INT-03` (no fence) and `EHB-INT-04` (complete fence, with a positive control).
- **conformance_scenarios.** `EHB-AUDRES-01`, `EHB-AUDRES-02`, `EHB-AUDRES-03`, `EHB-AUDRES-04`, `EHB-AUDRES-05`, `EHB-AUDRES-06`, `EHB-INT-04`, `EHB-AUDRES-07`, `EHB-AUDRES-08`
- **sources.** D1H §3 items 5–7, §4 items 3–5, §6; ADR-60; `P/adapters/pi/src/audit.ts`, `audit-identity.ts`, `index.ts:766-830`; PS 0.76.1 `src/runs/foreground/execution.js:1130-1137,1405-1406,1480-1512`, `subagent-executor.js:3900-3904`; tests `PT/gate.test.ts` ("tool_result redacts before privately admitting the correlated verdict", "errored auditor output is redacted and cannot admit a fenced verdict", "malformed auditor result returns a propagated redacted replacement", "missing native session keeps auditor identity unverified"), `PT/audit-identity.test.ts`.
<!-- END GENERATED -->

#### HX-FAIL-01 — a mechanism that fails says so, once, and never falls back silently
- **trigger.** The surface cannot be recorded, the presentation cannot be made, or both; the evaluation that reports the failure itself raises or faults.
- **obligation.** Send the typed fact with the evaluation (`host_failure`); the core words it as the batch's `prompt_error`; `convergence.report` is withheld and the first `next:` is stop. What the core already knows (`unavailable`, `already_presented`, `interaction_limit`) outranks the host's report. If the evaluation that carries the failure itself fails, apply the failure in process through the core's projection (`project_host_failure`). A literal agent that follows `next:` ends in at most two calls.
- **failure_policy.** `report-limitation` + `withhold-claim`. Never a pause as an evidence wait.
- **recovery.** The next recorded observation clears `surface_unrecorded`; a presentation failure leaves the derivation unspent where nothing was recorded.
- **visibility.** `model-context` (registry labels `waiting_surface_unrecorded`, `waiting_presentation_failed`, no host-supplied text), `human-ui` (observer).
- **forbidden_substitutions.** A stale answerable view (offering a report that would ask nobody); a silent retry; the old "Report again" notice, which loops.

### HB-SESSION — resume / reload / shutdown
<!-- BEGIN GENERATED: HX-FAIL-01 (make registry-doc) -->
- **outcomes.** Claude and Pi: `surface_unrecorded` | `presentation_failed` | `both` | `closed-tool-error` (the observation failed **and** its only evaluation failed: no `next:`, nothing promised; an explicit stop still succeeds).
- **conformance_scenarios.** `EHB-FAIL-01`, `EHB-FAIL-02`, `EHB-FAIL-03`, `EHB-FAIL-04`, `EHB-FAIL-05`, `EHB-FAIL-06`, `EHB-FAIL-07`
- **sources.** ADR-67 ("A host that fails to ask says so, on the same call"), F8, A7, A8, T4 rows 14–17, HEATH AS7; tests `V2/test_host_faithful.py::HostFailure::*`, `PT/gate.test.ts` ("gate: a host failure shows nobody a dialog, says so with the stop next, and a literal agent ends in two calls", "gate: a failed presentation is never reported as a wait the human was asked about").
<!-- END GENERATED -->

#### HX-SESSION-01 — a restored session keeps the run and orphans what cannot continue
- **trigger.** `SessionStart` (startup, resume, clear, compact, fork) on Claude; `session_start` / `session_shutdown` on Pi.
- **obligation.** Rebuild the opaque handle from durable state; mark every foreground correlation that cannot live across the boundary `orphaned` (children and audits), retaining the durable entry if the acknowledgement fails; keep a terminal run inactive but readable; re-observe the pi-subagents owner.
- **failure_policy.** `report-limitation`; gates re-evaluate from durable state.
- **recovery.** Durable correlation entries are reconciled at the next start.
- **visibility.** `model-context`.

### HB-VIEW — observer read
<!-- BEGIN GENERATED: HX-SESSION-01 (make registry-doc) -->
- **outcomes.** Claude: restore on `compact` only (other sources: not wired: `UNSOURCED`). Pi: `restored-active` | `restored-terminal` (retired, readable) | `orphaned`.
- **conformance_scenarios.** `EHB-SESSION-01`, `EHB-SESSION-02`, `EHB-SESSION-03`
- **sources.** D1H §4 item 6 and §6, ADR-33, `PT/gate.test.ts` ("session restore orphans unresolved audits and tombstones completed correlations", "session shutdown orphans a newly admitted unresolved audit", "session restore keeps a verified terminal run inactive"), `P/hooks/hooks.json` (`SessionStart` matcher `compact` only).
<!-- END GENERATED -->

#### HX-VIEW-01 — the observer shows the plugin's judgement and never derives it
- **trigger.** The operator opens the observer UI on the run store.
- **obligation.** Every value comes from the plugin's own derivations; reading changes nothing; a run it cannot fully read appears with a diagnostic; at every qualification checkpoint the UI agrees semantically with the RunView.
- **failure_policy.** `report-limitation`.
- **visibility.** `human-ui`.
- **forbidden_substitutions.** A second derivation in the UI (ADR-65 "One owner per fact").
<!-- BEGIN GENERATED: HX-VIEW-01 (make registry-doc) -->
- **outcomes.** `agrees` | `disagrees` (a FAIL) | `partial-read` (diagnostic shown).
- **conformance_scenarios.** `EHB-VIEW-01`
- **sources.** ADR-65, ADR-67 ("Observer UI"), QREF `observer_ui_live`.
<!-- END GENERATED -->

## 5. Earlier invariants the turn-end table keeps or supersedes

`HX-STOP-03` is the turn-end table (ADR-0068). This table says, for each earlier invariant, what it does to it. The **Keeps** rows hold for the cells they own and are proved by the scenarios named in `turn_end`; the rows marked **Changes** name the test whose deliberative cells phase B rewrites.

| Earlier invariant | Statement and source | `HX-STOP-03` (as implemented) | Why |
|---|---|---|---|
| **F5 / A2-1, A2-2** | Remove the surface note; the Claude Stop never settles as `evidence_wait`. A Stop that waited on a batch nobody had been shown would end the turn with nobody asked, because in Claude the human can only be asked *inside* `report_convergence`. (F5-fixes.md lines 11-13; ADR-67) | **Keeps** the reason. **Supersedes** the sentence "the Claude Stop never pauses". | A pause is never an evidence wait and never promises that a human "will be asked". |
| **F7 Stop directive** | When the only blocker is a human confirmation (route `human` or `unobserved`), block with `evidence_report` (`public-contract.json settlement_notices.evidence_report`: the human is asked inside the call, if this host can show a dialog). (F7) | **Keeps** in interactive auto. **Changes** in deliberative mode: the turn ends with `paused_for_evidence`, which tells the human to tell the agent to report. | The directive is an instruction to the agent; a deliberative agent is not instructed past its checkpoint (F-Q3-1). |
| **F8 "the report leads to stop"** | After the directed report, if nobody could be asked the response leads to `stop` within two calls. (F8; `V2/test_host_faithful.py::HostFailure::test_the_stop_directive_may_send_one_report_and_that_report_leads_to_stop`) | **Keeps.** | A pause does not replace the honest stop; it ends the *hook loop*, not the run. |
| **A1 `ClaudeStopEvidenceByControlMode`** | Class at `V2/test_a1_boundaries.py`: in every surface cell the Stop blocks with the directive and the mediator shows only when recorded. | **Changes** for the deliberative cells (phase B rewrites them to the `paused_for_evidence` pause); its interactive-auto cells are unchanged. | The class asserted the directive in every control mode; deliberative mode now pauses. |
| **A2 / F5 text in docs** | "The Stop hook never pauses for evidence" (`QREF claude.md:6-7`, ADR-67, `P/skills/empirica/references`, ledger rows `ev_*`). | **Supersedes the wording**: "a turn end never settles as an evidence wait". | The ledger rows for `ev_url_confirm` and `ev_missing_or_stale_spike` assert a block in an auto run; they stay true there. |
| **ADR-63 `human_wait` and `budget_exhausted`** | Exit 0 + contract `systemMessage` for the sole legitimate initial-approval blocker and for sole budget exhaustion. | **Keeps; extends by analogy.** | These are the existing precedents for a nonterminal pause; the new outcome reuses their shape (exit 0, contract notice, "not converged"). |
| **ADR-51 async-audit settlement** | Exit 0 only for exactly one `audit.pending` over one concrete pending audit child. | **Keeps.** | Unchanged. |
| **F3 m11 / ADR-67** | A failed surface observation fails closed (counts as `unavailable`). | **Keeps.** | The pause says nothing about the surface. |
| **ADR-32 / D1H** | The only convergence claim is `Allow(converged=true)` through `EvaluateRun(report_convergence)`. | **Keeps.** | A pause is exit 0 without a convergence claim, as the two settlements above. |

## 6. Assurance statements (AS7): what is delivered, observed, enforced

Heath AS7 requires each mechanism to say what it does *not* do. Per host, for the boundaries above.

**Claude (hooks `UserPromptExpansion`, `PreToolUse`, `PostToolUseFailure`, `SubagentStart/Stop`, `Stop`, `SessionStart:compact`, `PostModelSwitch`; MCP elicitation).**
- *Enforced before the effect:* tool calls (`PreToolUse` exit 2), the Stop turn end (exit 2, subject to Claude's 8-continuation cap), activation expansion (block), audit launch reservation.
- *Observed after the effect, not enforceable:* `PostModelSwitch` (async, cannot block), `PostToolUseFailure`, `SubagentStop` (verdict admission only; the child has already finished), `SessionStart:compact`.
- *Not observed:* a user interrupt (`Stop` does not run), the human's dismissal of a native Claude UI that is not an MCP elicitation, `PreCompact`/`PostCompact`/`SessionEnd` (not wired), the MCP client's form-elicitation capability at run start (only inside `report_convergence`), the model that actually served a child unless it appears in the transcript.
- *Not delivered:* a message from a hook that Claude Code overrides (the 8-cap), and a `systemMessage` whose rendering is not verified (G-03, section 8.1).

**Pi (extension events `session_start`, `model_select`, `tool_call`, `tool_result`, `session_before_compact`, `session_shutdown`; `ctx.ui`).**
- *Enforced before the effect:* tool calls (`{block:true}`), the report tool, audit launch (`tool_call` input rewrite).
- *Observed:* `tool_result` (after the effect), `model_select`.
- *Not observed or not enforced:* turn end (no gate; `agent_settled` observational), interrupt (`agent_settled.aborted` available, unused), a compaction failure (`session_compact_failed` not handled), a `forceTopLevelAsync` rewrite at the owner (invisible to the adapter), a launch the owner later changes.
- *Not delivered:* `ctx.ui` prompts in print/json mode (refused, never prompted); an RPC `custom()` that resolves nothing (a dismissal).

**Codex CLI 0.146.0 (`observational`, `wip_unsupported`).** Not declared beyond: the Stop hook permits only `Allow(converged=true)`; no human ingress; audit identity unverified, so convergence blocks. No boundary above is claimed supported.

## 7. Acceptance matrix

Each row is falsifiable: it names the setup, the action, and the expected observable result per host. The tests that cover a row and the qualification-ledger rows that settle it live in `contracts/empirica/v2/host-boundaries.json` (`covered_by`, `live_only`, `ledger_rows`), which owns them and which `make contract-check` verifies. Hosts: **C** Claude, **P** Pi, **X** Codex (not declared unless stated).

<!-- BEGIN GENERATED: scenarios (make registry-doc) -->
| Id | Modes | Setup | Action | Expected (C / P / X) | Proof |
|---|---|---|---|---|---|
| `EHB-ACT-01` | any | Claude/Pi, no run | `/empirica <goal>` typed by the human | C: `UserPromptExpansion` starts a run, injects the handle, goal verbatim. P: `/empirica` starts, persists the handle. X: best-effort | 2 tests; ledger `activation_verbatim_goal` |
| `EHB-ACT-02` | any | none | invoke with an empty or whitespace goal, or an unknown flag | C: block "Empirica did not start", no run. P: notify refusal, no run | 4 tests; ledger `empty_goal_refusal` |
| `EHB-ACT-03` | any | non-interactive surface (`sdk-cli`, Pi print/json), no delegation env | invoke `--auto` | C/P: structural refusal; no run | 2 tests; ledger `noninteractive_auto_refusal` |
| `EHB-ACT-04` | any | Claude interactive run just started | read the recorded `human_surface` before any report | C: `unobserved`. P: `human` iff `ctx.hasUI`, else `unavailable` | 2 tests; ledger `settings_env_and_entrypoint` |
| `EHB-ACT-05` | auto-delegated | delegated / non-interactive / Codex | start | `human_surface = unavailable` at once; route `unavailable` | 2 tests; ledger `ev_route_unavailable` |
| `EHB-ADM-01` | any | Claude at 2.1.277 and 2.2.0 | capture a receipt | outside `>=2.1.278,<2.2.0` is refused **by the receipt verifier**; no run-start refusal (`report-limitation`) | 2 tests |
| `EHB-ADM-02` | any | Pi at 0.99.9, 1.0.0, 1.0.x, 1.1.0 (the profile's qualified version), 1.9.9, 2.0.0 (`>=1.0.0,<2.0.0`); Claude below its qualified version | capture a receipt | a receipt from the qualified version or later inside the interval (1.1.0, 1.9.9) is accepted; 0.99.9 is outside the interval and is refused; 1.0.0…1.0.x are inside the interval but below the qualified version and are refused ("a native receipt from pi >= 1.1.0 is required"); 2.0.0 is refused as outside the interval; Claude below its qualified version is refused the same way ("a native receipt from claude >= <qualified> is required"); the qualified receipt is Pi 1.1.0 (profile id `pi+pi-subagents-foreground-audit-v1`) | 5 tests |
| `EHB-ADM-03` | any | Pi with pi-subagents outside `subagents_compatibility` `[minimum, maximum_exclusive)` (`1.0.0`, `9.9.9`, `0.49.9`) | session start, `/empirica`, `configure_run` | P: `host.subagents_version_unsupported` block; no run, no reservation | 3 tests |
| `EHB-ADM-04` | any | Pi, two owners / owner absent / tool inactive / owner changed after the snapshot | `/empirica`, then audit | P: closed `host.subagents_*` reason; audit refused before reservation | 4 tests |
| `EHB-ADM-05` | any | each reviewed pi-subagents version | run the matrix (`make empirica-subagents-matrix`) | five checks green per version (inventory, schema, preflight, classifier, retrieval) | 1 test |
| `EHB-ADM-06` | any | Claude main model Opus 4.8, no `CLAUDE_CODE_SUBAGENT_MODEL` | request the audit | reviewer alias `sonnet` (Sonnet 5.5); main family unobservable → denial with pin guidance | 3 tests; ledger `served_reviewer_and_producers_distinct` |
| `EHB-ADM-07` | any | Pi with pi-subagents inside the range but with no compat record (e.g. `0.99.0`), whose parameter schema declares every audit-launch field | session start, `/empirica`, `subagent` actions during the run, the run view | P: admitted; the recorded runtime has `reviewed: false`; the run view shows `host: pi-subagents 0.99.0 (not reviewed; preflight passed)`; a `subagent` action is allowed during a run only if some reviewed version has its name and the classifier calls it read-only management (`list`, `status`, `models`, `children.list`, `doctor`); every other name is refused with `host.subagents_launch_unsupported`; a schema missing an audit-launch field is refused (`host.subagents_version_unsupported`) | 5 tests |
| `EHB-ADM-08` | any | Pi, any pi-subagents version (reviewed or observed), whose launch preflight reports an effective allowlist with a tool outside the profile's `audit_launch_policy.audit_child_tools` (`edit`, `write`, `bash`, `subagent`) | `/empirica` start, and the audit launch | P: the seam refuses `tools-not-read-only` ("pi-subagents preflight refused (tools-not-read-only)" followed by the contract message of `host.subagents_tools_not_read_only`) at start and at launch; the five-tool allowlist of 0.74.0, 0.75.0 and 0.76.1 (`read`, `grep`, `find`, `ls`, `contact_supervisor`) is admitted | 3 tests |
| `EHB-GOV-01` | any | approval pending | attempt a benign write | C: `PreToolUse` exit 2 `governance.approval_required`. P: `{block:true}` | 2 tests; ledger `sentinel_blocked_before_approval` |
| `EHB-GOV-02` | any | route recorded, no investigation | research/spike/child/report | `route.required`, then `investigation.required`; no artifact written | 2 tests; ledger `configuration_pending` |
| `EHB-GOV-03` | deliberative, auto-interactive-pre | configuration form shown | human edits 8→6 and confirms | one final locked confirmation; approved ceilings 6 | 1 test; ledger `locked_approval_at_6` |
| `EHB-GOV-04` | deliberative, auto-interactive-pre | configuration form shown | cancel, timeout, malformed, wrong id | `no-decision`: dismissal durable, no budget installed | 3 tests; ledger `cancellation_and_timeout_nonterminal` |
| `EHB-TOOL-01` | any | active run | an unknown MCP research tool or writer | denied until admitted; public preparation tools exempt | 1 test |
| `EHB-TOOL-02` | any | active run, resolution unavailable | any investigative tool | C: exit 2. P: `{block:true}` | 1 test |
| `EHB-TOOL-03` | any | a subagent calls a tool | the subagent's `PreToolUse` | cannot admit main-author evidence | 1 test |
| `EHB-TOOL-04` | any | Pi 1.0.0, 1.1.0 | a failed tool result, or a result carrying `structuredContent` | judged by `isError` only; `tool_result.error` is not declared in any in-range version; `structuredContent` (declared by every supported Pi) is dropped with the redaction of `content`, so a fence in it never survives | 3 tests |
| `EHB-STOP-01` | auto-interactive-pre, auto-interactive-post, auto-delegated | an `auto` run active, not converged | Stop | C: exit 2 with real reason; no convergence claim | 1 test; ledger `snapshot_completion` |
| `EHB-STOP-02` | auto-interactive-pre, auto-interactive-post | interactive `auto` run, sole blocker = evidence confirmation, route `human`/`unobserved` | Stop | C: exit 2 with the `evidence_report` directive on the first and on a later Stop; never `evidence_wait` | 2 tests; ledger `ev_url_confirm` |
| `EHB-STOP-03` | deliberative, auto-interactive-pre | sole blocker = initial human approval | Stop | C: exit 0 `human_wait` ("paused for human governance input, not converged") | 2 tests; ledger `configuration_pending` |
| `EHB-STOP-04` | deliberative, auto-interactive-pre, auto-interactive-post, auto-delegated | sole blocker = budget exhausted | Stop | C: exit 0 `budget_exhausted` | 1 test |
| `EHB-STOP-05` | auto-interactive-pre, auto-interactive-post, auto-delegated | an `auto` run, spike missing/stale plus a URL | Stop | C: exit 2 with the spike reason; no pause | 1 test; ledger `ev_missing_or_stale_spike` |
| `EHB-STOP-06` | auto-interactive-pre, auto-interactive-post, auto-delegated | an `auto` run: a non-converged Block, an evidence directive (interactive modes only: a delegated run's route is `unavailable`), a sole `budget.exhausted`, one pending async audit, a sole initial approval | the first Stop (`stop_hook_active` false or absent) | C: real reason exit 2; `evidence_report` exit 2; `budget_exhausted` exit 0; async-audit view exit 0; `human_wait` exit 0 (the reason text is the one sentence of `_reason_text`) | 2 tests; ledger `ev_url_confirm` |
| `EHB-STOP-07` | deliberative | deliberative run, active, non-converged, the previous Stop already blocked | Stop with `stop_hook_active: true` | C: exit 0, `paused_for_human`: "paused for you, not converged: <real reason> Nothing was approved or confirmed by this pause, and nobody was asked." Never "will be asked"; the run stays `active` | 2 tests; ledger `stop_pause_rendering` |
| `EHB-STOP-08` | auto-interactive-pre, auto-interactive-post, auto-delegated | any `auto` run (interactive before or after its first approval, delegated), active, non-converged, with a blocker other than a sole evidence confirmation (`EHB-STOP-20` takes precedence for that case), the previous Stop already blocked | Stop with `stop_hook_active: true` | C: exit 2, `unattended_continue`: the real reason and "Auto mode does not hand the turn back to you: act on it, or end honestly with report_convergence intent=stop"; never a human pause | 2 tests; ledger `stop_unattended_directive` |
| `EHB-STOP-09` | auto-interactive-pre, auto-interactive-post, auto-delegated | Claude overrides Empirica after eight consecutive blocks | an operator checkpoint where the hook keeps blocking | live-only: **Q2** showed nine blocks before the override, in a deliberative run. A deliberative run now pauses on its first Stop, so only an `auto` run can be blocked repeatedly; the cap is a host limit for it and never the plan | live-only `stop_host_cap`; ledger `stop_host_cap` |
| `EHB-STOP-11` | auto-interactive-pre | interactive auto before its first approval | the first Stop and a continuation | C: `human_wait` settles an approvable first approval on either Stop; otherwise an auto first turn end gives the core's reason and a continuation takes the auto outcome (`unattended_continue`, or the evidence directive) | 1 test; ledger `configuration_pending` |
| `EHB-STOP-12` | deliberative, auto-interactive-pre, auto-interactive-post, auto-delegated | every mode; `budget.exhausted`, one pending async audit, `Allow`, `Inert`, `Fault` | Stop with `stop_hook_active: true` | C: unchanged: `budget_exhausted` and the async-audit view settle exit 0, `Fault` closed exit 2, `Allow`/`Inert` exit 0 | 1 test |
| `EHB-STOP-13` | deliberative | a deliberative Block whose run view has no governance, or whose run is not active, or two reasons | Stop with `stop_hook_active: true` | C: no run view or no active run blocks (exit 2); two reasons are all named in the hand-off | 2 tests |
| `EHB-STOP-14` | deliberative, auto-delegated | the same Block repeated | Stop with `stop_hook_active: true`, repeatedly | C: the same result every time; no counter, no note, no state | 1 test |
| `EHB-STOP-15` | any | the contract | read `settlement_notices` | `paused_for_human` and `unattended_continue` come from the contract and each carries exactly one `{reason}`; "will be asked" appears in neither | 1 test |
| `EHB-STOP-16` | deliberative | deliberative Claude run with a literal agent that ends its turn to wait | the Stop hook loop | C: the loop ends in at most two Stops with "paused … not converged … nobody was asked" | 3 tests; ledger `stop_pause_rendering` |
| `EHB-STOP-17` | auto-interactive-post, auto-delegated | interactive auto after the first approval, and delegated auto, with a literal agent | the Stop hook loop | C: Stops `[2, 2, 0]`; the continuation names `report_convergence intent=stop`; the run ends `stopped_*` | 1 test; ledger `stop_unattended_directive` |
| `EHB-STOP-18` | any | the Stop payload | `stop_hook_active` is `true`, absent, or a non-boolean (`"true"`, `1`, `null`) | C: only the literal `true` is a continuation; the hook passes it to the gate | 2 tests; ledger `stop_flag_reset` |
| `EHB-REPORT-01` | any | evidence satisfied and audit passed | `report_convergence` | `Allow(converged=true)`, terminal state durable | 1 test; ledger `snapshot_completion` |
| `EHB-REPORT-02` | any | open blocker | `report_convergence` | closed `Block` with the real reason and `next:` | 1 test |
| `EHB-REPORT-03` | deliberative, auto-interactive-pre, auto-interactive-post, auto-delegated | any state | `report_convergence` with `intent: stop` | always succeeds; honest stop | 1 test |
| `EHB-TERM-01` | deliberative | a deliberative run whose `report_convergence` or `stop` would end `stopped_frozen`, `stopped_residual` or `stopped_budget`; the host can show a dialog (Claude, Pi) | `report_convergence` or `intent=stop`, then the human accepts | the human is asked inside the call through the evidence-confirmation presentation port; on accept the transition commits and the run is terminal with that status | 4 tests |
| `EHB-TERM-02` | deliberative | the same, and the human declines, cancels or lets the dialog time out | the same call | nothing is granted; the run stays `active` and the result is the closed reason `governance.terminal_stop_pending` with the one text for its case, never a terminal state; the next explicit stop asks again, within the interaction ceiling | 5 tests |
| `EHB-TERM-03` | deliberative | the same, on a host with no dialog (a route `unavailable`) | the same call | the step stays blocked with `governance.terminal_stop_pending` (fail closed); the run stays `active` | 2 tests |
| `EHB-TERM-04` | auto-interactive-pre, auto-interactive-post, auto-delegated | each `auto` mode, a step that would end `stopped_*` | `report_convergence` or `intent=stop` | unchanged: no dialog, the transition commits as before | 1 test; ledger `auto_terminal_no_consent` |
| `EHB-TERM-05` | deliberative | Claude and Pi, a deliberative run at the terminal step | the human answers the dialog (accept, then a run with decline, cancel, timeout) | as `EHB-TERM-01` and `EHB-TERM-02` on the real host | live-only `terminal_stop_consent`; ledger `terminal_stop_consent` |
| `EHB-OBS-01` | any | Claude interactive, MCP form capability declared | first report | `human` recorded before evaluation | 1 test |
| `EHB-OBS-02` | any | surface write fails | report | `surface_unrecorded`; nothing shown; first `next:` is stop | 1 test |
| `EHB-OBS-03` | any | a human surface existed, then goes away | next report | route follows the latest observation | 1 test |
| `EHB-OBS-04` | auto-delegated | Pi print/json | start and report | `unavailable`, nothing prompted | 2 tests; ledger `ev_route_unavailable` |
| `EHB-PRES-01` | any | URL evidence | report | one batch, one dialog, items ≤64, decisions first | 2 tests; ledger `ev_url_confirm`, `ev_over_64_items` |
| `EHB-PRES-02` | any | batch changes between read and show | report | key mismatch: one re-read; a second change shows and spends nothing | 1 test |
| `EHB-PRES-03` | any | long verbatim quote | present | the whole quote is shown | 1 test; ledger `ev_long_quote` |
| `EHB-PRES-04` | any | already presented and left on Later, cancel or timeout, derivation unchanged | report | the view says `already_presented` and nothing asks again by itself; an explicit report asks again, one interaction each, until `interaction_limit` | 2 tests; ledger `ev_later_then_fresh_get_run` |
| `EHB-PRES-05` | any | 128 interactions spent | report | `interaction_limit`; stop honestly | 1 test |
| `EHB-PRES-06` | any | presentation made stale during the dialog | answer | refused, nothing granted | 1 test |
| `EHB-DLG-01` | any | configuration dialog | explicit Reject | `declined` recorded; nothing approved | 1 test |
| `EHB-DLG-02` | any | configuration dialog | Esc / cancel / timeout / reload | `no-decision`; never `declined` | 1 test; live-only `dialog_reload`; ledger `dialog_reload` |
| `EHB-DLG-03` | any | evidence batch | Confirm | receipt bound to presentation; claim satisfied | 1 test; ledger `ev_url_confirm` |
| `EHB-DLG-04` | any | evidence batch | Reject with a note | obligation row with fenced note; partial evidence kept | 1 test; ledger `ev_reject_with_note` |
| `EHB-DLG-05` | any | evidence batch | Later | `no-decision`, derivation spent | 1 test; ledger `ev_later_cancel_timeout` |
| `EHB-DLG-06` | any | evidence batch | cancel / timeout / MCP `decline` / malformed / dialog raises | `no-decision`, nothing granted. A form-level `decline` is `no-decision` by ruling (live: `form_level_decline`): the form says "Decline/Esc = send none" and the explicit per-item decision is Reject | 3 tests; live-only `form_level_decline`; ledger `ev_later_cancel_timeout` |
| `EHB-DLG-07` | any | decision-claim (`needs-decision`) | approve with answer; reject | answer kept at most 2000 chars; rejected decision keeps its answer, discards the claim | 2 tests; ledger `ev_needs_decision_only` |
| `EHB-INT-01` | any | Claude, a run active, human presses Esc mid-turn | — | no `Stop` hook event; run unchanged, not converged | live-only `host_interrupt`; ledger `host_interrupt` |
| `EHB-INT-02` | any | Claude, MCP elicitation open | Esc | `cancel` → `no-decision` | 2 tests; live-only `interrupt_elicitation`; ledger `interrupt_elicitation` |
| `EHB-INT-03` | any | Pi audit running | interrupt | non-error "Run paused after interrupt" (exit 0, no fence); no verdict → `audit_failure` | 1 test; live-only `host_interrupt`; ledger `host_interrupt` |
| `EHB-INT-04` | any | Pi audit child interrupted, detached or stopped (resumable) whose `finalOutput` carries one complete `empirica-verdict` fence | result | exit 0, no `isError`: still `audit_failure`, no identity, no verdict; the fence is redacted. A verdict is admitted only from a completed run (G2 ruling 2) | 2 tests |
| `EHB-COMPACT-01` | any | after current passing audit | `/compact` | restored run has the same status/obligations/usage/audit; handle given back | 3 tests; ledger `restore_after_compaction` |
| `EHB-COMPACT-02` | any | Pi compaction fails or is cancelled | `session_compact_failed` | run still gated from durable state (the adapter does not handle the event; the gate reads durable state at every `tool_call`) | live-only `pi_compaction_failure`; ledger `pi_compaction_failure` |
| `EHB-MODEL-01` | any | approved run | switch main A→B→A with a recording turn between | every artifact keeps its producer; approval stays; context updated | 2 tests; ledger `current_author_after_model_switch` |
| `EHB-MODEL-02` | any | audit launch after a switch | request audit | reviewer follows the *served* main model | 2 tests; ledger `served_reviewer_and_producers_distinct` |
| `EHB-MODEL-04` | any | Bedrock, main Opus 4.8 [1m], Opus/Sonnet/Haiku pinned | request the audit | reviewer `sonnet` | 1 test; ledger `served_reviewer_and_producers_distinct` |
| `EHB-MODEL-05` | any | same, Sonnet pin absent | request the audit | reviewer `haiku` (the next distinct family with a pin) | 1 test; ledger `served_reviewer_and_producers_distinct` |
| `EHB-MODEL-06` | any | same pins, main Sonnet 5.5 | request the audit | reviewer `opus` | 1 test; ledger `served_reviewer_and_producers_distinct` |
| `EHB-MODEL-03` | any | two models produced evidence | audit and report | audit may launch; convergence blocked (mixed producers) | 1 test; ledger `mixed_producers_block` |
| `EHB-AUDLAUNCH-01` | any | active run | auditor with author-supplied extras (`model`, `async`, `context`) | refused by `canonicalAuditorInputError`; no reservation | 1 test |
| `EHB-AUDLAUNCH-02` | any | Pi operator config `defaultSubagentContext: "fork"` | canonical audit | launch carries `context:"fresh"`; the preflight is asked the same context; the seam refuses `context-not-fresh` for a launch or a preflight response that is not `fresh`; the author still cannot send `context` | 4 tests |
| `EHB-AUDLAUNCH-03` | any | pi-subagents `forceTopLevelAsync` true | canonical audit | no valid foreground verdict; `audit_failure`, audit stays owed | 2 tests |
| `EHB-AUDLAUNCH-04` | any | a second audit while one is outstanding | request | denied `AUDIT_STILL_OPEN` | 2 tests |
| `EHB-AUDLAUNCH-05` | any | later launch step throws | audit | reservation rejected, spawn refunded once | 2 tests |
| `EHB-AUDLAUNCH-06` | any | auditor would run on author's model | `/empirica` / audit | blocked; same-class reviewer refused | 2 tests; ledger `reviewer_preflight_refusal` |
| `EHB-AUDRES-01` | any | exact native execution, one fence | result | verdict admitted | 2 tests; ledger `served_reviewer_and_producers_distinct` |
| `EHB-AUDRES-02` | any | two conflicting fences / none / unreadable transcript | result | audit failed | 3 tests |
| `EHB-AUDRES-03` | any | native session missing / served model unobservable | result | identity unverified; no independent claim | 1 test |
| `EHB-AUDRES-04` | any | Pi timed-out child with a fence in `details.results[0].finalOutput` | result | `isError` → `audit_failure`; fence redacted and never admitted; `isError` is the only failure signal | 3 tests |
| `EHB-AUDRES-05` | any | reload/shutdown before result | restart | audit `orphaned`; retryable | 2 tests |
| `EHB-AUDRES-06` | any | audit fails after approval | — | the human's answer stands; reaudit without asking again | 1 test; ledger `ev_audit_fails_after_approval` |
| `EHB-AUDRES-07` | any | Claude, an audit in which the auditor fetches one same-host URL and one cross-host redirecting URL with `WebFetch` (ADR-0069 G5-R3 premise) | keep the auditor's `agent_transcript_path` file and read its `WebFetch` tool_use and tool_result | L: evidence only: record the exact tool_use/tool_result shape (error flag, content, whether the URL and the redirect target appear); nothing in Empirica credits a fetch from the auditor's transcript yet, so a web record is still confirmed by audit only when start-time `audit_retrieval` contains `web` (ADR-0067) | live-only `webfetch_capture`; ledger `webfetch_capture` |
| `EHB-AUDRES-08` | any | Pi foreground audit in which the auditor calls `contact_supervisor` with `need_decision` (ADR-0069 G6-R2) | let the audit run to its end or to the audit timeout | L: record whether the call blocks until the audit timeout or the child detaches; no verdict is admitted either way (a timeout is `isError`; a detached child is non-terminal, `EHB-INT-04`) | live-only `auditor_contact_supervisor`; ledger `auditor_contact_supervisor` |
| `EHB-FAIL-01` | any | surface cannot be recorded | report | stop within two calls; nothing offered | 1 test |
| `EHB-FAIL-02` | any | presentation fails | report | `presentation_failed`; not worded as a wait the human answered | 2 tests |
| `EHB-FAIL-03` | any | both fail | report | stop in two calls | 1 test |
| `EHB-FAIL-04` | any | the failure report itself faults | report | closed tool error, no `next:`; explicit stop succeeds | 2 tests |
| `EHB-FAIL-05` | any | what the core knows outranks the host | report | `unavailable`/`already_presented`/`interaction_limit` win | 1 test |
| `EHB-FAIL-06` | any | a live double fault on a native host | — | as `EHB-FAIL-03` | live-only `host_double_fault`; ledger `host_double_fault` |
| `EHB-SESSION-01` | any | Pi session restored with an active run | `session_start` | handle reconstructed; run still gated | 1 test |
| `EHB-SESSION-02` | any | terminal run | `session_start` | inactive but readable | 1 test |
| `EHB-SESSION-03` | any | Claude resume/clear/fork | `SessionStart` (not `compact`) | a recorded limitation: no restore is wired; the Stop and the tool gate still hold, the author gets no handle back | live-only `claude_resume_clear_fork`; ledger `claude_resume_clear_fork` |
| `EHB-VIEW-01` | any | any run | open the observer at each checkpoint | agrees with RunView | 1 test; ledger `observer_ui_live` |
| `EHB-STOP-19` | auto-interactive-pre | a first approval that cannot be asked here (no MCP ingress, interactions spent, or a prompt error), so `human_wait` does not apply; the previous Stop already blocked | Stop with `stop_hook_active: true` | C: interactive auto before its first approval takes the auto outcome (exit 2, `unattended_continue`, `intent=stop` named), where it used to re-block with the real reason until Claude's cap; the first Stop is the real-reason block | 1 test |
| `EHB-STOP-20` | auto-interactive-pre, auto-interactive-post | the sole blocker is an evidence confirmation a human can be asked about (route `human` or `unobserved`, batch not yet shown); the previous Stop already blocked; this takes precedence over `EHB-STOP-08` | Stop with `stop_hook_active: true` | C: every interactive `auto` mode keeps the `evidence_report` directive (exit 2: the act-on-it action is the report that asks the human) | 1 test; ledger `stop_unattended_directive`, `auto_evidence_directive` |
| `EHB-STOP-21` | deliberative | Claude, a deliberative run, active and not converged, any blocker other than the existing settlements | the **first** Stop (`stop_hook_active` false or absent) and a later one | C: exit 0 with `systemMessage` = `paused_for_human` naming the real next step; the turn ends; the run stays `active`; the result is the same whatever `stop_hook_active` is | 1 test; ledger `stop_first_pause`, `stop_pause_rendering` |
| `EHB-STOP-22` | deliberative | Claude, a deliberative run, the sole blocker is an evidence confirmation a human can be asked about | Stop, with `stop_hook_active` false or true | C: exit 0 with `systemMessage` = `paused_for_evidence` (tell the agent to report, and you are asked in that call, if this host can show a dialog); the run stays `active`; nobody was asked | 2 tests; ledger `stop_first_pause` |
| `EHB-STOP-23` | deliberative, auto-interactive-pre, auto-interactive-post, auto-delegated | Claude, every control mode × run state of the turn-end table × `stop_hook_active` (false, absent, true) | `stop_result` for each cell | C: the exit code, stdout and stderr class of each cell equal the outcome the registry table (`turn_end`) names for it; read from the JSON, not restated in the test | 1 test |
| `EHB-STOP-29` | any | Pi, a converged run or no run | `agent_before_settle` | P: nothing is appended and no continuation is requested (a converged run, or no run) | 1 test |
| `EHB-STOP-35` | deliberative, auto-interactive-pre, auto-interactive-post, auto-delegated | Pi, one pending async audit | `agent_before_settle` | P: no continuation and nothing appended: the pending audit's result continues the run | 1 test |
| `EHB-STOP-36` | deliberative, auto-interactive-pre, auto-interactive-post, auto-delegated | Pi, the sole blocker is the initial approval or an exhausted budget | `agent_before_settle` | P: no continuation; the settlement notice (`human_wait`, `budget_exhausted`) is appended as a visible `custom_message` | 1 test; ledger `pi_settle_notice` |
| `EHB-STOP-31` | deliberative | Pi, a deliberative run, active, non-converged (including the evidence-wait case) | `agent_before_settle` | P: a `custom_message` entry with `display: true` holding `paused_for_human` (or `paused_for_evidence`) is appended; no `continue`; exactly one notice per settle (`turn_end` is not used) | 1 test; ledger `pi_settle_notice` |
| `EHB-STOP-32` | auto-interactive-pre, auto-interactive-post, auto-delegated | Pi, an auto run, active, non-converged | `agent_before_settle` | P: `continue: true` with the next obligation (the core's reason, or the evidence directive in the interactive modes) appended as the message | 2 tests; ledger `pi_auto_continue_bound` |
| `EHB-STOP-33` | auto-interactive-pre, auto-interactive-post, auto-delegated | Pi, an auto run that already continued on this observation basis and whose basis has not changed | `agent_before_settle` again | P: no `continue`; the `unattended_continue` notice is appended and `no-progress` is recorded; a changed basis (a new tool result, a changed run state) allows one more continuation | 1 test; ledger `pi_auto_continue_bound` |
| `EHB-STOP-34` | deliberative, auto-interactive-pre, auto-interactive-post, auto-delegated | Pi, every control mode × run state of the turn-end table | the settle handler for each cell | P: each cell equals the outcome the registry table (`turn_end`) names for it, read from the JSON | 1 test |
| `EHB-INT-05` | any | Pi ordinary (non-auditor) child whose result row is interrupted, detached or stopped, exit 0, no `isError` | `tool_result` | the child is recorded `failed`, never `completed` (one predicate, `childNotTerminal`, for the ordinary and the audit path); a completed child is `pending` then `completed` | 1 test |
| `EHB-FAIL-07` | any | Pi, the core projects a presentable evidence batch but the host has no dialog to show it in (`hasUI` false, or no select/input) | `report_convergence` | P: the gate reports `presentation_failed` (nothing spent, nothing evaluated) and blocks with that notice and `intent=stop` next; it never permits, which would emit the pause notice for a human nobody asked | 2 tests |
<!-- END GENERATED -->

## 8. Gaps: what the user must decide

### 8.1 `UNSOURCED` outcomes
| Id | Outcome | Question |
|---|---|---|
| G-01 | `HX-STOP-03` whether `stop_hook_active` stays true across tool calls within an auto continuation chain | **Does not gate the design:** if it resets, an auto run's post-tool Stop blocks again and the outcome is `host-capped` (`report-limitation`). A deliberative run never loops. Ledger row `stop_flag_reset` (auto only). Observed once in Q3 (it persisted across one tool call). |
| G-02 | `HX-STOP-03`: whether the Q2 hand-off carried `stop_hook_active: true` on the second Stop | **Moot** for deliberative mode: it pauses on the first Stop. Ledger row `stop_host_cap` keeps the Q2 count. |
| G-03 | `HX-STOP-03`: how Claude renders an exit-0 `systemMessage` at Stop | Ledger rows `stop_pause_rendering` and `stop_first_pause`; the experiment `2026-10-08-exp-first-stop` showed a first-Stop `systemMessage` ends the turn and is recorded as a `hook_system_message` attachment. |
| G-04 | `HX-ADM-01`: no run-start refusal for an out-of-range host (Claude or Pi) | **Resolved: no new refusal.** Receipt-time enforcement, `report-limitation`. |
| G-05 | `HX-STOP-04` visibility: Pi shows nothing at turn end | **Resolved (ADR-0068):** Pi 1.0.0 and later acts at `agent_before_settle` and shows one notice per settle. |
| G-06 | `HX-INT-01`: no "interrupted, not converged" notice on either host | Wanted? Claude gives no Stop; Pi's `agent_settled.aborted` is unused. |
| G-07 | Codex `HB-TOOL`, `HB-INT`, `HB-MODEL`, `HB-COMPACT` | Codex is `wip_unsupported`: leave "not declared", or read the Codex adapter for a conformance baseline? |
| G-08 | `HX-ACT-01` guard for Pi `session_start` vs a Claude `startup` where the run was not started by `/empirica` | None known; listed so it is not forgotten. |
| G-09 | `HX-SESSION-01`: Claude restores on `compact` only (`resume`, `clear`, `fork` not wired) | Intended? A resumed Claude session with an active run is gated by Stop and tools, but the author gets no handle back. |
| G-10 | `HX-DLG-01` form-level `decline` vs `declined` | **Resolved: no-decision**, see C-02. |

### 8.2 `MISSING` tests

Phase A of round G9 adds scenarios whose tests are written in phase B. Until then `make contract-check` fails on exactly these ("has no covering test and is not live-only"), by design:

- `EHB-STOP-21` to `EHB-STOP-34`: the deliberative pause on a first Stop and on the evidence wait (Claude), the table-driven test per host, and the Pi handler.
- `EHB-TERM-01` to `EHB-TERM-04`: consent before a deliberative `stopped_*` (accept, refusal, no dialog, auto unchanged). `EHB-TERM-05` is live-only (`terminal_stop_consent`).

Existing tests whose deliberative cells change in phase B: `ClaudeStopEvidenceByControlMode` (`V2/test_a1_boundaries.py`), `StopModeTests::test_EHB_STOP_06_*`, `_19_*`, `_20_*` (Codex is not a supported host; its `_stop` is not part of the turn-end table). `EHB-STOP-24` to `EHB-STOP-28` (the Codex cells) and `EHB-STOP-30` (split into `EHB-STOP-35` for the pending audit and `EHB-STOP-36` for the settlement) are retired, not reused. `EHB-STOP-10` ("Pi has no turn-end gate") is retired, not reused.

### 8.3 Conflicts between sources
| Id | Conflict | Question |
|---|---|---|
| C-01 | **Stop never pauses** (F5, ADR-67, `QREF claude.md`, A1 test) vs **Q2/Q3**: an operator hand-off turn is a legitimate outcome, and the first Stop carried the agent past the checkpoint. | **Resolved (ADR-0068):** one turn-end table; deliberative pauses on any other active state, auto continues. F5, F8, ADR-0051 and ADR-0063 hold; ADR-0067 carries a dated amendment. |
| C-02 | Heath AS3 vs Empirica's per-dialog decline mapping. | **Resolved: no change.** The mapping is decided and disclosed in each dialog (see `HX-GOV-02`, decline mapping). |
| C-03 | ADR-52 `<1.1.0` provisional vs the user ruling `>=1.0.0,<2.0.0` (earlier `>=0.84.1`). | **Resolved:** interval `<2.0.0`, qualified 1.1.0, new dated ADR-0052 amendment (G2). The receipt floor is the profile's `version` (ADR-0069 G6-R1); the constant this row once named is deleted. |
| C-04 | S5-R3 `context:"fresh"` vs `canonicalAuditorInputError` forbidding the author's `context`. | **Resolved (G2):** the host adds it in `auditLaunchInput`; the author-side guard is unchanged. |
| C-05 | the reviewed pi-subagents list vs the matrix failing for four versions at HEAD. | **Resolved (G2):** the four fixtures were re-captured (`definitionDigest` stays unmasked); 0.76.1 admitted; the matrix is green for all five. |
| C-06 | `pi_api_typecheck` checked the Empirica mirror only; the Methodologist mirror failed against the then floor. | **Resolved (G2):** both mirrors are typechecked against 1.0.0 and 1.1.0 (G9: the floor and the latest); the Methodologist handler type was fixed. |
| C-07 | Claude interval vs the installed 2.1.294. | **Resolved:** the interval is unchanged; the profile id and version move to `claude-code`. |

### 8.4 Questions that cut across entries
All three were ruled (ADR-0068): (1) the registry is data (`host-boundaries.json`, validated in `contract-check`); (2) the live-only scenarios are the ledger rows named in `host-boundaries.json`; (3) the pause wording is carried in `settlement_notices.paused_for_human` and `paused_for_evidence` next to `human_wait`, `evidence_report` and `budget_exhausted`, with `unattended_continue` for automatic runs.
