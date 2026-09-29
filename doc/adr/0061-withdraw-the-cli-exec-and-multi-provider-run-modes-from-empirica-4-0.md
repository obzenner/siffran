---
number: 61
title: Withdraw the cli_exec and multi_provider run modes from Empirica 4.0
status: accepted
date: 2026-09-29
tags:
- empirica
- governance
- modes
- actors
- attribution
links:
- target: 24
  kind: Amends
- target: 28
  kind: Amends
- target: 36
  kind: Supersedes
- target: 60
  kind: Amends
---

# Withdraw the cli_exec and multi_provider run modes from Empirica 4.0

## Context and Problem Statement

ADR-24 introduced two optional run modes. `multi_provider` allowed actors outside the host harness (pi, codex). `cli_exec` dispatched actors as non-interactive CLI subprocesses (`claude -p`, `pi -p`, `codex exec`), bought dispatcher-witnessed attribution, and cost the spawn budget. ADR-28 set them from the invocation, and ADR-36 made the doctor probe codex and pi when `multi_provider` was on.

The strongest implementation shipped in 1.x, and it was only partial. A `PreToolUse:Bash` hook recognised actor CLIs in a command, charged the spawn ledger and recorded the declared attribution. It never blocked when `cli_exec` was off and never checked `multi_provider`. The Mode B adapters, in which Empirica itself runs the actor process, were never delivered (ADR-24, *Build outcome*).

The strict v2 rewrite (commit `095bf51`, plugin 2.0.0) removed even that enforcement. From 2.0 through the unreleased 4.0 branch, the modes have been parsed, stored and approved, and 4.0 projects them into the human approval dialog. No gate reads them. The host `dispatch` action falls through to `Fault unsupported` (`core/evaluation.py`), and the Claude Bash hook fails open. The 4.0 governance dialog therefore asked a human to approve two capabilities that had no effect. That is a consent defect.

A design pass tried to restore enforcement in 4.0 by gating recognised shell dispatches on Claude and Pi. It is recorded in `.research/replan-4.0.0/design/MODES.md`. Two independent audits, both verdict **fail**, established that inferring actor launches and their lifecycle from arbitrary shell text cannot be made sound:

- **Classification is not reliable.** Quote-aware tokenisation still makes `printf ';' codex exec` and `printf ; codex exec` identical, as it does `'$(codex exec)'` and `"$(codex exec)"`. Newlines vanish, heredocs look like commands, and the classifier returned only the first actor.
- **Accounting is not sound.** Short-circuit operators and loops make the number of actors in the text differ from the number of processes run.
- **The lifecycle is unobservable.** A shell tool result is not actor completion (background jobs, `; true`, cancellation). Claude fires no failure hook on denial or cancellation, and the existing `launch_rejected` transition always refunds.
- **The gate would fail open.** A command hook that exceeds its host timeout does not block the tool call.
- **Pi descendants are unobserved.** `pi-subagents` children are separate processes, and the parent's `tool_call` hook does not see their shell commands.

## Decision Drivers

* Human approval must never present a capability the system does not enforce (ADR-60: approval must not be misrepresented).
* The core decides from admitted host-observed facts. It must not infer them from author-authored shell text.
* No dead configuration, parsing or documentation in the release (the code-quality bar for 4.0).
* The contract is still unreleased (public contract 3.0.0), so removal is a compatible change of the 4.0 line.

## Considered Options

* Restore enforcement by gating recognised shell dispatches: refuted twice by independent audit (above).
* Keep the modes, but display them read-only as "not enforced in 4.0".
* **Withdraw the modes and the shell-dispatch surface from 4.0. Reintroduce actor modes only together with a host-owned actor tool.**

## Decision Outcome

Chosen option: **withdraw the modes and the shell-dispatch surface from 4.0.** A read-only display would keep configuration that does nothing, as well as its parsing, storage and documentation, only to tell the human it is inert.

Empirica 4.0 removes all of the following:

- the `cli_exec` and `multi_provider` modes from run state, `configure_run`, the approval dialog, the public contract (`mode_fields` and the mode controls), the state and request schemas, and the generated fixtures;
- the `--cli-exec`, `--multi-provider` and `--no-*` invocation flags, and the `EMPIRICA_MODE_*` environment variables, on Claude, Codex and Pi. These flags now surface as unknown flags;
- the host `dispatch` action and its request schema, the Claude `PreToolUse:Bash` actor-dispatch classifier and hook, and the dispatch advice;
- the doctor's multi-provider probe. The baseline doctor is unchanged.

Approval covers budget ceilings and the control mode (deliberative or `--auto`). This amends ADR-60's "budget ceilings, modes, and control mode".

### Future reintroduction

Actor modes may return only together with a **host-owned actor tool**. This is ADR-24's Mode B as specified: the author asks the host to run a named actor, and the host does the following.

- The core decides admission from the approved modes and budget: `cli_exec` gates the tool, `multi_provider` gates a target outside the host harness, and the host supplies a closed same/external relation so the core never interprets names. The core then reserves one spawn.
- The host runs the process itself with a fixed argv and no shell, under a bounded timeout, and observes the exact lifecycle and exit status.
- The host records **witnessed** attribution, because it chose the target and model.

Shell-level detection of actor CLIs may then exist only as best-effort advice or denial pointing to the tool. It is never the enforcement mechanism. A proposal to reintroduce actor modes must answer every refutation above, and must pass an independent audit before code.

### Consequences

* Good, because the approval dialog shows only enforced configuration, and the consent defect is closed.
* Good, because parsing, state, contract and documentation surface are removed, along with the unsound shell classifier.
* Bad, because 4.0 offers no sanctioned way to use an external-harness actor with budget accounting or attribution. An author's own shell use of an actor CLI is unmetered. This is design-time assurance, not a sandbox.
* Neutral, because audit independence is unaffected. Evidence producers are observed when each artifact is admitted (ADR-60), and CLI output reaches a run only as author-submitted research.

### Confirmation

* No reference to `cli_exec`, `multi_provider`, `EMPIRICA_MODE_*` or the `dispatch` action remains in code, contract, schemas, fixtures or live docs. The only exceptions are this ADR, historical ADRs, and tests that assert the removed flags surface as unknown and that a `dispatch` action is rejected.
* The approval dialog on Claude and Pi shows exactly three budget rows.
* `make check`, `make empirica-core-integration` and `make empirica-host-integration` pass.
