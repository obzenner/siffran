# Lane D — Independent audit (research phase) — Fold-1 evidence + design critique

Lane: D (research, GLM — decorrelated-vendor audit, ADR-24). READ-ONLY: no repo file created or
modified. All repo claims cite `file:line`; all external claims cite a primary-source URL and the
passage that decides them. Anything uncitable is labelled **UNVERIFIED**.

## Stance (verbatim)

> **Stance:** parametric knowledge (training weights) = hypothesis only. Every load-bearing claim
> discharged against evidence (code / docs / runtime) or surfaced as UNVERIFIED. Open questions are
> resolved until blocked, then surfaced with what was tried.

---

## Part 1 — Intellectual lineage (Fold-1 evidence for ADR-0039)

For each item: primary-source URL, the passage that decides it, and one line on how it grounds or
constrains the brief's v1 design (citations to `obligations-contract-brief.md` use section names, not
line numbers, because the brief is uncommitted prose).

### L1 — Design by Contract (Meyer): obligations / benefits
- **Source:** https://en.wikipedia.org/wiki/Design_by_contract
- **Passage:** "The central idea of DbC is a metaphor on how elements of a software system
  collaborate with each other on the basis of mutual *obligations* and *benefits*. … The supplier
  must provide a certain product (obligation) and is entitled to expect that the client has paid its
  fee (benefit). The client must pay the fee (obligation) and is entitled to get the product
  (benefit)." And: "The contract is semantically equivalent to a Hoare triple which formalises the
  obligations." (Term coined by Bertrand Meyer in connection with Eiffel, 1986; OOSC 1988/1997.)
- **Grounds/constrains the brief:** The brief's `Obligation{must, must_not?, witnesses[], because[]}`
  is the DbC obligations/benefits pair lifted to an *inter-agent* boundary: `must` is the next actor's
  obligation (what to cause), `witnesses[]` is the benefit the actor is entitled to demand (the
  discharge observation), `because[]` is the contract's provenance. DbC's premise that a contract is a
  *collaboration interface* (not a test plan) is why the brief's invariant demands projection "at
  every boundary", not just at the verifier.

### L2 — Hoare triples / pre-postconditions as the 'must' shape
- **Source:** https://en.wikipedia.org/wiki/Hoare_logic (Hoare 1969; Floyd 1967 lineage)
- **Passage:** "A Hoare triple is of the form {P} C {Q} … P is named the *precondition* and Q the
  *postcondition*: when the precondition is met, executing the command establishes the postcondition."
  "Using standard Hoare logic, only partial correctness can be proven."
- **Grounds/constrains the brief:** `must` is a postcondition the next actor must establish; a
  `Witness` is the observation that the postcondition holds. "Partial correctness" (correct *if it
  terminates) is the right default for an obligation that is `open` until a witness arrives — matching
  `state ∈ {open, partial, discharged, violated, blocked, deferred}`. The brief's verifier is
  postcondition-checking, not full program proof, which is exactly what keeps it decidable via
  exact-match witnesses rather than a predicate evaluator (the explicit v1 non-goal).

### L3 — Deontic logic: obligation / prohibition (must / must_not) and why prohibitions need observed-violation semantics
- **Source:** https://plato.stanford.edu/entries/logic-deontic/ (SEP, rev. 2021)
- **Passage:** The Traditional Definitional Scheme: "IMp ≡ OB¬p" (impermissible/prohibited = obligatory
  that not-p). The decisive disanalogy with alethic necessity: "If OBp then p (if it is obligatory that
  p, then p is true)" is *false* — "Obligations can be violated, and impermissible things do happen."
- **Grounds/constrains the brief:** `must_not` is prohibition (IM). Because obligations *can be
  violated*, a `must_not` obligation **cannot** be discharged by the absence of an observation
  (absence of evidence ≠ evidence of absence); it can only be **violated** by an observed `pass` of a
  forbidden witness. This is precisely the brief's rule "`must_not` + observed `pass` = violated" and
  that a `must_not` with no observation stays `open` (never "proved-compliant-by-silence"). The deontic
  source is the *reason* the verifier's two buckets `residual` vs `violated` are asymmetric.

### L4 — in-toto attestation Statements as the witness / observation shape
- **Source:** https://raw.githubusercontent.com/in-toto/attestation/main/spec/v1/statement.md and
  https://raw.githubusercontent.com/in-toto/attestation/main/spec/README.md
- **Passage (statement.md):** Schema
  `{ "_type": "https://in-toto.io/Statement/v1", "subject": [{"name":"","digest":{...}}], "predicateType": "", "predicate": {...} }`;
  "Subject artifacts are matched purely by digest"; "Subjects are assumed to be *immutable*"; fields
  use RFC 2119 keywords ("Each element MUST have `digest` set").
  **Passage (README):** Four independent layers — "Predicate … Statement: Binds the attestation to a
  particular subject and unambiguously identifies the types of the predicate … Envelope … Bundle."
- **Grounds/constrains the brief:** The brief's `Observation{kind, ref, outcome, source, at, payload?}`
  is the in-toto Statement shape: a typed assertion (`predicateType` ≈ `kind`) bound to an identified
  subject (`subject[].digest` ≈ `ref`) carrying a verdict (`predicate`/`outcome`). "Matched purely by
  digest" is the lineage of the brief's "Exact match on `(kind, ref)`". The "Statement vs Predicate"
  layering constrains the design: the *type* (`kind`) and the *subject identity* (`ref`) must be
  separable from the *content* (`payload`), so the verifier matches before it interprets.

### L5 — Runtime verification / trace checking (LTL over finite traces, monitors) as the deterministic-verifier lineage
- **Source:** https://en.wikipedia.org/wiki/Runtime_verification and
  https://en.wikipedia.org/wiki/Linear_temporal_logic (Pnueli 1977)
- **Passage (RV):** "Runtime verification is … extracting information from a running system and using
  it to detect and possibly react to observed behaviors satisfying or violating certain properties …
  specifications are typically expressed in trace predicate formalisms, such as … linear temporal
  logics … monitors are synthesized from them … The monitor verifies the received event trace and
  produces a verdict whether the specification is satisfied." RV explicitly generalises semantics to
  "finite trace versus infinite trace semantics."
  **Passage (LTL):** "LTL was first proposed for the formal verification of computer programs by Amir
  Pnueli in 1977"; "safety properties usually state that *something bad never happens* (G ¬ϕ), while
  liveness properties state that *something good keeps happening*."
- **Grounds/constrains the brief:** The brief's `verify(contract, observations, trusted) -> Verdict`
  is a runtime-verification monitor over the **finite** trace of trusted observations, producing a
  verdict. `must_not` (prohibition) maps to a *safety* property ("bad never happens"); `must` maps to
  a *liveness* property ("good eventually happens"). The finite-trace point is load-bearing: on a
  finite prefix a liveness (`must`) obligation can only ever be **unwitnessed**, never "proved
  unsatisfiable" — which is why the verdict has separate `residual` and `unwitnessed` buckets and may
  never report a `must` as *failed* merely because no witness arrived yet.

### L6 — GSN / SACM assurance cases vs obligations (why they are different layers)
- **Source:** https://en.wikipedia.org/wiki/Goal_structuring_notation (GSN Community Standard v3,
  2021; Kelly 1998, York)
- **Passage:** "Goal structuring notation (GSN) is a graphical diagram notation used to show the
  elements of an argument and the relationships between those elements … to present safety cases."
  Criticism, citing Habli & Kelly: "a GSN diagram was just a depiction, not the safety case itself"
  (the Magritte / Treachery-of-Images analogy).
- **Grounds/constrains the brief:** GSN/SACM answers the brief's *top* layer ("What do we know, and
  why may we believe it?") — the claim-graph/evidence layer that already exists in Empirica. The
  Obligation layer answers a *different* question ("What must the next actor cause/preserve/avoid?").
  The brief's layer model keeps them separate on purpose: the claim graph *justifies* an obligation
  (`because[]`), it is not the obligation. This is the lineage for the issue's non-goal "The next agent
  should not have to understand GSN, replay the claim graph" and constrains `project()` to compile
  the graph *down* to obligations, never re-expose it. (The **SACM** half of "GSN/SACM" — the OMG
  Structured Assurance Case Metamodel — was *not* independently fetched; see Part 3.)

### L7 — API conformance testing as the model for the 'cold-start handoff' test
- **Source:** https://en.wikipedia.org/wiki/Conformance_testing
- **Passage:** "Conformance testing … is testing or other activities that determine whether a
  process, product, or service complies with the requirements of a specification, technical standard,
  contract, or regulation."
- **Grounds/constrains the brief:** The issue's test F ("Final handoff is sufficient from a cold
  start") and the brief's `preserved()` are *conformance tests*: a fresh consumer with no history
  receives only the handoff and must recover every action-relevant obligation — the handoff must
  *conform* to the contract spec. The lineage constrains the test to be **black-box / consumer-side**:
  it must assert recoverability of obligations *without* Empirica internal state, which is the issue's
  "agent equivalent of an API consumer conformance test." This is why `preserved()` must be defined
  against a *decoder of the projected view*, not against the in-memory contract (see D5).

### L8 — Append-only / revision semantics (event sourcing / Git objects) for contract revisions
- **Source:** https://martinfowler.com/eaaDev/EventSourcing.html (Fowler 2005)
- **Passage:** "Event Sourcing ensures that all changes to application state are stored as a sequence
  of events … the event log is a purely additive structure that requires minimal locking." Capabilities:
  "Complete Rebuild … discard the application state completely and rebuild it by re-running the
  events"; "Temporal Query: We can determine the application state at any point in time"; "Event
  Replay: If we find a past event was incorrect, we can compute the consequences by reversing it and
  later events." "A common example of an application that uses Event Sourcing is a version control
  system."
- **Grounds/constrains the brief:** The brief's `Contract{revision, parent_revision?,
  obligations[], provenance}` with "revisions are append-only; `revise()` returns a new revision with
  explicit `supersedes`; nothing mutates in place" is event-sourcing / Git-object semantics: each
  revision is an immutable event; `parent_revision`/`supersedes` gives temporal query; the live
  contract is a *projection* of the revision log. "Nothing mutates in place" is what forbids silent
  in-place goalpost-moving (the issue's "prevents an executing model from moving the goalposts").
  *But* (see D4) append-only makes a move *visible*, not *honest* — the lineage shows the gap, not the
  guard.

---

## Claims established (repo facts independently re-verified)

Each was a "verified starting fact" in the brief; I re-derived it from the tree rather than trusting the
table (main is on `feat/obligations-contract`).

| ID | Statement | Evidence |
|----|-----------|----------|
| R1 | Core computes per-claim remediation as `ClaimReason(claim_id, text, confidence, reason)` over open claims | `plugins/empirica/core/convergence.py:111-124` (`_blocked_converging` appends `ClaimReason(claim_id=nid, text=node["text"], confidence=node["confidence"], reason=why)`); `plugins/empirica/core/decisions.py:44-51` (the dataclass), `:83-97` (`Block`) |
| R2 | Application discards it: every Block path calls `wire.block(decision.reason, …)` | `plugins/empirica/application/service.py:869-957` (`_finalize_block` → `wire.block(decision.reason, self._run_view(…))`) |
| R3 | Wire collapses Block to `{type, reason, run}` | `plugins/empirica/application/wire.py:195-196` (`def block(reason, run): return {"type":"Block","reason":reason,"run":run}`) |
| R4 | RestoreRun projects the graph to four integers | `plugins/empirica/application/service.py:244-267` (`_restore_graph_view` returns `{"gating": len(gating), "open": len(open_claims), "blocked": len(blocked), "deferred": len(deferred)}`) |
| R5 | Claude restore renders that snapshot and says "continue resolving the application-reported open work" | `plugins/empirica/adapters/claude/restore.py:45-70` (`restore_context` JSON-dumps `snapshot`, frames it as data, instructs to "continue resolving the application-reported open work") |
| R6 | Claude Stop renders only `reason` to stderr | `plugins/empirica/adapters/claude/completion.py:70-100` (`stop_result`: `Block` → `StopResult(2, stderr=text)` where `text = result.get("reason")`) |
| R7 | Pi renders only `reason` (gate, convergence notice, settled nudge) | `plugins/empirica/adapters/pi/src/translate.ts:~103` (`gateFromDecision` Block → `{kind:"deny", reason: result.reason}`), `:~132` (`convergenceNotice` Block → `…blocked — ${result.reason}`), `:~183` (`settledFollowUp` → `…— ${result.reason}`) |
| R8 | Response schema `block` allows additionalProperties (additive change is legal within v1) | `contracts/empirica/v1/response.schema.json` `$defs.block` → `"additionalProperties": true`, `"required": ["type","reason","run"]` |
| R9 | Test RS2 locks in counts-only restore | `plugins/empirica/tests/test_application.py:1228-1236` (`check("RS2 …", snap["graph"]["gating"] == 1 and snap["graph"]["open"] == 1, …)`) |
| R10 | SKILL.md promises "re-injects the graph — including the missing folds" | `plugins/empirica/skills/empirica/SKILL.md:363-366` ("Across compaction, `SessionStart:compact` re-injects the graph — including the missing folds — so the loop is durable-resumable") |
| R11 | Pi `/empirica` does not parse mode flags; goal = `args.trim()` | `plugins/empirica/adapters/pi/src/index.ts:~111` (`const goal = args.trim() \|\| "(goal …)"`) |
| R12 | Pi holds the run handle only in extension memory; the model never receives it as a referenceable token | `plugins/empirica/adapters/pi/src/index.ts:99-118` (`let runHandle: string \| null = null;` closure; only `ctx.ui.notify(notice.text,…)` reaches the surface, never a handle the model can pass back) |
| R13 | Pi gates the `report_convergence` tool but registers no such tool (only a slash command) | `plugins/empirica/adapters/pi/src/index.ts:~178` (`pi.on("tool_call", …)` checks `gatedTools.has(event.toolName)`, default `[REPORT_CONVERGENCE_TOOL]`); `grep` confirms **no** `registerTool`/`pi.tool` — only `pi.registerCommand("report-convergence", …)`. The gate intercepts a tool the extension never exposes. |
| R14 | Pi has no RestoreRun on compaction, no compaction/SessionStart interception | `plugins/empirica/adapters/pi/src/index.ts` (full file): only `resources_discover`, `empirica`/`empirica-status`/`report-convergence` commands, `tool_call`, `agent_settled` handlers. No `RestoreRun`, `compaction`, or `SessionStart` symbol. `agent_settled` is explicitly observational ("Pi cannot veto completion", ADR-32) and uses `pi.sendUserMessage(nudge, {deliverAs:"followUp"})` — a best-effort nudge, not a gate. |
| R15 | Host-neutral pattern the brief says to copy exists in code | `contracts/empirica/v1/response.schema.json` (schema) + `plugins/empirica/core/{decisions,convergence}.py` (pure core, injected `evidence`/`audit` oracles, no I/O) — the pattern is real. (The *ADR-30 doc text* itself was not read; see Part 3.) |

## Claims refuted (brief assertions found false / unsupported as stated)

| ID | Brief assertion | Finding |
|----|-----------------|---------|
| F1 | "Reusability is a *verified* property, not an intention" (Placement decision) | **Refuted as stated.** At the time of writing `lib/obligations/` and `scripts/check_vendor.py` do not exist; the byte-identical-vendored property is *not yet verified* — it is an intended property that `make vendor-check` + shared fixtures *will* verify. Calling it "verified" now is a category error. |
| F2 | "`preserved(before_view, after_view) -> bool \| reasons` — the property the regression tests assert" (v1 scope) | **Refuted as specified.** `preserved()` is not decidable as the brief states it (see D5): the brief names four failure modes ("disappears / becomes less specific / loses its discharge condition / recoverable only by model inference") but defines no equivalence/normal form and no *decoder* of the projected view. Without a decoder, "recoverable only by model inference" is asserted, not checked. The regression oracle the brief points at does not yet exist as a computable function. |
| F3 | "`verify` … pure, deterministic, order-independent over observations" (v1 scope) | **Unsupported as a property.** This is a design *intention*; it becomes a verified property only after fixtures pin order-independence/idempotency (see D6 F9/F10). Listing it under "v1 scope" as if it were settled is premature. |

## Open / blocked (what was tried)

- **DuckDuckGo search rate-limited** (HTTP 403/429) and one Eiffel `Design-by-Contract` doc URL 404'd.
  Worked around by fetching primary sources directly (`crawl4ai_fetch` / `ketch_scrape`): Wikipedia
  (Hoare, DbC, LTL, RV, GSN, Conformance), SEP (deontic), raw GitHub (in-toto spec), Martin Fowler
  (event sourcing). All eight lineage items got a fetched, citable passage.
- **SACM (OMG Structured Assurance Case Metamodel) not independently fetched.** GSN (the more cited
  half of "GSN/SACM") is verified via the Wikipedia + GSN Community Standard reference; the SACM
  metamodel spec was not retrieved, so the *SACM-specific* claim in the brief is UNVERIFIED (see Part
  3). Not blocking — GSN alone grounds the "different layer" argument.
- **Pi extension docs not read.** The brief points Lane C at
  `/Users/dmitry.lambrianov/.nvm/.../docs/extensions.md` (+ `compaction.md`, `sessions.md`). I
  deliberately did not read them (Lane C's assignment) and stayed read-only on the adapter source. So
  the brief's claims about *what Pi can do* (mode-flag parsing ADR-28, surfacing the run handle,
  registering `report_convergence`, spawn interception, compaction RestoreRun) are UNVERIFIED by me.
  The *absence* claims (no RestoreRun/compaction, gate-without-registered-tool, no mode flags, handle
  in memory only) I **did** verify from `index.ts` (R11–R14).
- **Codex adapter not spot-checked.** The brief's `lifecycle.py:300-301,334-337,362-367` "renders only
  reason" cites were not re-read; carried from the brief's table as UNVERIFIED by this lane.
- **`make check` not run.** Read-only lane; the new `lib/` does not exist yet. N/A here.

## Files changed
None. READ-ONLY lane — no repo file created or modified. The only artifact is this report, written to
the authoritative run output path (outside the repo). No `git add/commit/push/checkout/stash` was run.

---

## Part 2 — Adversarial critique of the brief's v1 design

Each attack cites the brief element it targets. The goal is to find where an obligation can still be
lost or where a v1 decision is underspecified enough to admit a silent divergence or an abuse.

### D1 — Where an obligation can still be lost or become less specific across a boundary
The v1 scope says `project()` is "the ONE shape every boundary emits." But Empirica-consumption step 4
spreads that one shape across **five different transports**: Claude Stop *stderr* (text), Claude/Codex
*restore* (a JSON snapshot inside a data-fenced block), Codex *deny reasons* (a tool-deny string), and
Pi *notices + nudge* (`ctx.ui.notify` text / `sendUserMessage`). These are not one shape — they are one
structured view **serialized into four different string-shaped channels**.

- **Flattening loss:** today `wire.block(reason: str, run)` (R3) and Pi's
  `gateFromDecision → {kind:"deny", reason: result.reason}` (R7) carry a *single string*. The brief
  asserts `project()` returns a structured view, but the **serializer contract for each transport is
  unspecified**. If a Block deny serializes the structured obligations into the `reason` string, the
  set is "preserved" only if the *consumer parses* that string — and after compaction the consumer no
  longer has the Stop stderr at all. The very boundary the brief claims lossless (Block → agent) is a
  string-flattening point unless the wire schema carries obligations as a structured field.
- **Stop→compaction gap:** the Stop projection goes to stderr; compaction discards stderr. The brief's
  test A (compaction preserves) implicitly assumes RestoreRun is the carrier, but never *names*
  Stop→compaction as a gap that the **persisted contract revision** must fill. An obligation projected
  at Stop but not persisted to the revision is lost at compaction. The durable carrier must be the
  revision, not the Stop text; the brief should say so.

### D2 — Where exact `(kind, ref)` matching breaks in practice
`ref` is a free string like `artifact:research/<claim_id>`. Exact match is brittle in three ways the
v1 scope does not address:
- **Ref drift across revisions:** `revise()` mints a new revision; if a claim id is renamed, a witness
  declared with the old `ref` will never match an observation produced under the new id — a
  *discharged* obligation surfaces as `unwitnessed` forever. The brief says revisions are append-only
  but does not say witness `ref`s are *rewritten* (or aliased) on revision.
- **Ref-grammar divergence (the TS-mirror risk):** with "NO predicate language" the brief also fixes
  no `ref` *grammar* per `kind`. Two producers (the Python projection and the TS mirror) can mint
  `artifact:research/G7` vs `artifact:research/g7` for the same logical witness; exact match silently
  fails and an obligation the agent *did* discharge stays `unwitnessed`. This is precisely the
  cross-implementation divergence the brief says it wants to prevent.
- **Recommendation:** the fixtures must pin a **canonical `ref` grammar per `kind`** (a shape/regex),
  not merely assert string equality; equality is necessary but insufficient to keep two
  implementations from diverging on ref minting.

### D3 — Is `judgment` a backdoor?
Partially. The brief closes the *observation* side: "a `judgment` observation never discharges a
machine-kind witness." Good — an auditor opinion cannot launder a `test`/`exit_code`/`event` witness.
But the brief leaves the **witness-declaration** side open: nothing stops the Empirica projection from
*declaring* a `judgment:audit/G7` witness for an obligation whose fact is intrinsically
machine-observable (it should have been `test:…` or `exit_code:…`). Then a model/auditor opinion
discharges it, bypassing the deterministic verifier entirely. That is a backdoor at the **projection
layer**, which the verifier's kind-guard cannot see.
- Also, `judgment` has no distinct `trusted()` constraint or provenance requirement: `trusted` is an
  injected host predicate, so the *trust* of a judgment is host-defined, and the brief does not require
  a `judgment` observation to carry **who** judged (`source`). An opinion with no provenance is a bare
  assertion.
- **Recommendation:** (a) a witness `kind` should be the *strongest* kind the fact admits
  (machine > judgment), enforced or flagged at projection/audit time; (b) `judgment` observations
  MUST carry an identifiable `source` (auditor/model id), and the default `trusted` should reject
  anonymous judgment.

### D4 — Can `revise()` be abused to move goalposts, and what makes a revision honest?
Append-only (L8) makes a change *visible*, not *honest*. An executing model that finds a convenient
shortcut can `revise()` to drop a `must` or relax a `must_not`, produce revision N+1 `supersede`-ing N,
and then "discharge" the easier set. The brief's only guard is "explicit `supersedes` + provenance,"
which makes the move *auditable*, not *forbidden* — and the issue itself names this exact threat
("prevents an executing model from moving the goalposts after discovering what was convenient to
implement"). The brief is silent on:
- May a revision **drop or weaken** a still-open obligation? The brief does not say.
- Must a revision **preserve** all *discharged* obligations from the parent? Unspecified.
- Must a weakening revision carry a **human/auditor attestation**? Unspecified.
- **Who may call `revise()` mid-run** — the model, the core, or the human? Unspecified. This is the
  single biggest abuse surface: if the executing model is the revising authority, append-only
  legitimises goalpost-moving.
- **Recommendation:** define `preserved()` *across revisions*: a revision may not drop/weaken a
  still-open obligation without an attested reason; dropped obligations must be recorded as
  `superseded-with-reason`, not silently absent; and `revise()` authority must not rest with the
  executing model alone.

### D5 — Is `preserved()` decidable as specified?
No, not as stated (this is F2). `preserved(before, after) -> bool | reasons` over the brief's four
failure modes:
1. **"disappears"** — decidable: `after` contains the obligation with the same `id`.
2. **"becomes less specific"** — **not decidable without a defined equivalence.** With no predicate
   language, "specificity" is text comparison. Two renderings of one obligation can differ in
   whitespace/ordering/phrasing while being equivalent; `preserved()` needs a **normal form**
   (canonical JSON, sorted witnesses, normalised text) or it is either too strict (flags benign
   rephrasing) or too loose (misses real weakening).
3. **"loses its discharge condition"** — decidable iff the condition == the witness set:
   `before.witnesses ⊆ after.witnesses` (witnesses may be added, never removed — the DbC
   "strengthen postconditions, never weaken" rule from L1).
4. **"recoverable only by model inference"** — **not decidable as stated.** This is a property of the
   *consumer's parser*, not of the two views. Decidability requires a **reference decoder**
   `parse(project(view)) -> obligations`; `preserved()` then compares the decoder's reconstruction to
   the source. The brief specifies no decoder.
- **Recommendation:** ship a canonical decoder in `lib/obligations/` and define `preserved` as
  `parse(after) ⊇ parse(before)` over normalised obligations. Without it, failure mode 4 is asserted,
  not checked.

### D6 — Minimal fixture set so a TS reimplementation cannot silently diverge
The brief says "Python and TS tests both consume the fixtures." To prevent silent divergence the
fixtures must pin **observable behaviour** of `verify`, `project`, `preserved`, and `revise`, not
just schemas. Minimal set (each names the divergence it blocks):

| # | Case | Pins |
|---|------|------|
| F1 | empty contract, empty observations → Verdict all-empty | baseline; no obligations ⇒ no residuals |
| F2 | one `must` + matching `test` obs `pass` → `satisfied` | exact (kind,ref) + outcome=pass discharges |
| F3 | one `must` + matching `test` obs `fail` → stays `residual`/`unwitnessed` | a `fail` does not satisfy; residual bucket |
| F4 | matching `ref`, wrong `kind` → NOT discharged | **kind** matters (catches TS impl that ignores kind) |
| F5 | matching `kind`, wrong `ref` → NOT discharged | **ref** matters (catches ref-grammar divergence) |
| F6 | `must_not` + observed `pass` → `violated` | prohibition/violation asymmetry |
| F7 | `must_not` + NO observation → NOT violated (open) | absence ≠ violation (the deontic point; catches a TS impl that treats silence as violation or as discharge) |
| F8 | `judgment` obs `pass` discharges a `judgment` witness, but does NOT discharge a *different* obligation that has a `test` witness | the judgment-backdoor guard |
| F9 | observations in **reversed** order vs forward → same Verdict | order-independence |
| F10 | duplicate observations (same kind,ref,outcome twice) → same as single | idempotency |
| F11 | `revise()` → N+1 with `supersedes=N`; an obs discharging an obligation only in N does NOT discharge N+1 | append-only + supersedes (catches a TS impl that merges revisions) |
| F12 | `preserved(view, view)` True; `preserved(view, view−one-obligation)` False with a reason naming the dropped id | the `preserved` contract + reasons output |
| F13 | `must` text weakened ("refund within 1 day" → "refund") → `preserved` False | specificity (needs the D5 normal form) |
| F14 | `must_not` witness observed `pass` → `violated` regardless of `payload` | payload does not override outcome for prohibitions |
| F15 | cold-start: 3 open obligations → `project()` → a fresh decoder with **only** the view recovers all 3 ids, musts, witnesses | conformance / cold-start (needs the D5 decoder) |

F4/F5/F8/F9/F11 are the ones that specifically catch a TS mirror from silently diverging; F15 needs
the canonical decoder from D5.

---

## Part 3 — Claims UNVERIFIED in the brief itself

- **U1 — "Reusability is a *verified* property, not an intention"** (Placement decision). The
  vendoring (`lib/obligations/`, `scripts/check_vendor.py`, `make vendor-check`) does not exist yet.
  Verified property, not yet — UNVERIFIED until Lane A delivers.
- **U2 — The `verify`/`preserved` behavioural guarantees** ("pure, deterministic, order-independent";
  "`preserved()` … the property the regression tests assert"). Design intentions; UNVERIFIED until
  the lib + fixtures exist (D5/D6 are what would verify them).
- **U3 — `judgment` cannot let a model opinion decide a machine-observable fact** (v1 scope). The
  *observation*-side guard is specified; the *witness-declaration*-side guard is not (D3). The
  guarantee as stated is UNVERIFIED because the projection layer is unconstrained.
- **U4 — ADR-30 characterization** ("host-neutral pattern: `contracts/<proto>/v1/*.schema.json` +
  fixtures + a pure `core/` … ADR-30 `doc/adr/0030-*.md`"). The *code pattern* is verified (R15); the
  *ADR-30 doc text* was not read — UNVERIFIED.
- **U5 — Codex adapter renders only `reason`** at `lifecycle.py:300-301,334-337,362-367`. Not re-read
  by this lane — UNVERIFIED by me (carried from the brief's table).
- **U6 — Pi *capability* claims** (mode-flag parsing ADR-28; surfacing the run handle; registering
  `report_convergence`; spawn interception; compaction RestoreRun). The Pi extension docs
  (`extensions.md`/`compaction.md`/`sessions.md`) were not read (Lane C's job). Whether Pi *can* offer
  these hooks is UNVERIFIED. (The *absence* of RestoreRun/compaction and the unregistered gated tool
  ARE verified — R13/R14.)
- **U7 — SACM half of "GSN/SACM"**. GSN verified (L6); the OMG Structured Assurance Case Metamodel
  spec was not fetched — SACM-specific claims UNVERIFIED.
- **U8 — Issue's "Relevant history" commits** (`9fd3faa`, `2a394d6`, `93b2234`) and "the pass-budget
  regression was real". Commit contents not fetched — UNVERIFIED.
- **U9 — The core invariant `obligations before == obligations visible after`**. The `==` is an
  equivalence the brief never formally defines (no normal form, no decoder — D5). As a *checkable*
  property it is UNVERIFIED; this is the top residual risk.
- **U10 — "Pi adapter is brought to parity … every remaining gap is named as a gap."** Current Pi
  gaps verified (R11–R14); that parity *will be* achieved is an intent, UNVERIFIED (depends on Lane C
  + the Pi docs).

## Residual risks (ranked)
1. **The invariant is not yet checkable.** `preserved()` has no defined equivalence/normal form and
   no decoder (D5/F2/U9) — the regression oracle the whole design leans on does not exist as a
   computable function until Lane A ships a canonical decoder + normal form.
2. **Transport-flattening loss** (D1): `project()` is "one shape" but the five render targets are
   string-shaped channels; unless the wire schema carries obligations as a structured field and the
   durable carrier is the *revision* (not Stop stderr), obligations projected at Stop are lost at
   compaction.
3. **`revise()` authority** (D4): unspecified who may revise mid-run; append-only makes goalpost
   moves visible, not honest. Biggest abuse surface.
4. **`judgment` backdoor at the declaration layer** (D3): projection can route machine-checkable
   facts through `judgment` witnesses; observation-side guard cannot see it.
5. **ref-grammar divergence Python vs TS** (D2): no canonical `ref` grammar per `kind`; exact match
   silently fails across implementations.
6. **Specificity is undefined** (D5 mode 2 / F13): "becomes less specific" needs a normal form or
   `preserved()` is non-deterministic in practice.
7. **SACM** (U7): minor — only the ADR's lineage completeness is at risk; GSN already grounds the
   layer-separation argument.

## Requests to other lanes (exact diffs / spec clauses)

### Request to Lane B (Empirica consumption, Python) — exact diff
`wire.block` must carry obligations as a structured field (additive; schema already allows
`additionalProperties: true` — R8). File `plugins/empirica/application/wire.py:195-196`:

```diff
-def block(reason: str, run: dict) -> dict:
-    return {"type": "Block", "reason": reason, "run": run}
+def block(reason: str, run: dict, obligations: list[dict] | None = None) -> dict:
+    result: dict = {"type": "Block", "reason": reason, "run": run}
+    if obligations is not None:
+        result["obligations"] = obligations
+    return result
```
Then every `_finalize_block` path in `service.py:869-957` must pass the projected obligations through
(`wire.block(decision.reason, run, obligations=[…])`) so the Block carries the structured set, not a
flattened string (closes D1 flattening). RS2 (`test_application.py:1228-1236`) must be extended to
assert the obligation identities, not just `gating==1, open==1`.

### Request to Lane A (contract + lib) — spec clauses (files do not exist yet, so no diff possible)
1. **Canonical `ref` grammar per `kind`** (D2): define and pin in `contracts/obligations/v1/` a
   grammar/shape for each `kind`'s `ref`; fixtures F4/F5 assert kind and ref independently.
2. **Canonical decoder + normal form** (D5): ship `parse(project(view)) -> obligations` and a
   normalisation (canonical JSON, sorted `witnesses`, normalised `must`/`must_not` text); define
   `preserved` as `parse(after) ⊇ parse(before)`.
3. **`judgment` declaration guard + provenance** (D3): require a witness `kind` to be the strongest
   kind the fact admits (machine > judgment), flagged at projection/audit; require `judgment`
   `Observation.source` to be a non-anonymous identifier and the default `trusted` to reject
   anonymous judgment. Fixture F8 pins the guard.
4. **Honest revisions** (D4): `revise()` may not drop/weaken a still-open obligation without an
   attested reason; superseded obligations recorded as `superseded-with-reason`; specify the revising
   authority (not the executing model alone). Fixture F11 pins append-only.
5. **Fixtures F1–F15** (D6) as substrate-neutral fixtures under `contracts/obligations/v1/`,
   consumed by both Python and TS.

### Request to Lane C (Pi parity, TS) — contingent on Lane A/B
Once the wire Block carries `obligations` (Lane B above), render the **projected obligations** in
every Pi surface, not just `result.reason`: `gateFromDecision` deny (translate.ts:~103),
`convergenceNotice` (translate.ts:~132), `settledFollowUp` (translate.ts:~183), and `statusNotice`.
Cannot produce an exact diff until the `obligations` field exists on the wire `Block` result and the
TS `Result` type is widened to carry it. (The Pi *capability* questions — whether Pi offers
compaction/session-start hooks for RestoreRun, tool registration, mode-flag parsing — require reading
the Pi docs Lane C was assigned; I did not read them, so I make no capability claim here.)
