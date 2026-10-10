---
name: native-qualification
description: Qualify Empirica end to end in an operator-present Claude Code or Pi session, keeping host evidence and refusing to fabricate consent, races, failures or convergence. Use for native acceptance after adapter, UI or contract changes, not for routine unit checks.
compatibility: Needs a supported Claude Code or Pi executable, this checkout, Python 3, Git, make, and an operator who answers the native prompts. Receipt validation uses the repository Make targets.
metadata:
  workflow: empirica-native-qualification
  receipt: empirica-live-receipt/v3
---

# Native qualification

One deliberately prepared **real host session**, not a simulated successful journey. Run lifecycle commands from the
repository root through Make. `make native-qualification` prints this directory.

Never answer a human consent prompt, choose a dialog answer, seed handles, invent research, reopen a terminal
`stopped_*` run, copy credentials, replace global packages, or change permissions, authentication or user settings.
Launch only after the operator explicitly says go. Make one launch attempt. If terminal creation or input delivery is
uncertain, inspect that session rather than retrying. No automatic relaunch loops. The scripts in `scripts/` read and
report; they never answer, launch, or declare a pass.

## 1. Prepare before opening a host

**Keep the activation short.** Claude does not expand a skill invocation longer than about 900 characters (observed in
Q6): the text arrives as a plain message and no run starts. Keep the whole activation, goal included, under about 650.
Read the [host registry](../../../contracts/empirica/v2/host-profiles.json) and the
[governance guide](../../../plugins/empirica/skills/empirica/references/governance.md). They are authoritative. Use an
already prepared supported executable; an out-of-range installation is **BLOCKED**, not permission to upgrade global
tools. Empirica is not available on Codex.

The operator's first message, the goal given at activation, tells the author to pause at every operator checkpoint
(model switch, `/compact`) and wait for the operator to say go before continuing. Without it the author runs straight
through and those rows end `NOT_REACHED`.

Agree with the human on the host, the exact candidate checkout, a small falsifiable goal and claims, initial limits
`8/0/1`, the host reviewer configuration, the host decision timeout, and a fresh evidence directory **outside the source
checkout**. Omit Empirica `--auto` in the primary scenario. Choose a non-destructive spike against an existing file; no
source edits are needed. Git worktrees share durable Git shadow refs: `EMPIRICA_HOME` isolates operational files, not Git
storage. Use a separately prepared task repository when complete Git isolation is required.

Before launch:

- Record HEAD, the exact host, plugin and subagent versions, the expected load paths and the reviewer configuration
  (`python3 scripts/census.py` prints the checkout facts). A release qualification needs a clean committed candidate
  because receipts bind HEAD and version.
- For an exploratory dirty candidate, retain `git status --porcelain=v1 --untracked-files=all`, both binary diffs, and a
  SHA-256 manifest of tracked and untracked candidate bytes (`python3 scripts/manifest.py`). Never stage to obtain a
  fingerprint. Mark the result non-release if source identity is incomplete.
- Create a new evidence directory exclusively (`python3 scripts/evidence_dir.py <path>` refuses an existing one). An
  existing directory or launch marker needs reconciliation, not deletion. Record the chosen command and a
  launch-requested marker before sending it. Preserve the normal HOME, provider and auth configuration, unrelated
  plugins and permissions. Set `EMPIRICA_HOME` to the evidence directory's `state` child.
- Model switches persist. Snapshot the host settings before the session and restore them during cleanup (the files
  are named in the host reference).
- Host specifics (launch command, elicitation, reviewer configuration, settings files): [Claude](references/claude.md),
  [Pi](references/pi.md).
- **Start the observer UI on the run's own store before launch.** Run
  `python3 plugins/empirica/adapters/observer/cli.py serve --home <evidence>/state --repo <task repository> --port 0`
  from this checkout (history lives in Git, not in the store) and keep
  its log in the evidence directory. Open the printed loopback URL where the operator can watch it. It is read-only and
  is checked at every checkpoint (ledger row `observer_ui_live`).

After explicit go, launch one of the checkout overrides in the host reference, inheriting the prepared environment.
Capture loaded paths, parent session ID, native transcript and host signals. A successful terminal API response alone
proves neither activation nor loading. Stop on any mismatch.

## 2. Run the scenario ledger

Execute the rows of [the ledger](references/scenarios.md) in table order: the primary run, the ADR-0067 evidence rows,
the required refusal rows (each in a separate fresh run), the host-boundary rows (D2, live-only), then the Methodologist sanity. They are author instructions
plus human checkpoints, not a script for fabricating results. Record each row as `PASS`, `FAIL`, `BLOCKED` or
`NOT_REACHED` with its evidence pointer. Read state only through public tools and never edit operational state. A row
marked *deterministic* is covered by a named test and is never claimed live. In particular the 128-interaction ceiling is a fixed contract constant, not an operator setting: native qualification never exercises it.

## 3. Receipt

After one complete converged trace, build and validate the v3 receipt as in [the receipt reference](references/receipt.md).
A receipt does not prove every UI checkpoint and cannot qualify dirty source.

## 4. Retain one compact result

Retain candidate, source-manifest, release-candidate, host, profile and version, plugin and subagent and loaded-path
facts; session, child and operation IDs; the command; and the host decision timeout. Then record one line per Row ID:

```text
<row_id> | PASS / FAIL / BLOCKED / NOT_REACHED | <artifact pointer>
```

Add `methodologist` in the same shape, then the overall result and limitations. PASS is valid only when every required
row passed. Stop on a required blocker, mark later rows NOT_REACHED, and preserve failed artifacts. One host pass does
not qualify another host. `make check` is local regression evidence; release qualification also needs the integration
and installed-host gates in `make release-check`.
