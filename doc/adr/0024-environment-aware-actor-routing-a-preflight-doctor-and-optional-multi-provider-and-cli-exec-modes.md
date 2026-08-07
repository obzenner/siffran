---
number: 24
title: "Environment-aware actor routing: a preflight doctor, and optional multi-provider and CLI-exec modes"
status: accepted
date: 2026-08-05
tags:
  - architecture
  - routing
  - harness
  - verification
  - attribution
  - portability
links:
  - target: 23
    kind: Supersedes
  - target: 20
    kind: Depends on
  - target: 21
    kind: Depends on
  - target: 17
    kind: relatesto
  - target: 13
    kind: relatesto
  - target: 19
    kind: relatesto
  - target: 22
    kind: relatesto
---

# Environment-aware actor routing: a preflight doctor, and optional multi-provider and CLI-exec modes

**Status note:** verified, then **BUILT in plugin 0.5.0**. See *Build outcome* at the end for what
shipped, what deliberately did not, and the two residuals the run reports rather than claims.

The verification plan (below) is fully discharged: all 12 claims evidenced by harness-written spike
records whose gates are real subprocess exit codes, zero residuals, `{"converged": true, "audit":
"passed"}`. Three audit rounds ran on it; **the first FAILED and was right to**, forcing two
corrections and two new claims, all recorded rather than quietly fixed. V10's judgement half was
answered by the human (same-vendor cross-generation audit is acceptable decorrelation) and its
machine-checkable half was then evidenced.

The **build** was audited four times and failed all four. That history is recorded in *Build
outcome* because it is the most transferable thing this ADR produced: each fix was scoped to the
demonstrated failure, and the next auditor found a different instance of the same property — until
the control itself was rebuilt to require executable proof instead of written justification.

## Context and Problem Statement

ADR-23 decided that empirica routes work by **tier and role** (`fast | capable | frontier` ×
`author | spike-runner | auditor`), resolved to concrete models by a per-harness adapter, and that
the workflow must "never name a model". That was the right call for **cost**. It is the wrong
primitive for **audit independence**, and the gap is now measurable rather than theoretical.

Three findings force a revision.

**1. The independence guarantee ADR-20 P6 claims is not currently delivered.** VERIFIED by reading
the plugin: `agents/empirica-spike-runner.md` and `agents/empirica-auditor.md` both declare
`model: opus`. P6 exists so that "the author cannot grade its own work", and ADR-23 says the
auditor "SHOULD differ in model tier". In the shipped configuration the auditor is *the same
weights re-grading their own reasoning*. Nothing in the harness detects this, because — VERIFIED
by grep over `hooks/*.py` — **no hook, evidence predicate, or audit ticket records a model identity
anywhere**. The audit ticket is `{nonce, pass}`; the verdict's `auditor` field is a self-reported
string.

**2. `tier` destroys the property that makes cross-model audit worth having.** `opus-5` and
`gpt-5.6-sol` are both "capable+" and are *not* interchangeable: different training data, different
failure modes, different blind spots. Routing through a tier label collapses them, and the adapter
is free to pick either. What a second model buys is **decorrelated error** — an epistemic property,
not a cost class — and it belongs in the claim, not in a cost ladder.

**3. Models cannot self-report identity.** VERIFIED empirically. Asked "name the model you are"
over a Bedrock Mantle endpoint pinned to `openai.gpt-5.6-sol`, codex answered *"I'm GPT-5.4"* and pi
answered *"I'm ChatGPT, powered by OpenAI"*, while the raw HTTP response carried
`"model":"openai.gpt-5.6-sol"`. **Attribution therefore cannot come from the actor. It must come
from whatever dispatched it.**

Meanwhile empirica must stay **a plugin anyone can install**. It cannot assume this author's
machine: no assuming `pi` exists, no assuming a Bedrock account, no assuming a token-minting
script. A run on a bare Claude Code install must work exactly as it does today.

The problem: how does empirica use a *richer* environment when one is present, stay *honest and
functional* when it is not, and never silently pretend an independence property it did not get?

## Decision Drivers

* **Installability first.** The default path must require nothing but Claude Code and python3. Any
  capability beyond that is opt-in, and its absence is never an error.
* **Audit independence must be real or explicitly disclaimed.** ADR-20 P6 is the plugin's headline
  guarantee; a same-model audit that reports as independent is exactly the overclaim ADR-21
  forbids.
* **Attribution before assignment.** "Assign claim G4 to model X" is decoration unless the run
  records who actually answered. Attribution is the foundation; routing is built on it.
* **Detect, never infer.** A preflight must *check* the machine, not guess from it. Presence of a
  binary is not the same as a working configuration, and "installed" is not "permitted".
* **Preflight must not spend inference.** Probing capability by asking a model a question costs
  tokens, needs credentials, and can fail for reasons unrelated to availability. Version and config
  checks are free and deterministic.
* **No silent provider substitution.** A user who routes through Bedrock for data-governance
  reasons must not have a fallback quietly send their code direct to a vendor.
* **Honest degradation (ADR-21).** Every unavailable capability degrades to a working default *and
  says so in the run's report*.

## Considered Options

* **A. Keep ADR-23 as-is (tier/role only).** Rejected: it cannot express "audit with a different
  model than the author", which is finding 1. It also has no place to record attribution, so the
  independence claim stays unverifiable.

* **B. Hard-code a fleet (haiku / sonnet / opus-4.8 / opus-5) in the plugin.** Rejected: model IDs
  churn (ADR-23's own reasoning, still valid), and it bakes one vendor's ladder into a plugin others
  install. It also cannot express cross-vendor.

* **C. Actor as a first-class claim field, with a preflight doctor and two opt-in modes (chosen).**
  The claim names the actor; a preflight records what the machine can actually reach; two modes gate
  everything beyond the baseline.

* **D. Full CLI-dispatch rewrite (all actors invoked as `-p` / `exec` subprocesses).** Rejected *as
  the default*, accepted as an opt-in mode (Mode B). It is architecturally attractive — attribution
  becomes dispatcher-witnessed, verdicts get schema-validated. The reason for not making it the
  default was that it appeared to break the ADR-17 spawn budget; **V4 has since shown the
  enforcement boundary survives** (a `PreToolUse:Bash` gate can deny and charge the same ledger), so
  the objection is narrower than it looked — see Consequences. It stays opt-in because it adds three
  credential paths and per-claim process latency, not because the budget is lost.

## Decision Outcome

Chosen: **option C** — actor identity as claim data, a non-inferential preflight doctor, and two
independently-toggled optional modes, both **OFF by default**.

### 1. Actor is a first-class field on a claim

A claim may name the actor that must resolve it. Absent an `actor`, the claim resolves the way it
does today (session default / agent-definition model), so existing graphs keep working.

```json
"G4": {"type": "Goal", "kind": "needs-experiment",
       "actor": {"harness": "claude-code", "model": "claude-opus-5"}}
"G7": {"type": "Goal", "kind": "needs-data",
       "actor": {"harness": "pi", "provider": "bedrock-mantle-openai",
                 "model": "openai.gpt-5.6-sol"}}
```

`tier` survives as an **optional fallback** for claims that genuinely do not care, not as the
routing primitive. This is the part that supersedes ADR-23.

### 2. Attribution is recorded, and it comes from the dispatcher

The in-toto predicate (ADR-22) gains an `actor` object, and the audit ticket records the actor
declared at dispatch. Because finding 3 proved a model cannot report its own identity, the value is
written by **whatever invoked the actor**, never by the actor.

Trust level, stated exactly (ADR-19 G3 / ADR-21):

| Dispatch path | Attribution strength |
|---|---|
| CLI-exec mode — empirica invokes the process | **Dispatcher-witnessed.** empirica chose the model, so it knows. Provider response metadata (e.g. a response id) may corroborate |
| In-session `Agent` spawn (default) | **Declared, not witnessed.** `spawn_gate.py` sees `subagent_type`; the model resolves from agent frontmatter *after* the hook fires. Same trust level as a citation being true |

An attribution empirica did not witness must be reported as declared, never as proven.

### 3. The gate checks assignment against attribution — reporting first

Three checks, in ascending order of how much trust they require:

1. **Mismatch:** a claim assigned to actor X whose evidence is attributed to actor Y → report.
2. **Same-actor audit:** the audit's actor equals an approved claim's actor → report that
   independence was **not** obtained. This is the check that makes finding 1 visible.
3. **Blocking** on either: **deferred.** Following the P1 precedent
   (`convergence_gate.py` reports `p1_violation` on the allow path rather than wedging the run), a
   signal that rests on a *declared* field must not be the sole reason a run fails closed. Blocking
   becomes appropriate once attribution is dispatcher-witnessed for the path in question.

### 4. `empirica doctor` — a preflight that detects, and never infers

Runs at run-start, writes `<run_dir>/actors.json`, and **spends no inference**. Rules:

* **Detection is version/config only.** `--version`, `--help`, a config read, a `doctor`
  subcommand. Never a model call. A tool that is installed but unconfigured is reported as
  unconfigured, not available.
* **Baseline is never gated.** Claude Code + python3 is the only hard requirement, and it is
  present by construction if the plugin is running.
* **Optional tools are only probed when their mode is enabled.** With multi-provider mode off,
  `codex` and `pi` are not probed at all — no reason to inspect a user's machine for a feature they
  did not turn on.
* **Available ≠ permitted.** A detected tool whose provider is unknown or unapproved is reported as
  `configured-but-unapproved`, and never selected by default. VERIFIED motivation: this author's
  `codex doctor` reported `default model provider openai / auth mode chatgpt` — installed and
  working, but routing direct to a vendor rather than through Bedrock, which for a user who
  excludes models on data-retention grounds is a *different decision*, not a detail.
* **Output is a recommendation, not an action.** The doctor may say "a cross-vendor auditor is
  available — assign it?" It never reassigns a claim on its own.

### 5. Two optional modes, both OFF by default, independently toggled

**Mode A — multi-provider.** Enables actors outside the host harness (`pi`, `codex`). When off: no
probing, no external dispatch, and empirica behaves exactly as 0.4.x does.

**Mode B — CLI-exec.** Dispatch actors as non-interactive subprocesses (`claude -p`, `pi -p`,
`codex exec`) instead of in-session spawns. Buys dispatcher-witnessed attribution,
schema-validated output, and per-claim session continuity. Costs the spawn budget (see
Consequences). Off by default until that is resolved.

The modes are orthogonal: Mode B with Mode A off means dispatching *Claude* models as CLI
subprocesses — the cheapest way to get witnessed attribution with no external dependency, and
probably the right first increment.

### 6. Session continuity, deterministically derived

Each (run, claim, actor) gets a stable session id derived as
`uuid5(NAMESPACE_URL, f"empirica:{run_id}:{claim_id}")`. Deterministic, so ADR-19's "hooks must not
use randomness in a resumable run" holds. UUID form because — VERIFIED — `claude --session-id`
requires a valid UUID, while `pi --session-id` and `codex exec resume` accept arbitrary strings or
thread names. One derivation satisfies all three.

### 7. Fable is excluded, by policy

`fable` is an accepted value of Claude Code's `model:` frontmatter field. empirica must not select
it. Reason recorded so it is not "cleaned up" later by someone who sees an unused tier: **data
retention — fable records and stores content at Anthropic for 30 days**, which is incompatible with
users who route inference through their own tenancy for governance reasons. A policy exclusion, not
a capability judgement.

## Findings of record — VERIFIED

Checked on this machine, 2026-08-04/05. Recorded so the next agent need not rediscover it, and
because ADR-21 requires claims about a harness to be grounded in a real read rather than recall.

**Structured output — all three CLIs support it natively:**

| CLI | Flag | Notes |
|---|---|---|
| `claude` | `--json-schema <schema>` | plus `--output-format json` (requires `--print`) |
| `codex` | `--output-schema <FILE>` | plus `-o/--output-last-message <FILE>`, `--json` for JSONL events |
| `pi` | `--mode json` | |

This matters: the current auditor hand-writes `audit-verdict.json` and the gate hopes it parses. A
schema at the boundary removes that class of failure. For contrast, `searlsco/prove_it` greps
`PASS`/`FAIL` out of prose.

**Session persistence:**

| CLI | Flag | Semantics |
|---|---|---|
| `claude` | `--session-id <uuid>` | **must be a valid UUID**; also `--resume`, `--fork-session` |
| `codex` | `codex exec resume <SESSION_ID\|thread-name>` | `--last` for most recent |
| `pi` | `--session-id <id>` | arbitrary string, **creates if missing** |

**pi session continuity was live-tested, not assumed:** with `--session-id empirica-probe-0001`
against `openai.gpt-5.6-sol`, a token planted in call 1 (`PINEAPPLE-7719`) was recalled verbatim in
a separate call 2. The first call emitted `Warning: No project session found with id '…'; creating a
new session with that id` — the create-if-missing behaviour a per-claim session needs.

**Cross-vendor reachability through Bedrock:** `pi --list-models` lists
`bedrock-mantle-openai / openai.gpt-5.6-sol` (400K context, thinking; images no) among 159 models.
Endpoint `https://bedrock-mantle.us-east-1.api.aws/openai/v1`, wire API `openai-responses`. A
direct `POST /responses` returned HTTP 200 with `"model":"openai.gpt-5.6-sol"`.

**codex supports Amazon Bedrock natively.** Found in the compiled binary: auth mode
`"type": "amazonBedrock"` / `bedrockApiKey`, a dedicated
`app-server/src/request_processors/bedrock_auth.rs`, types `ModelProviderAwsAuthInfo` and
`WireApi`, and a region check naming Mantle. Custom providers are configured with `base_url`,
`wire_api` (`"responses" | "chat"`), `env_key`, `env_key_instructions`, `requires_openai_auth`,
`http_headers`, `query_params`.

**codex was configured for Bedrock Mantle in this session and verified.** Provider block in
`~/.codex/config.toml`; profile in a **separate file** `~/.codex/bedrock-sol.config.toml` — codex
0.146.0 rejects `[profiles.*]` tables in the main config with an explicit error telling you to move
them out. Two controls proved the routing is genuine: with the token unset it demanded
`BEDROCK_MANTLE_API_KEY`; with a bogus token it failed against `bedrock-mantle.us-east-1.api.aws`,
**not** against OpenAI — so it does not silently fall back to ambient ChatGPT auth. Both CLIs then
answered a "hello" prompt successfully.

**Auth is a short-lived bearer token, not a static key.** pi's `apiKey` value is
`"!$HOME/Desktop/code/pi-config/bin/mint-bedrock-token.sh"` — a shell-out that mints a Bedrock token
from ambient AWS credentials (≤12h; nothing secret stored). Any adapter must therefore support
*minting per invocation*, not reading a fixed secret. This is user-specific and must never be
assumed by the plugin.

**Per-actor effort is available:** Claude Code subagent frontmatter takes
`effort: low|medium|high|xhigh|max`; pi takes `--thinking off|minimal|low|medium|high|xhigh|max`;
codex takes `model_reasoning_effort`. Free strictness for an auditor without changing model.

**Prior art** (research pass over ~40 frameworks, assurance-case tooling, and the attestation
standards):

* **No existing system combines derived claim state + digest-bound evidence + cross-model audit with
  recorded attribution.** The category search returned nothing.
* **`Proof-or-Stop`** (arXiv:2607.14890, 16 Jul 2026) is **independent convergent invention of
  empirica's core thesis** — verified by fetching the abstract: it *"treats agent outputs as claims
  rather than lifecycle state"* and permits transitions *"only when fresh,
  tracked-source-state-bound, mechanically verifiable evidence satisfies the relevant gate"*, citing
  GSN, in-toto, SLSA and W3C PROV. Its GitHub org holds only an empty repo, so the implementation is
  not locatable; its "cross-vendor" claim is a human-review-layer review, not a gate. **Abstract
  verified; contents not read** (see V9).
* **Why nobody built this:** OMG SACM v2.3 defines
  `assertionDeclaration:AssertionDeclaration[1] = asserted` — a **stored, defaulted** attribute,
  changed by setting a value. Confirmed by extracting the specification PDF directly. Every
  SACM-conformant GSN tool inherits a writable status field, which is exactly what ADR-22's derived
  state removes. **ADR-22's vocabulary-only decision is retroactively load-bearing, not
  fastidious.**
* **Nearest misses:** `moai-adk` has the Stop hook and role split but its evidence gate is
  explicitly advisory ("does not block, outputs only to stderr"); `searlsco/prove_it` has real
  blocking, per-task models and genuine cross-vendor review but **records no model field**, so
  attribution is config-only; `ratifylabs/evidence-gate` greps prose and concedes an agent can type
  "exit code 0"; `OntoGSN` genuinely derives validity but has no LLM integration.
* **Worth adopting** (ADR-22's standards-over-invention rule): **MLflow `AssessmentSource`** —
  `source_type ∈ {HUMAN, LLM_JUDGE, CODE}` plus `source_id="gpt-4o-mini"`, attached to the
  assessment itself. That is actor-type and actor-identity in two fields, verified in MLflow source.
  Prefer it to a bespoke `actor` schema. CycloneDX CDXA's
  `claims[]`/`evidence[]`/`counterEvidence[]` is the nearest published claim vocabulary but puts no
  digest on evidence.
* **The blocking Stop hook is commodity** (Claude Code, OpenHands, Goose, Factory, Devin all honour
  exit 2). The unclaimed ground is not "can you name a different judge" — 13 of 20 judge frameworks
  allow that — but **"can the run prove it was judged by a different model, and be blocked from
  claiming success if it can't?"**
* **Low-credibility cluster, do not build on:** there is no SLSA ML/AI track; the AI-agent proposals
  (in-toto #554/#565, SLSA #1594) are unmerged, and #1594 was closed by a maintainer citing
  AI-generated noise.

## Verification plan — RESOLVED

**Status: discharged.** The plan below was run as an empirica claim graph
(`.claude/empirica/2bff45e3c6166af2/`, branch `verify/adr-24-claims`) — empirica adjudicating its
own design ADR. Every claim's Fold-2 gate is a real subprocess exit code written by
`spike_harness.py`; two independent audits ran, the first FAILED, and the corrections it forced are
recorded below rather than hidden.

**Outcome: 12 approved, 0 residuals.** The Stop gate reports `{"converged": true, "audit": "passed"}`.

| # | Verdict | What the evidence showed |
|---|---|---|
| V1 | **approved** | `claude --session-id <uuid5>` creates the session; a second `--resume` call recalled the planted token. Per-claim continuity works, and the derived-uuid5 scheme of §6 is sound |
| V2 | **approved** | `--json-schema` constrained output exactly (required keys, enum, no extras). **The flag takes the schema INLINE, not a path** — a path yields `Error: --json-schema is not valid JSON` |
| V3 | **approved** | `codex exec --output-schema` (a **path**, unlike claude's inline flag) constrained the verdict object. ~8s end to end |
| V4 | **approved** | **The blocker clears.** The current gate does not cover Bash dispatch, *and* a `PreToolUse:Bash` gate can deny by exit 2, charge the same ADR-17 ledger, and still allow unrelated Bash. Mode B's budget is recoverable |
| V5 | **approved** | pi held four turns and answered a question requiring turns 1 and 3, correctly excluding turn 2 |
| V6 | **approved** | `codex doctor` is regex-parseable; derived `configured-but-unapproved (openai)` on this machine |
| V7 | **approved** | Provider attests `openai.gpt-5.6-sol` while the model self-reports *"I'm OpenAI's ChatGPT"*. **Attribution must come from the provider/dispatcher** |
| V8 | **approved** | No model field in the documented payload schema, none in six live Agent payloads, and `spawn_gate.py` probes five spellings of the agent-type key. In-session attribution cannot be witnessed — which is Mode B's reason to exist |
| V9 | **approved** (reworded) | The paper *asserts* an open-source implementation, names `github.com/Proof-or-Stop` and an `arxiv-v1` tag — but that repo 404s and the org holds only a `.github` metadata repo. Design must be read from the paper |
| V10 | **approved** (split) | Human decision: same-vendor cross-generation audit is acceptable decorrelation. The machine-checkable half is evidenced — `us.anthropic.claude-opus-4-8` and `us.anthropic.claude-opus-5` are distinct inference profiles, both invoke via converse, and a nonexistent generation is rejected, so addressability is real |
| V11 | **approved** (new) | §3 demonstrated: `actor` is additive (leaf still validates, claim still approves), same-actor audit is detected, and a different actor produces no false positive |
| V12 | **approved** (new) | §4 demonstrated: three distinct statuses from non-inferential reads alone — pi `permitted`, codex `configured-but-unapproved:openai`, nonexistent CLI `absent` |

**What the first audit caught, recorded because the corrections matter more than the pass:**

* **V3 was falsely blocked.** It was tagged `needs-experiment` and blamed on codex latency. The
  auditor reproduced the spike in 8.24s. The real cause was a missing `BEDROCK_MANTLE_API_KEY`
  export in that subprocess — a misdiagnosis, not a design limit. Corrected and evidenced.
* **V9 stopped at the abstract.** The full PDF says considerably more, including a repo and tag that
  do not resolve. The claim was reworded (which invalidates its evidence digest by design) and
  re-evidenced.
* **G0 overclaimed.** It asserted "ADR-24's design is buildable as specified" on the strength of
  *preconditions only* — nothing tested §3's attribution check or §4's doctor. **V11 and V12 exist
  because of that finding**, and G0 was narrowed to what the evidence supports.
* **P1 is witnessed only vacuously here.** `first_tool_ts`/`first_tool_seq` are null despite real
  investigation, so the ordering check passed via its "no investigative tool call recorded yet"
  branch. Both auditors flagged it. The second traced it to substance — `route_ts` precedes the
  earliest evidence by ~15 minutes, and the tool calls ran inside spawned subagents that do not
  attribute to the parent manifest. **A harness-witnessing gap, not a violation by this run**, and a
  candidate defect for its own investigation.
* Informational, not verdict-affecting: V7's identity-disagreement assertion is a single-substring
  test — correct here, fragile in general.

**Two implementation facts the plan did not anticipate**, both of which a port must handle: the two
schema flags take *different* argument forms (claude inline, codex a path), and the Bedrock bearer
token is short-lived, so an adapter must mint per invocation rather than read a fixed secret.

The original plan, for the record:

| # | Claim | Owes | Why it blocks building |
|---|---|---|---|
| V1 | `claude --session-id <uuid>` **creates** a session when none exists, as pi does, rather than erroring | Fold 2 spike | Per-claim session continuity in Mode B depends on it; unknown today |
| V2 | `claude -p --json-schema` output actually validates against a supplied schema, including on refusal and error paths | Fold 2 spike | The verdict boundary rests on it |
| V3 | `codex exec --output-schema` likewise | Fold 2 spike | Same, for the codex adapter |
| V4 | A spawn budget can still be enforced under Mode B | Fold 1 + Fold 2 | **The blocker.** `spawn_gate.py` denies at `PreToolUse` on `Agent`; a `Bash` call to `pi` is not an `Agent` spawn, so ADR-17's cap silently stops applying. Mode B must not trade away a real guarantee without saying so |
| V5 | pi session continuity holds for a realistic auditor transcript, not a three-word token | Fold 2 spike | Only tested with `PINEAPPLE-7719` at trivial length; 400K-context behaviour unknown |
| V6 | `codex doctor` output is machine-parseable enough to answer "configured for which provider?" | Fold 1 | The doctor's available-≠-permitted rule depends on reading it reliably |
| V7 | Whether provider response metadata (e.g. Mantle's `response.id`) can corroborate attribution | Fold 1 + Fold 2 | Decides whether Mode B attribution is *witnessed* or merely *dispatcher-declared* |
| V8 | Claude Code exposes no way for a hook to observe a subagent's resolved model | Fold 1 | If false, in-session attribution could be witnessed too, and Mode B's main advantage shrinks |
| V9 | `Proof-or-Stop`'s actual contents, and whether an implementation exists | Fold 1 | Adopt rather than reinvent; an abstract is not enough |
| V10 | Whether `opus-4.8` vs `opus-5` decorrelation is meaningfully better than same-model | — | Probably **not** empirically resolvable here; likely a `needs-decision` residual for the human. Record it rather than invent a number |

**Cheap steps that do not depend on the plan** (safe to land first, no mode required):

1. Document the fable exclusion with its reason.
2. Split the agent-definition models so the auditor is not the author's weights: researcher →
   `haiku`, spike-runner → `claude-opus-5`, auditor → `claude-opus-4-8` with `effort: xhigh`. Pair
   with a test asserting auditor ≠ spike-runner, so the independence cannot silently regress the way
   it already did.
3. Add `actor` to the evidence predicate as a passthrough field — additive, and every later step
   needs it.

## Consequences

**Good:**

* ADR-20 P6 becomes checkable rather than nominal: the run can report whether independence was
  actually obtained.
* The plugin stays installable — baseline behaviour is unchanged, and optional tooling is only
  probed when asked for.
* Attribution moves toward witnessed under Mode B, which is the one genuinely unclaimed property
  found in the whole prior-art sweep.
* Schema-validated verdicts remove a hand-written-JSON failure mode.
* Per-claim sessions mean an auditor re-examining a claim at pass 3 retains passes 1–2 instead of
  starting cold.

**Bad, stated plainly:**

* **Mode B's effect on ADR-17 — resolved by V4, and it is better than feared.** The concern was
  that the spawn budget, enforced today at a boundary the model cannot cross, would decay into
  convention-plus-visibility inside the dispatcher. V4 showed the enforcement boundary **survives**:
  a `PreToolUse` hook matched on `Bash` receives the payload, can deny by exit 2, and can charge the
  same `budget.py` ledger — so a CLI-dispatched actor is gated at the same boundary as an `Agent`
  spawn. The residual cost is narrower than "a downgraded guarantee": the gate must now recognise
  *which* Bash commands are actor dispatches, so coverage depends on a command test rather than on a
  tool name. A dispatch spelled unusually would slip past. That is a real limitation and it must be
  documented at the point of implementation, but it is not the loss of an enforced boundary.
* More surface: three CLIs, three credential paths, no unified spend view.
* Latency: process startup per claim instead of an in-session spawn.
* The doctor adds a step that can itself be wrong — a false "unavailable" silently narrows routing —
  so its output must appear in the run report, not only be consumed internally.
* `actor` in a hand-written graph is still forgeable, like every other artifact (ADR-19 G3). Nothing
  here changes the file-level trust model.

## More Information

* Supersedes **ADR-23**'s routing primitive (tier+role → actor identity); keeps its cost reasoning
  and its no-hard-coded-IDs rule for *tiers*.
* Depends on **ADR-20** (P6 independence, P3 two folds) and **ADR-21** (harness honesty,
  no-overclaim).
* Relates to **ADR-17** (spawn budget — see the Mode B consequence), **ADR-13** (agentic review may
  block but never approve: a cross-model auditor is still a veto, never the approver), **ADR-19**
  (determinism in hooks; run identity), and **ADR-22** (standards over invention — prefer MLflow's
  `AssessmentSource` shape to a bespoke one).
* A defect found while investigating this area is fixed in PR #10, merged as `127eb43` (the P1 route
  stamp self-trip). Unrelated to this decision, but it is why the P1 report-don't-block precedent is
  cited above.

## Build outcome — IMPLEMENTED in 0.5.0

This ADR is built. The run is recorded at `.claude/empirica/2a2036d1a18fee8c/` on branch
`verify/adr-24-claims`, and **four independent audits failed it before this section was written.**
That history is the most useful thing here, so it is recorded rather than smoothed over.

**Delivered and wired into running code:**

| § | Mechanism | Where |
|---|---|---|
| 1 | `actor` on a claim node — additive, absent ⇒ unchanged behaviour | `claimgraph.py`, `actors.py` |
| 2 | `actor` on the in-toto predicate; dispatcher-side attribution on the audit ticket | `evidence.py`, `spawn_gate.py`, `audit.py` |
| 3 | mismatch + same-actor checks, reported on the Stop gate's allow path | `attribution.py`, `convergence_gate.py` |
| 4 | `empirica doctor` preflight, non-inferential, run at run-start (`make doctor`) | `doctor.py`, `run_start.py` |
| 5 | both modes off by default, independently toggled | `modes.py` |
| 5B | `PreToolUse:Bash` dispatch gate charging the ADR-17 ledger | `dispatch_gate.py` |
| 6 | `uuid5` per-claim sessions, used by the gate's cold-start advice | `actors.py`, `dispatch_gate.py` |
| 7 | `fable` refused at the single normalisation choke point | `actors.py` |

**Deliberately NOT delivered:** the per-CLI adapters Mode B enables (they would be unreachable code
behind an off-by-default flag), and blocking on a §3 finding (§3.3 defers it until attribution is
witnessed).

**The defect that had to be fixed first.** The "second known defect" recorded above — `run_start.py`
apparently not firing — was **misdiagnosed here.** It did fire. `/empirica` was invoked while the
session's cwd was `<repo>/plugins/empirica`, so the manifest was written under that subdirectory,
while every later hook fired from `<repo>` and derived a different `run_id`. The docs define `cwd` as
"Current working directory when the hook is invoked" and ship a `CwdChanged` event, so **run identity
was keyed on a moving value.** Identity, the run directory and the spawn ledger now anchor to the
project root. Recorded as a correction rather than edited away: the symptom-for-cause substitution is
the same failure mode ADR-21 exists to prevent, and it happened in this document.

**What four audits cost, and bought.** Each audit failed the run and each found something real:

| Audit | Found |
|---|---|
| 1 | 8 findings, including that the fix's test read the working tree while the harness resolved an *installed* copy where auditor and author were still the same model |
| 2 | 4 mutations that left the suite green, **plus** that audit 1's fix had become vacuous |
| 3 | 6 more, **plus** that audit 2's fix made the extra roots structurally incapable of gating |
| 4 | 4 of the sweep's written excuses were factually false, 11 mutations were silently dropped, and an advertised operator was unimplemented |

The pattern matters more than the count: **each fix was scoped to the demonstrated failure, and the
next auditor found a different instance of the same property.** Audit 3 diagnosed it — a curated
sabotage table's coverage is exactly its author's imagination — and audit 4 diagnosed the sequel: a
generated sweep plus a hand-written excuse list is a curated control one indirection out. The control
is now a generated sweep in which **every survivor must supply an executable witness of
observational equivalence**; a survivor that cannot prove itself harmless fails the spike. That is
ADR-13's rule (the exit code approves, not the author) applied to the plugin's own test harness.

**Two residuals, surfaced not claimed.** The run reports `converged: false`:

* **P1** — this run's route ordering is a genuine violation. `first_tool_seq` was claimed before
  `route_seq` because the session's manifest predated the build's route announcement, and
  first-write-wins makes it uncorrectable. Reporting it clean would be the laundering the gate exists
  to prevent.
* **P6** — audit independence is **unverifiable for this run**, and the plugin's own new §3 check is
  what says so. Every auditor resolved from the installed 0.4.1 copy, where both roles declare
  `model: opus` and whose `hooks/` contains no ADR-24 modules at all. The dispatcher-side attribution
  is therefore a tier alias, which `coverage` correctly reports as UNMEASURED. The auditors
  self-reported being the same model as the author; that reading is *not* recorded as fact, because
  finding 3 forbids taking a model's word for its own identity — even when the suppressed reading is
  the more damaging one. This resolves once the fix ships and the plugin is reinstalled.

**Non-obvious implementation facts worth keeping:** a stale *installed* plugin copy cannot be fixed
by the commit that fixes the defect, so `test_hooks.py` gates tree defects and *warns* about install
staleness — a check that can only go green after release would block its own fix and then be deleted.
And the two schema flags take different argument forms (claude inline, codex a path), while the
Bedrock bearer token is short-lived, so an adapter must mint per invocation.

## 0.5.1 — P6 obtained, and the plugin finds a defect in itself

Re-running the workflow under `--plugin-dir` (session-scoped, so the tree's definitions are what the
hooks resolve) closed the second residual and produced one new finding.

**P6 is now obtained.** The spawn gate recorded `model: claude-opus-4-8, is_tier: false` on the audit
ticket, resolved from the tree's 0.5.0 auditor definition — a *different generation* from the Opus 5
author, which is the decorrelated error this ADR argues a tier cannot buy. Two caveats stay on the
record: the attribution is `declared`, not witnessed (§2 — `cli_exec` is off, so empirica did not
dispatch the auditor itself), and the suite's staleness `warn()` still fires because it scans on-disk
marketplace copies and *cannot observe `--plugin-dir` shadowing*. Declared-consistent, not proven.

**V5 — `dispatched_harness` over-detected, and the cost was a denial, not an over-count.** Detection
scanned every token for a known actor-CLI name, so `echo claude -p` and `grep claude -p file`
classified as dispatches. A positive charges the ADR-17 ledger and, at the cap, `main()` returns 2:
an innocent Bash command DENIED. Detection now matches only in **command position**, still skipping
env-assignment prefixes and transparent wrappers (`env`, `timeout`, …) because those were what the
original all-tokens scan was actually needed for.

The regression test is deliberately **two-sided**. Narrowing to `tokens[0]` would kill over-detection
while breaking every prefix case earlier audits added, and an under-detection lets a real dispatch go
uncharged; whichever direction the author happens to be chasing is the one that gets tested, so both
are asserted. Verified by reverting the fix: the suite fails 584/587.

Worth stating plainly, because it is the point of the harness: **this defect was found by empirica
auditing its own implementation** — a falsification control that refused to assert the buggy value as
correct surfaced it, and the auditor then bounded the severity better than the author had (`main()` is
inert unless `cli_exec` is on, so 0.5.0 shipped no baseline regression; the fix matters before Mode B
lands). The remaining residuals are **P1** (uncorrectable, above) and the undelivered Mode B adapters.
