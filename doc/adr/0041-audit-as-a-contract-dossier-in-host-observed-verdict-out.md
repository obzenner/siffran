---
number: 41
title: "Audit as a contract: dossier in, host-observed verdict out"
status: proposed
date: 2026-09-12
tags:
  - audit
  - contract
  - independence
  - pi
links:
  - target: 20
    kind: Amends
  - target: 24
    kind: Depends on
  - target: 25
    kind: Depends on
  - target: 27
    kind: Depends on
  - target: 30
    kind: Depends on
  - target: 39
    kind: relatesto
  - target: 40
    kind: Amends
---

# Audit as a contract: dossier in, host-observed verdict out

## Context and Problem Statement

ADR-20 P6 makes an independent audit a necessary condition for convergence: the author cannot grade
its own work. ADR-25/27 bind a verdict to the argument it reviewed with per-claim and argument
digests that the gate recomputes from disk, and ADR-24 records *who* audited with a ticket nonce
issued at spawn. Every other Empirica boundary went through the `empirica/v1` contract (ADR-30) and,
since ADR-39, carries the obligation contract losslessly. The audit did not.

The first live Pi dogfood run (`doc/design/reports/dogfood-pi.md`, P-8..P-10) made the coupling
visible. The auditor spawned by the dev agent (Claude Opus 5, a different vendor from the GPT-5.6
author) had no way to read the argument it was asked to audit: `RestoreRun` returns gating *counts*
(`application/service.py` `_restore_graph_view`), and the auditor definition told it to obtain the
digests "from the knowledge view and its digest helpers" (`agents/empirica-auditor.md`, previous
revision) — which on Claude Code meant running the plugin's Python in-process against the store. It
went hunting for the artifact by content hash, ran `grep -rl <hash> / --include='*.json'`, hung for
four minutes, and was stopped. The fallback auditor reported "missing citation context". Three spawns
and three ticket nonces were consumed without a verdict.

Reading the mechanism against that transcript exposed four defects, three of them pre-existing on
every host:

1. **No wire view of the argument.** Nothing in `empirica/v1` answered "what must I review?" The
   auditor's inputs were a host-side Python convenience, not a contract operation.
2. **Verdict submission was host-specific.** The definition named
   `adapters.claude.knowledge.build_audit_verdict_request` through `BridgeTransport`.
3. **The nonce went to the wrong principal.** `audit_ticket` returned the nonce to the *author's*
   spawn call (Claude: hook output → Task prompt; Pi: a model-visible message), and the author pasted
   it into the child. The ticket therefore proved that a spawn happened, not that someone other than
   the author wrote the verdict. ADR-20 P6's independence rested on a norm.
4. **Reservation and ticket were spent before the launch was validated.** A `subagent` call that
   pi-subagents rejected (`agent` combined with `workflowScript`) had already consumed a reservation
   and a nonce.

## Decision Drivers

* The auditor must need nothing but the contract: no store access, no host Python, no bridge.
* Independence must be a property the host enforces, not an instruction the author follows.
* The digests an auditor signs must be, by construction, the digests the gate recomputes.
* Same shape on Claude Code, Codex and Pi; host differences live in the adapter, never in the rubric.
* Budget accounting must reflect what actually happened: a child that never ran costs nothing; a
  child that ran and produced nothing usable costs its spawn.

## Considered Options

* **A. Give the auditor Empirica tools** (`empirica_argument`, `empirica_verdict`) and let it call
  `audit_verdict` itself.
* **B. Dossier in, host-observed verdict out** — a read command that renders everything the auditor
  must review; the host injects it (with the nonce) into the child's task at spawn time and extracts
  the verdict from the child's *output*, dispatching `audit_verdict` itself.
* **C. Keep Python-in-process** and ship the Python helpers to every host.

## Decision Outcome

Chosen option: **B**, because it is the only one under which the author never holds the nonce and the
verdict never passes through a model-callable operation. A closes the "how do I read it" gap but
leaves `audit_verdict` callable by whoever holds a nonce — including the author, who receives it. C
is what failed live.

### The contract

* **`GetArgument`** (`application/wire.py`, `service.py` `_get_argument`): an `Allow` whose `run`
  carries `argument = { argument_digest, theta, frozen_claims, claims[], tickets[], text }`. Each
  claim carries `id, text, kind, state, confidence, parent, claim_digest, evidence_digest` and its
  evidence leaves (`evidence_id, fold, kind, citation, result/gate, command, artifact_id`). The
  digests are computed by the same helpers `core.audit.coverage_check` uses
  (`knowledge.build_digest_of`, `claims.argument_digest`), so a verdict assembled *only* from
  `GetArgument` values is accepted by construction — proven end to end in
  `adapters/pi/test/live-bridge.test.ts` against the real bridge. `tickets[]` never carries a
  nonce. `text` is rendered by the application, so no host needs a mirror.
* **`StartRun.actor`** (optional): the author's model/harness/provider, persisted in operational
  state. `audit_ticket` returns `Block` when the auditor's declared model equals the author's
  (`actors.same_actor`; ADR-24 decorrelation becomes a check). A record without a usable model
  degrades to "no author recorded" rather than refusing the run.
* **`ObserveAction(void_spawn){nonce?}`**: releases one reservation (never below zero) and voids the
  named ticket; a void ticket can never satisfy coverage.
* **Verdict output contract**, identical on every host: the auditor returns one fenced block
  tagged `empirica-verdict` containing `{verdict, nonce, argument_digest, claims_reviewed[],
  findings[], ts}` (`agents/empirica-auditor.md`, "Output"). The definition is now host-neutral: it
  forbids touching `~/.empirica-plugin`, `refs/empirica` and the bridge, and it names no adapter.

### The host's obligations (adapter, per host)

1. **Validate the launch shape before reserving.** Exactly one of `agent | workflowScript |
   resume`; management calls (`action: "list"`) pass untouched.
2. **Reserve, ticket, then inject.** For an auditor spawn: `reserve_spawn` → `audit_ticket` (actor
   from the agent definition, attribution `declared`) → `GetArgument` → replace the child's task with
   *rubric + `argument.text` + `Your nonce: <nonce>` + output contract*. The author receives only
   "auditor spawned; verdict is recorded by the host". On Pi the launch is forced foreground
   (`async = false`) so the verdict returns in the same tool result.
3. **Ingest from the child's output, never from the author.** When the ticketed call's result
   arrives, extract the block, dispatch `audit_verdict`, **redact the block** from the author-visible
   result, and append the rendered `run.contract` so the author sees the audit obligation flip to
   `satisfied` (or the reason it did not, with the auditor's findings).
4. **Account honestly.** Launch error → `void_spawn{nonce}` (the child never ran). No usable block →
   nothing released; the ticket simply never gets a matching verdict and the obligation stays open.

**Host status at the time of this record** (verified, not planned):

| Host | Inject dossier + nonce into the child | Host-observed verdict ingest | Status |
|---|---|---|---|
| Pi | `tool_call` rewrites `input.task`, forces `async=false` | `tool_result` of the ticketed call | **Complete**, proven live (`live-bridge.test.ts`) |
| Claude Code | PreToolUse `hookSpecificOutput.updatedInput` rewrites the Agent prompt | `SubagentStop.last_assistant_message` (with transcript fallback) | **Complete**: launch shape is validated before reservation; auditor ticket carries declared Claude actor attribution; `GetArgument` dossier and nonce are injected only into the child; `SubagentStop` host-records the parsed verdict. |
| Codex 0.146.0 | no `updatedInput` in the documented PreToolUse output | `Stop` carries only the parent's `last_assistant_message` | **Blocked by the host API** (`adapters/codex/README.md`, "Auditor round-trip limitation"). Codex can reserve and ticket; it cannot inject or ingest. The audit obligation stays open there. |

On Claude Code the nonce necessarily travels in the hook's `updatedInput` JSON on stdout — that is
the host-native channel for mutating the child's request, read by Claude Code, not shown to the
author model. No diagnostic line on either host prints it. Codex is the one host where the author's
spawn still cannot be rewritten; there the audit obligation simply stays open.

### Consequences

* Good: the auditor is a plain model with ordinary tools. It reads a dossier, follows citations, and
  returns JSON. The Opus 5 failure mode — improvising a way into the store — has no reason to occur.
* Good: independence is mechanical. The nonce is created by the application, delivered by the host
  into the child's task, and consumed from the child's output; the author's context never contains it,
  not even after the fact.
* Good: "a verdict from `GetArgument` values passes coverage" is an executable claim, not prose.
* Bad: the auditor's *output* is now load-bearing text. A host that fails to capture the child's
  final output (detached runs, truncated results) yields no verdict; Pi mitigates by forcing a
  foreground launch, at the cost of blocking the author's turn for the audit's duration.
* Bad: `GetArgument.text` can be large for big arguments; it is injected once per spawn.
* Neutral: `audit_verdict` remains a wire operation. Trust does not come from hiding it but from the
  nonce's path and the recomputed digests; ADR-0040's earlier phrasing ("trusted-only ops are never
  model-callable tools") is replaced by the precise statement: *verdicts enter only through
  host-observed child output; the author never holds the nonce.*

## Confirmation

* `adapters/pi/test/live-bridge.test.ts` — real stdio bridge and Python application: dossier
  injected (rubric, every claim, `claim_digest=`), nonce absent from every author-visible message and
  from the redacted tool result, verdict built only from `GetArgument` accepted, audit obligation
  `satisfied`, `report_convergence` → `Allow`, over-budget second spawn denied with the contract.
* `plugins/empirica/tests/test_application.py` — a verdict built solely from `GetArgument` values
  converges the run; rewording a claim or swapping its evidence leaves the audit residual; tickets
  are nonce-free; `text` names every claim and evidence id; `void_spawn` releases exactly one
  reservation and a voided nonce cannot satisfy coverage **even after the state is reloaded** — the
  first revision of `void_spawn` lost the `void` flag on decode, which this test now pins; same-model
  ticket `Block`, tier-alias and unknown-actor allow; `StartRun` without a model records no author.
* `adapters/pi/test/audit.test.ts` — every interception and ingestion branch with a fake dispatch,
  asserting the exact request sequence, redaction across content shapes, and nonce absence.
* `adapters/claude/tests/test_claude_adapter.py`, `adapters/codex/tests/test_codex_adapter.py` —
  Claude pins launch-shape validation, declared ticket actor, native `updatedInput` injection, ticket voiding,
  host-observed verdict ingestion, and JSONL transcript fallback; Codex pins its payload gap rather than prose.
* `contracts/fixtures/empirica-get-argument.json`, `empirica-void-spawn.json` — generated from the
  real service, validated by `make contract-check`.
* Live: the next `make pi-dev` run reaching the audit step is the acceptance test for this ADR; its
  findings go to `doc/design/reports/dogfood-pi.md`.

## Pros and Cons of the Options

### A. Auditor calls Empirica tools

* Good: smallest change; reuses existing operations.
* Bad: whoever holds a nonce can write a verdict, and the author holds it. Independence stays a norm.
* Bad: the child needs the Empirica extension loaded and a live run handle; fragile across hosts.

### B. Dossier in, host-observed verdict out

* Good: see Decision Outcome.
* Bad: depends on the host capturing the child's output; verdict extraction is text parsing.

### C. Ship the Python helpers to every host

* Good: no new wire operation.
* Bad: this is the design that failed live; it couples every host to the plugin's Python and to
  the store layout, and it still hands the nonce to the author.

## More Information

* Findings and verbatim evidence: `doc/design/reports/dogfood-pi.md` (P-8, P-9, P-10) and the Opus 5
  auditor transcript referenced there.
* Related: ADR-0039 (obligation contract), ADR-0040 (Pi parity; amended here), ADR-24 (actor
  attribution), ADR-25/27 (verdict digests).
