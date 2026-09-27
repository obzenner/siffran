---
name: native-qualification
description: "Qualify this checkout in an operator-present Claude Code or Pi session. Use for native acceptance after adapter/UI changes, not routine unit checks. Preserve failures and require real human consent."
allowed-tools: [Read, Glob, Grep, Bash, Write]
---

# Native qualification

One deliberately prepared **real host session**, not a simulated successful journey. The repository
root is `../../..` from this skill directory; run lifecycle commands there through Make.
`make native-qualification` only prints this entrypoint.

Never answer a human consent prompt, seed handles, invent research, reopen a terminal `stopped_*`
run, copy credentials, replace global packages, or change permissions, authentication, or user
settings. Launch only after the operator explicitly says go. Make one launch attempt. If terminal
creation or input delivery is uncertain, inspect that session rather than retrying. No automatic
relaunch loops.

## 1. Prepare before opening a host

Read the [host registry](../../../contracts/empirica/v2/host-profiles.json) and
[governance guide](../../../plugins/empirica/skills/empirica/references/governance.md). They are
authoritative. Use an already prepared supported executable; an out-of-range installation is
**BLOCKED**, not permission to upgrade global tools. Codex Empirica is unsupported for convergence.

Agree with the human on the host, exact candidate checkout, small falsifiable goal and claims,
initial limits `8/0/1`, modes, host reviewer configuration, host decision timeout, and a fresh
evidence directory **outside the source checkout**. Omit Empirica `--auto` in the primary scenario.
Choose a non-destructive spike against an existing file; no source edits are needed. Git worktrees
share durable Git shadow refs: `EMPIRICA_HOME` isolates operational files, not Git storage. Use a
separately prepared task repository when complete Git isolation is required.

Before launch:

- Record HEAD, exact host/plugin/subagent versions, expected load paths, and reviewer configuration.
  A release qualification requires a clean committed candidate because receipts bind HEAD/version.
- For an exploratory dirty candidate, retain `git status --porcelain=v1 --untracked-files=all`, both
  binary diffs, and a SHA-256 manifest of tracked and untracked candidate bytes. Never stage to
  obtain a fingerprint. Mark the result non-release if source identity is incomplete.
- Create a new evidence directory exclusively. An existing directory or launch marker requires
  reconciliation, not deletion. Record the chosen command and a launch-requested marker before
  sending it. Preserve normal HOME, provider/auth configuration, unrelated plugins, and permissions.
  Set `EMPIRICA_HOME` to the evidence directory's `state` child.
- Claude needs native MCP form elicitation. The primary checklist verifies settings `env` propagation
  and the entrypoint signal. Configure its reviewer as described in the
  [governance guide](../../../plugins/empirica/skills/empirica/references/governance.md).
- For Pi, use the checkout's complete package and run the auditor-resolution inspection described in
  `doc/testing.md`. Configure its reviewer as described in the
  [Pi adapter guide](../../../plugins/empirica/adapters/pi/README.md#governed-initialization).

After explicit go, launch one of these exact checkout overrides, inheriting the prepared environment:

- `make claude-dev CLAUDE=/absolute/supported/claude ARGS="--debug-file /absolute/evidence/claude.debug.log"`
- `make pi-dev PI=/absolute/supported/pi`

Capture loaded paths, parent session ID, native transcript, and host signals. A successful terminal
API response alone proves neither activation nor loading. Stop on any mismatch.

## 2. Primary Empirica scenario

These are author instructions plus human checkpoints, not a script for fabricating results. Record
each row as `PASS`, `FAIL`, `BLOCKED`, or `NOT_REACHED` with its evidence pointer. Read state only
through public tools and never edit operational state. Execute rows in table order.

| Row ID | When | Human/author action | Expected observation | Evidence |
|---|---|---|---|---|
| `settings_env_and_entrypoint` | Before activation | Inspect the retained Claude hook/MCP log, or Pi host context. | Claude settings `env` reaches hooks and MCP; the recorded entrypoint is `cli`. Pi records `ctx.mode=tui\|rpc` with `interactive=true`. | Hook log or host context |
| `activation_verbatim_goal` | Start | Human invokes Claude `/empirica:empirica <goal>` or Pi `/empirica <goal>`. | User-command expansion, exact verbatim goal, trusted invocation provenance, and fresh run binding. A model-invoked skill/read or ordinary pasted chat does not activate. | RunView and transcript |
| `configuration_pending` | After route and graph | Route, propose the graph from supplied context, then submit `configure_run` with `8/0/1` and modes. | Human review shows goal read-only and configuration edits only; graph and reviewer fields are absent; state is pending with zero investigation usage. | Pending RunView and dialog |
| `sentinel_blocked_before_approval` | While configuration is pending | Attempt one benign, uniquely named write in the evidence directory. | Host blocks before execution; operator-side inspection confirms the file is absent. Unexpected execution is FAIL: preserve evidence and stop. | Denial plus operator file listing |
| `locked_approval_at_6` | After sentinel | Human edits passes `8 → 6` and confirms the host-owned locked summary. | One FINAL CONFIRMATION occurs in the same call, read-only, with no author action between forms. Effective limit is 6 and digest, configuration epoch, and human provenance match. Host decision timeout is read-only, not a total run deadline. | Dialog transcript and RunView |
| `graph_change_keeps_configuration_approval` | After approval | Amend the claim graph from supplied context. | Configuration approval remains current and no configuration dialog opens. | RunView before/after |
| `research_and_spike` | After investigation admission | Record investigation, cited research, and one deterministic spike. | Source, command, input bindings, exit status, result, and lossless obligations are retained; nothing is invented. | RunView and evidence |
| `current_author_after_model_switch` | Between research and spike | Human switches the main model A→B, allows one turn that records no evidence, switches back to A, then the spike is recorded. | Every artifact's producer is the model that served it (A); nothing is attributed to B; the reviewer is derived from the current author A, not from B or the earliest model. | RunView attribution and host model-switch signal |
| `served_reviewer_and_producers_distinct` | When audit is owed | Request one canonical auditor through the host profile. | Package path/scope is canonical; the verdict-bearing served model is observed separately from requested configuration; independence classification follows the [audit guide](../../../plugins/empirica/skills/empirica/references/audit.md#independence-reporting). | Child transcript and RunView |
| `audit_stales_after_graph_change` | Optional, after a passing audit | Amend the graph, then re-evaluate. | Prior audit coverage becomes stale while configuration approval remains current; a fresh bound audit is required. | RunView before/after |
| `restore_after_compaction` | After a current passing audit, before completion | Human triggers host compaction (`/compact`). | The restored run has the same status, obligations, usage, and audit coverage; blocked actions remain blocked; no progress is fabricated. | RunView before/after compaction |
| `snapshot_completion` | After restore | Call the guarded completion path. | Exact `Allow(converged=true)` and matching durable terminal state. The terminal RunView keeps Required/Observed/Missing and offers only `run.inspect` guidance, no mutation actions. Prose, child pass alone, or `Allow(converged=false)` is not convergence. | Decision and terminal RunView |
| `installed_transport_latency` | Throughout | Operator records the slowest Empirica tool/hook round trip from the retained log. | No host transport timeout, retry, or dropped tool result occurred. | Debug/host log timings |

For the Pi audit row, invoke the packaged auditor once through the structured `subagent` tool
with exactly `{"agent":"empirica.empirica-auditor","task":"Audit the host-provided dossier."}`;
send no other fields. A rejected launch is an admission failure, so stop rather than retrying. The
[Pi adapter guide](../../../plugins/empirica/adapters/pi/README.md#canonical-audit-call) owns the
complete invocation and reviewer-derivation rules.

For a retained Claude admission log, run
`make empirica-claude-mcp-log-check LOG=/absolute/evidence/claude.debug.log`. After one complete
converged trace, use `make empirica-host-receipt` with the arguments listed by `make help`, then
`make empirica-host-live-check`. A receipt does not prove every UI checkpoint and cannot qualify
dirty source.

## 3. Required refusal checks in separate fresh runs

Do not squeeze contradictory branches into the primary run. Execute each required row in a separate
fresh run.

| Row ID | Human/operator action | Expected observation | Evidence |
|---|---|---|---|
| `empty_goal_refusal` | Invoke without a goal and, where the host permits it, with whitespace-only goal input. | Visible structural `Block` and no run/handle creation. | Host output and run census |
| `noninteractive_auto_refusal` | Invoke auto through a non-interactive surface without the configured delegation environment variable. | Claude records `sdk-cli` from env or transcript. Pi records provenance signal `ctx.mode=print\|json` with `interactive=false`. Both return a visible structural `Block`, create no run, and name interactive invocation or delegation as remedies. | Hook log or RunView refusal |
| `delegated_auto_admission` | Optional: only when the operator explicitly prepared `EMPIRICA_AUTO_DELEGATION=1`, repeat non-interactive auto. | Provenance records delegation and StartRun is admitted. This does not qualify audit or convergence. | RunView |
| `cancellation_and_timeout_nonterminal` | Human cancels the first configuration dialog; at the next presentation lets it expire. Launch this run with a short `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` (for example `60`) prepared by the operator. | Each settles as dismissal, not rejection: the run stays active and non-terminal, configuration is not approved, investigation remains blocked, and the presentation count is visible. End with an honest stop. | Dialog transcript and RunView |
| `mixed_producers_block` | Human switches the main model after research so the spike is served by a second model, then the author requests the audit and the guarded completion. | The audit may launch and complete, but the verdict is classified `mixed` and completion is refused with `audit.producers_mixed`; never `Allow(converged=true)`. End with an honest stop. | RunView independence/blockers and decision |
| `reviewer_preflight_refusal` | Pi required; Claude optional. Author runs on the model the host would choose as reviewer (Pi: the effective `subagents.defaultModel`), records evidence, then requests the audit. | Refused before launch as same identity class or unavailable; no child is reserved and audit usage is unchanged. End with an honest stop. | RunView and host denial |
| `codex_negative` | Optional: invoke `$empirica <goal>` in Codex. | Visible unsupported/refusal result; no convergence claim. | Codex transcript |

On Claude, `reviewer_preflight_refusal` cannot be induced without changing user settings while any
other Claude family is pinned; record it `NOT_REACHED` with that reason. A failure *after* reservation
(refunded `launch_rejected`, then a relaunch in the same run) is not induced natively; it is covered
by the real-service regressions in `make empirica-host-integration`. Record that pointer in the
result rather than a native PASS.

Large-scope viewport accessibility is an optional separate run.

## 4. Small Methodologist sanity

After Empirica is terminal, use Claude's `/methodologist:think` or Pi's `/think`, choose a named
methodology as the human, and verify the six-phase plan comes from this checkout. Where MCP
selection is exposed, retain one normal `methodologist_select` result. On Codex use `$think` or the
implicit native simple mode, not a slash command. Codex Empirica can establish only its expected
fail-closed refusal, never convergence qualification.

## 5. Retain one compact result

Retain candidate, source-manifest, release-candidate, host/profile/version, plugin/subagent and
loaded-path facts; session/child/operation IDs; command; and host decision timeout. Then record one
line per Row ID in the tables above:

```text
<row_id> | PASS / FAIL / BLOCKED / NOT_REACHED | <artifact pointer>
```

Add `methodologist` using the same row shape, followed by the overall result and limitations. PASS
is valid only when every required table row passed. Stop on a required blocker, mark later rows
NOT_REACHED, and preserve failed artifacts. One host pass does not qualify another host. `make check`
is local regression evidence; release qualification also requires the integration and installed-host
gates in `make release-check`.
