# Empirica Pi adapter (v2)

This is the v2-only Pi translation shell. It exposes the same public author/read/report
surface as the Claude and Codex adapters and owns no convergence policy.

## Capability profile

The promoted profile is qualified on Pi `0.84.1` and admits compatible Pi releases
`>=0.84.1,<1.1.0`; receipts still record the exact observed Pi version. The profile tier is
`foreground_only`, with `promotion_status=promoted` after an installed-host foreground trace reached
guarded `Allow(converged=true)`. `pi-subagents` is an external prerequisite
(`pi install npm:pi-subagents@<reviewed version>`), never bundled or loaded by this package; it must
provide the structured `subagent` tool. Only the exact versions in `subagents_compatibility.reviewed_versions`
(`contracts/empirica/v2/host-profiles.json`) are supported; any other version is refused before a run starts. Asynchronous audit execution is not supported and is never silently
downgraded.

## Audit runtime ownership

`src/runtime-owner.ts` resolves the audit runtime at `session_start` from `pi.getAllTools()` and
`pi.getCommands()`; it never searches the file system and never imports `pi-subagents` by bare name.
Pi keeps only the first registrant of a tool name, so `getAllTools()` can show a single `subagent`
even when two `pi-subagents` copies are loaded. The adapter therefore also resolves the nearest
`pi-subagents` package of every extension slash command (Pi does not deduplicate commands) and refuses
(`host.subagents_duplicate_owner`) when the tool owner and the commands name more than one distinct
package root. A copy that registers no commands cannot be seen, and a second `subagent` tool entry
(not produced by current Pi) is refused as well. The first registrant — the one Pi runs — is bound.
Its canonical `sourceInfo.path` locates the nearest enclosing `package.json`,
which must be named `pi-subagents` and carry a semver `version`. The adapter then resolves
`pi-subagents/preflight` *from that package* and requires a callable `resolveSubagentLaunchContract`
whose file belongs to that same package (the nearest `package.json` above it is the owner's root; a
nested `node_modules/pi-subagents` copy is refused).
Each failure is a typed code mapped to a public-contract reason by `src/owner-refusal.ts`
(`host.subagents_missing`, `_duplicate_owner`, `_owner_unverified`, `_version_unsupported`); the
guidance text comes from the contract. The owner is re-observed when `/empirica`, `configure_run`,
and the canonical auditor launch run; if the owner has changed since `session_start` the run is
refused and the audit is not launched. An owner-identity or inventory refusal is sticky until the next
`session_start` (startup, `/reload`, or a new session); a registered-but-inactive `subagent` tool is
refused on each use and admitted again once it is active. A `PI_SUBAGENT_CHILD=1` process is never a
parent owner. A malformed or unavailable inventory is an unobservable owner, never an exception out
of `session_start`.
The runtime is recorded once, with the run, at `StartRun` (`invocation.host_runtime`) and re-proved
at audit admission: the private `audit_prepare` carries the runtime the adapter observes now, and the
Python application refuses closed (`audit_refused`, before any reservation or state write) when it is
not exactly the recorded one, which the adapter turns into the same sticky `owner-changed` refusal.
A run therefore cannot be audited by a different pi-subagents than the one its receipt will name: after
a Pi or pi-subagents upgrade, start a new `/empirica` run.

## Governed initialization

4.0.0 adds exact author-sized run-configuration approval before investigation. The author chooses
all three ceilings from the task and selected graph and supplies the bounded rationale; this adapter
never chooses ceilings. Deliberative mode uses `ctx.hasUI` and one documented `ctx.ui.custom()`
component for every change; cancel/no custom UI fails closed. ↑/↓
moves through controls, digits edit budgets, ←/→ chooses the action, Enter
activates it, and Esc dismisses. Approval covers
budget ceilings and control mode while showing the goal read-only; graph content and reviewer
configuration are not approvable. The review renders the escaped rationale as **Agent sizing
rationale — unverified**. A human amendment changes ceilings only and opens a read-only locked
confirmation showing original versus amended values plus the warning that the rationale was not
regenerated. Interactive `--auto` opens this same dialog exactly once before investigation, then
accepts only non-raising revisions without another dialog. Missing UI never downgrades to delegation.
Delegated auto requires recorded operator provenance and remains inside 8/1/2. See
[governance](../../skills/empirica/references/governance.md) for limits, identity classes,
and fresh-generation compatibility. These UI flows have fast simulated-control coverage and
real-service governance coverage; the historical profile receipt does not certify native human
approval for 4.0.0. Follow the operator-led procedure printed by `make native-qualification` for
that boundary.

## Surface

| Pi surface | v2 operation | Behaviour |
|---|---|---|
| `/empirica <goal>` | `StartRun` | Starts a durable run, persists the opaque handle, and injects public-tool guidance. Refuses before creating a run when no verified external pi-subagents owner of the `subagent` tool is active, because no independent audit could launch (contract reason `host.subagents_*`). |
| `empirica_observe` | `ObserveAction` | Accepts only canonical public author kinds. Trusted kinds are rejected locally and by schema. |
| `empirica_read` | `GetRun`, `GetArgument`, `GetContract`, `RestoreRun` | Returns a deterministic plain-text author view; resolves a session handle when needed. |
| `report_convergence` | `EvaluateRun(report_convergence | stop)` | Fails closed unless the guarded response is `Allow`; `intent: stop` records an honest non-converged terminal. |
| `tool_call(subagent)` | `child_reserve` + private lifecycle | Binds the canonical auditor, forces foreground execution, injects the dossier, and records launching/pending facts. |
| `tool_result(subagent)` | private `audit_identity` + `audit_verdict` | Correlates by `toolCallId`, redacts before the first await, binds the verdict to the final native assistant record in the host-generated child session, and admits only one exact fenced verdict. |
| compaction | `RestoreRun` | Carries the opaque handle and restores the selected run. |

The canonical auditor has no plugin model pin. The adapter chooses the project or user scope that
resolves this package's own auditor, then derives the execution model from host settings in order:
`subagents.agentOverrides["empirica.empirica-auditor"].model`, `subagents.defaultModel`, then the
main model. It rejects an unavailable reviewer or one in the same identity class before launch,
and rejects shadowed agent definitions and author-supplied overrides. `EMPIRICA_PI_AUDITOR_MODEL` no longer configures
the auditor.
The adapter never trusts `details.results[].model`, which is requested launch configuration.
Instead it reads the exact result row's host-generated `sessionFile` and accepts identity only when
the final native assistant record carries concrete provider/model fields and its sole verdict equals
the admitted tool-result verdict. Missing, malformed, oversized, changed, or ambiguous sessions
remain unverified and block convergence.

The adapter explicitly disables generic writer acceptance gates on this host-owned read-only audit
call. Empirica's bound verdict contract remains authoritative; author-supplied acceptance, model,
context, or tool overrides are rejected before that runtime-owned mutation.

The adapter-private Python subprocess exposes no Pi tool. It is the imperative ingress shell for
host-observed attribution, child events, and audit verdicts. Public tools cannot express these
payloads.

## Canonical audit call

Follow the complete packaged-auditor invocation and rejection rules in
[audit](../../skills/empirica/references/audit.md).

## Hard gate

With a non-null run handle, `report_convergence` permits only a centrally guarded `Allow`.
`Block`, `Inert`, every `Fault`, malformed responses, and transport failures deny. The central
guard enforces `converged=true` iff `run.status=converged`.

Two Blocks are the exception and settle nonterminally: the sole human-approval wait (ADR-63) and
the sole `budget.exhausted` blocker of an active run (ADR-64 interim). Pi permits the call, prints
the contract-owned notice (`settlement_notices`) ahead of the preserved Block, keeps the run
active, and opens no dialog and makes no private call. Mixed reasons and terminal runs still
deny.

Pi has no native completion veto; the model must call `report_convergence` before making a
convergence claim. A turn can otherwise finish without a terminal decision.

## Validation

Run `make check-pi` for fast deterministic adapter coverage. Run the targeted diagnostics in
`doc/testing.md` when changing persistence or full host-flow boundaries. Profile qualification is
operator-led via `make native-qualification`; release promotion additionally requires
`make empirica-host-live-check` with an honestly applicable retained installed-Pi receipt.
