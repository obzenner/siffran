---
name: native-qualification
description: "Qualify this checkout in an operator-present Claude Code or Pi session. Use for native acceptance after adapter/UI changes, not routine unit checks. Preserve failures and require real human consent."
allowed-tools: [Read, Glob, Grep, Bash, Write]
---

# Native qualification

One deliberately prepared **real host session**, not a simulated successful journey. The repository root is `../../..` from this skill directory; run lifecycle commands there through Make. `make native-qualification` only prints this entrypoint.

Never answer a human consent prompt, seed handles, invent research, reopen a terminal `stopped_*` run, copy credentials, replace global packages, or change permissions/auth/user settings. Launch only after the operator explicitly says go. One launch attempt: on uncertain terminal creation/input delivery, inspect the existing terminal/session rather than retry. When using Orca, load its CLI skill and use its managed terminal APIs. No automatic relaunch loops.

## 1. Prepare before opening a host

Read the [host registry](../../../contracts/empirica/v2/host-profiles.json) and [governance guide](../../../plugins/empirica/skills/empirica/references/governance.md). They are authoritative, not model recollection. Claude currently admits `>=2.1.278,<2.2.0`; Pi admits `>=0.84.1,<0.85.0` with exact `pi-subagents@0.50.0`. Use an already prepared side-by-side supported executable; an out-of-range installation is **BLOCKED**, not permission to upgrade global tools. Codex Empirica is unsupported for convergence.

Agree with the human: host, exact candidate checkout, small falsifiable goal/claims, initial limits `8/0/1`, modes, concrete reviewer, deadline and fresh evidence directory **outside the source checkout**. Omit Empirica `--auto` in the primary scenario. The host's auto-permission footer is unrelated. Choose a non-destructive spike against an existing file (for example, checking a Make help entry); no source edits are needed. Git worktrees share durable Git shadow refs: separate `EMPIRICA_HOME` isolates operational files, not Git storage. Use a separately prepared task repository if complete Git isolation is required; do not improvise a new loader during the run.

Before launch:
- Record HEAD, exact host/plugin/subagents versions and expected load paths. A release qualification requires a clean committed candidate; receipts bind HEAD/version, not dirty bytes.
- For exploratory dirty candidates, retain `git status --porcelain=v1 --untracked-files=all`, both `git diff --binary HEAD` and `git diff --cached --binary`, and a SHA-256 manifest of actual tracked **and untracked candidate file contents**, with explicit artifact exclusions and deleted paths. Never stage files to obtain a fingerprint. Label the result non-release; if complete source identity cannot be captured, mark provenance BLOCKED.
- Create a new evidence directory exclusively; an existing directory/launch marker requires reconciliation, not deletion. Record the chosen command and a launch-requested marker before sending it. Preserve normal HOME, theme, provider/auth configuration, other plugins and permissions.
- Claude needs native MCP elicitation availability. Empirica does not read operator model configuration; selecting a reviewer is not proof of availability or separate authorization.
- Set `EMPIRICA_HOME` to the new evidence directory's `state` child. Use the checkout's complete Pi package (including bundled auditor and subagents), not its internal adapter directory. Pi's committed `.pi/settings.json` provides the checkout override while preserving unrelated user packages. Do not replace normal settings with an isolated home.

After explicit go, launch interactively through **`make claude-dev CLAUDE=/absolute/supported/claude`** or **`make pi-dev PI=/absolute/supported/pi`**, inheriting the prepared environment. For retained Claude admission logs, pass `ARGS="--debug-file /absolute/evidence/claude.debug.log"`. These are directory-plugin/project overrides, not global installs. Capture the actual loaded paths, parent session ID and native transcripts; a successful terminal API response alone proves neither activation nor loading. Stop on any setup mismatch.

## 2. Primary Empirica scenario

Use these as instructions to the author plus human checkpoints, not a fixed script that fabricates tool results. Each row receives `PASS`, `FAIL`, `BLOCKED`, or `NOT_REACHED` with evidence. Read state through public tools and retain snapshots; never edit operational state.

1. **Activation:** the human invokes Claude's `/empirica:empirica <goal>` or Pi's `/empirica <goal>`. Verify native user-command expansion and fresh run binding before proceeding. A model-invoked Skill/read call is not activation. Stop if the command was pasted as ordinary chat.
2. **Proposal:** from supplied context only, route and propose the complete readable scope, budgets, modes and concrete auditor. Verify pending state and zero investigation usage. The human reviews the actual form/dialogs.
3. **Request/edit:** the human chooses the dedicated Request changes control, then enters a plain-language scope correction in its separate feedback dialog. The approval/edit surface must not expose a feedback field. The author reads `run.governance.change_request`, revises scope as requested and preserves the current configuration when presenting the new proposal. Leave passes at `8` here so step4 exercises the configuration-only confirmation path. Retain exact feedback and pending snapshots: neither edits nor feedback count as consent.
4. **First approval:** edit passes `8 → 6` and choose the host's edit/approval path. On both Claude and Pi expect a **host-owned FINAL CONFIRMATION in the same tool call**, showing those exact edits read-only, with no intervening author action. It offers only Confirm or Decline; do not enter feedback there. Verify pending/effective snapshots before confirming; then the human explicitly confirms that unchanged summary. Verify the agreed effective limit, exact digest/revision and human approval provenance. `6` is the primary example, not a reason to overwrite other intentional human values.
5. **Revocation:** enter investigation and obtain actual cited research. Make one agreed material scope revision. Verify approval is revoked. **Do not approve the replacement yet.**
6. **Blocked sentinel:** while that replacement is still unapproved, attempt a benign uniquely named side effect in the evidence directory. Confirm the host blocks it **before execution** and the file remains absent, using operator-side inspection. Unexpected execution is FAIL: preserve it and stop. Do not run destructive or secret-bearing commands.
7. **Reapproval/work:** now the human approves the fresh proposal. Perform admitted cited research and one deterministic spike. Retain command, input bindings, exit status and result; do not invent citations or output.
8. **Audit:** request exactly one canonical auditor through the profile's managed mechanism. Claude uses managed **async** audit/Stop settlement/handback; Pi uses **foreground** audit. Follow `audit_execution`, not the `foreground_only` tier name. Verify observed resolved author/auditor identities, approved selection, exact child/session/operation binding and current-snapshot verdict. Prefer a known distinct authorized model for this primary run.
9. **Completion:** call the guarded completion path; require the actual `Allow(converged=true)` and matching durable terminal state. A prose success, child pass alone or `Allow(converged=false)` is not convergence. Stop honestly on blockers; never force a passing verdict.

For Claude MCP admission use `make empirica-claude-mcp-log-check LOG=...` where applicable. After a complete one-child converged trace, use `make empirica-host-receipt` with its required transcript/state/child/version/command/output arguments from `make help`; `make empirica-host-live-check` checks both supported hosts' receipts. Receipt verification does **not** prove every UI checkpoint, cannot record partial failures, and does not qualify dirty source. Do not weaken it to accept the wrong trace.

## 3. Small Methodologist sanity

After Empirica is terminal, use Claude's `/methodologist:think` or Pi's `/think`, choose a named methodology as the human and verify the six-phase plan comes from this checkout. Where MCP selection is exposed, retain one normal `methodologist_select` result. On Codex use `$think`/implicit native simple mode, not a slash command; request structured bridge mode separately if testing MCP. Codex Empirica can establish only its expected fail-closed refusal, never convergence qualification.

Cancellation, large-scope viewport accessibility, and explicit auto are **separate optional runs**, not contradictory branches squeezed into the primary scenario. For Claude cancellation, verify a **not converged** human-wait notice settles the turn without reopening the form, changing the active run to terminal, or permitting investigation. Auto is not human approval and still requires a known-distinct author-proposed reviewer.

## 4. Retain one compact result

```text
candidate: <HEAD>; source_manifest: <path|clean>; release_candidate: <yes|no>
host/profile/version: <exact>; subagents/plugin: <exact>; loaded_paths: <paths>
session/child/operation: <ids>; command: <command>; deadline: <value>
checkpoint | PASS / FAIL / BLOCKED / NOT_REACHED | artifact pointer
activation | ... | ...
proposal | ... | ...
request_edit_pending | ... | ...
approval_at_6 | ... | ...
revision_revokes | ... | ...
sentinel_blocked_before_execution | ... | ...
reapproval_research_spike | ... | ...
independent_audit | ... | ...
snapshot_completion | ... | ...
methodologist | ... | ...
result: <PASS only if all required checkpoints passed; otherwise FAIL/BLOCKED>
limitations: <including optional scenarios not run; no secrets>
```

Stop on a required blocker, mark later rows NOT_REACHED and preserve failed artifacts. A single host pass does not qualify the other host or optional branches. `make check` is local regression evidence; release qualification additionally requires the deliberate integration and installed-host gates in `make release-check`.
