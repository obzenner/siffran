---
number: 72
title: Bind human decisions to a core-owned immutable HITL document
status: accepted
date: 2026-11-02
tags:
- empirica
- governance
- evidence
- hosts
- state
links:
- target: 63
  kind: Amends
- target: 67
  kind: Amends
---

# Bind human decisions to a core-owned immutable HITL document

Amends ADR-0063 (the run-configuration approval and its amendment confirmation) and ADR-0067 (the end-of-pass
evidence confirmation batch). Status *accepted*: the forms stack ships this record; release acceptance at the stack
top (Empirica 7.0.0) carries it together with the upgrade guide `doc/upgrading-empirica-7.md`.

## Context and Problem Statement

Every human-in-the-loop decision is asked in a native host form that carries everything itself. Claude's
elicitation form for the configuration quotes the goal, renders the whole sizing rationale wrapped beneath its
border, and lists every budget with used and allowed (`plugins/empirica/adapters/governance.py`, the form
builders as the workstream found them; the rationale and budget rows through `review_form`'s predecessors); the
edited-configuration confirmation repeats the values with their "(was …)"
priors and the amendment warning (the confirmation branch beside them; both the file before the
workstream). The end-of-pass batch embeds each claim's full text and each
quote in the item itself, and carries a record's "why no deterministic check" reason
(`plugins/empirica/adapters/confirmation.py`, the file before the workstream; the reason is `spike_unavailable`); the
terminal consent question is again a fully worded form item (the file before the workstream). Pi renders the same content in its
TUI dialog (`plugins/empirica/adapters/pi/src/dialog-view.ts`, `governance_inactive` and the item dialogs), the two hosts sharing one builder.

The operator's native closeouts (2026-10-26..28) showed the result: the forms are long, humans stop checking
them, and the provenance detail (rationale, claim text, the no-spike reason) that fills them is rarely opened.
The form is doing two jobs at once — being the short decision the human makes, and being the document of record
the decision is supposed to rest on — and it is too long for the first and too ephemeral for the second. There
must be one owner for the trust facts a human decides on, and the record must be able to prove *which* words a
human decided on without trusting the adapter that showed them.

## Decision Drivers

* A form's job is the decision; a document's job is what the decision rests on. Keeping both in one string
  fails both.
* What a human approved must be provable from the run's own record: not from an adapter's word, not from a
  file the adapter could have rewritten, and not from form text the record never saw.
* The core already owns every trust fact (goal, budgets, rationale, batches, claims); no host may re-derive
  or re-word any of it (ADR-0067; the UI/observer contract carries the same rule).
* Both hosts must keep working: Claude Code mediates decisions only through MCP form elicitations, and
  the supported Claude range starts below the CLI version that added URL-mode elicitation — so this
  ADR's forms reference documents by path as an adapter compatibility choice, not because the CLI
  cannot open URLs (anthropics/claude-code issue #48164 is the context); Pi shows dialogs in its
  TUI through the Python private bridge.
* Immutable bytes are checkable: two readers must get one answer, and the answer must have an exact digest.
* No backwards compatibility is owed inside a run: a state-schema version bump already classifies old run
  files as corrupt, as `empirica.run/2` does today
  (`plugins/empirica/tests/test_run4_binding.py`, `Run4SchemaTests.test_roundtrip_is_deeply_immutable_and_encoder_is_run4_only`).

## Considered Options

* Keep everything in the forms (rejected: the observed outcome — too long to check, provenance rarely opened).
* Markdown documents instead of JSON (rejected: no machine-checkable structure, so no exact digest of the
  decision surface; the user chose JSON).
* YAML documents (rejected: another dependency and no canonical byte form; JSON already has one owner,
  `core/canonical.py`).
* Digest recorded only in the sidecar file and the form, not in state ("not-P", rejected: the record could not
  prove which document a human decided on without trusting the adapter or the file system; the decision's
  fingerprint covers only the payload the adapter submits, `core.governance.canonical_digest`).
* A recorded "what the dialog stood beside" block inside the digested document — reviews left, the human-surface
  observation, a separate budgets-used map (rejected, review-1 finding 1: the core re-renders the document and
  refuses a mismatch at every decision, but the decision gate's own receipt append is what the interaction
  counters count (`core.governance.interactions_remaining`, which every path that admits or shows an interaction
  reads), and the transaction layer's `trusted_human_surface` rewrites
  `human_surface` between calls, so a digest over those values changes under
  the human's own answer and the recompute would refuse every decision. The document records only the decided
  content plus facts fixed for the run's lifetime).
* One core-owned immutable JSON document per decision, written beside the run's record and bound into the
  run's own state by its digest (chosen).

## Decision Outcome

### The HITL document, `empirica.hitl/1`

The core owns and renders one immutable JSON document for every human-in-the-loop decision, defined by the
schema `empirica.hitl/1` at `contracts/empirica/v2/hitl.schema.json` (source tree only; like the state
fixtures and `shared-defs.json` it is not vendored into the plugin today). Two kinds:

* **configuration** — what the configuration forms ask (ADR-0063): an explicit `question` discriminator
  (`approve` \| `confirm_amendment`, below), the goal, the sizing rationale marked as author-unverified
  (`rationale_source: "author_unverified"` — the contract's unverified label the forms render under today,
  so the author's untrusted text never reads as a core statement, ADR-0063), the `control_mode` (it is inside
  the `proposal_digest` the decision binds — `core.governance.proposal_digest` — and authorises later
  automatic acceptances), every budget with used/minimum/maximum, the amendment and its `(was …)` priors on a
  `confirm_amendment` document, and a `context_when_shown` block of **run-fixed invocation facts only**
  (host, signal, interactive, delegation, audit_retrieval — `core.projection.project_invocation`; recorded at
  StartRun and never rewritten, unlike `human_surface`, which the transaction layer's `trusted_human_surface`
  rewrites). The block is marked **not part of the decision**: it explains what
  the dialog stood beside. Nothing volatile is inside it — no `reviews_left` (the receipt the decision gate
  appends changes it, `core.governance.interactions_remaining`), no `human_surface` (rewritten between calls), no separate budgets-used
  map (`budgets[].used` already carries it): the form shows reviews-left live from the dialog instead
  (the shared `governancePresentation.dialog`, `contracts/empirica/v2/shared-defs.json`, its `budgets` rows the
  forms render live — `adapters.governance.review_form`).
* **evidence** — what the end-of-pass evidence confirmation batch asks, and the terminal "end this run
  without converging" consent (ADR-0067): every record with its source, quote, verification class,
  **`claim_text`** (checked against the record's `claim_digest`, the digest binding that claim's id and
  text, `core/evaluation.py:277-280` — the text is the content the human judges a quote against) and
  **`outcome`** (supporting or refuting — a human cannot judge a quote without knowing what it claims to
  be), the "why no deterministic check" reason where one exists, every claim with its text, and the
  terminal consent's status and unresolved residue
  (`adapters.confirmation.build_items`, the shared item builder; `core.evaluation.plan_evidence_confirmation`
  and `core.evaluation.terminal_batch`).

Which rows count as the terminal residual content is **selected by one core function** from a GetRun-shaped
snapshot — the run view's active obligation rows that are not satisfied, excluding the terminal consent row
itself (`core.projection._obligation` for a row's shape, `core.projection._obligations` for the active
row set, whose content
depends on the snapshot's command, which is why the GetRun shape is the one source; implemented in PR 10, the
presenting leg) — so the presentation and the consent cannot disagree on what ending now would leave
unresolved.

### The question and the amendment's priors

Which configuration form is shown is an **explicit `question` discriminator in the document** — `approve` or
`confirm_amendment` — derived by the core, never by the adapter: it is `confirm_amendment` iff some receipt
has `outcome == "amend"` and `plan_revision + 1 == governance.plan_revision`; otherwise `approve`. The
governance state word is **not** the discriminator (`HUMAN_WAIT_STATES` includes `rejected`, a state in which
a re-present is admitted, `core.governance.HUMAN_WAIT_STATES`): the most common edit — a human amendment
before the
first approval — goes through `revise`, which leaves the state `pending` whenever `approval_kind` is `None`
(`core.governance.revise`); an author revision after the first approval yields `revision_pending` with
no human amendment (an author `configure_run` goes through the same `revise`); and the host's own gate is
not a form chooser at all — review-2 (M1) pinned the adapters to show the confirmation form exactly when the
bound document's `question` is `confirm_amendment`, the recursion flag only guarding a re-presented dialog
(`adapters.governance` `_mediate`). The document still carries
`governance_state` as information, covering `pending`, `revision_pending` and `rejected`.

The confirm document's `(was …)` priors are not derivable any other way — the state's proposal has already
been replaced at the amend commit (the decision gate's `revise` over the proposal) — so run/4 records them
**on the receipt the amendment finalises**: `receipts[].superseded_budgets`, written by the decision gate's
one receipt append (the sole receipt writer, `application.governance`) from
`state.governance["proposal"]["budgets"]` — read from the
state, not the already-revised `governed` — and `null` for every other outcome. The invariant checks:
non-null if and only if `outcome == "amend"`, and its keys are exactly the ceilings
(`core.governance.CEILINGS`).

### Deterministic bytes and the digest

The core renders the document as deterministic pretty JSON: sorted keys, 2-space indent, one trailing
newline, literal UTF-8 (no ASCII escaping), built on the one canonical JSON owner so it inherits the old
guarantees — string keys are sorted, non-finite floats and unordered containers are rejected, one writer
(`core.canonical.canonical_json`; the pretty renderer is new code beside it). The
`document_digest` is the `sha256:`-prefixed SHA-256 of exactly those bytes, serialized and verified the way
every other digest in the core is (`core.canonical.canonical_digest`). Because the rendering is a pure function of
run state, two readers get byte-identical documents.

**Every free-text field is display-escaped through the core's one escaper** — `safe_text`, which doubles a
backslash and turns bidi and zero-width characters into their `\uXXXX` escapes rather than emitting them
literally (`core.projection.safe_text`): goal and rationale arrive through it with the dialog, and the
builder applies it to every remaining free-text field (claim text, citation, source_ref, the
"why no deterministic check" reason, a residual's `required` line). A bidi override or a zero-width space
must never reach the file a human opens in a terminal — this document is the one surface meant to be trusted.
The raw wording stays bound by its digests (`claim_digest`, `statement_digest`), which the escape cannot
change because the digests are computed before the document is built.

**The core hands the adapter the rendered document as `document: {text, digest}`** — the exact rendered
UTF-8 string plus its digest, published in the private presentation answers (below). No adapter or transport
ever re-renders a dict, so there is exactly one renderer of the decision surface, and the private response
schemas validate the pair as a string plus a `digest256`
(the shared `digest256` member, `contracts/empirica/v2/request.schema.json`; the private answers that carry
it — `governancePresentation.document`, `contracts/empirica/v2/shared-defs.json`, and the private evidence
prompt members at `contracts/empirica/v2/response.schema.json`) —
which is also why `hitl.schema.json` stays source-only and **not vendored**:
nothing in a private response is ever validated against the hitl schema, only against the string+digest pair.

### The digest is recorded where the decision is recorded ("P")

The core renders the document and verifies the digest; the decision is only admitted when it carries the
digest of the document the core renders **then**. A mismatch or a stale run is refused with nothing spent,
the way a decision bound to an older revision is refused today
(`core.governance.decision_error`, `governance.stale_proposal`). The accepted
digest is recorded in the run's own state — `governance.receipts[].document_digest` in `run.json`
(`contracts/empirica/v2/state.schema.json:396-425`; the sole receipt writer is
the decision gate's one receipt append in `application.governance`) — and in the evidence presentation and evidence
confirmation artifact bodies, beside the `receipt_id`, `key`, `record_ids`, `claims` and stamps they already
carry (`core.evaluation.plan_evidence_presentation` and its `_confirmation_artifact`). The record, not the adapter, names
what was decided on. Rejected alternative (*not-P*): recording the digest only in the sidecar/form, since the
record must prove which document a human decided on without trusting the adapter or the file system (the
fingerprint covers only the submitted payload, `core.governance.canonical_digest`, and the sidecar lives in a
machine-local directory the adapter writes).

### The write-first flow

The order is fixed, and spending follows the successful check, never the reverse:

1. **Ask** — the adapter asks the core for the document. The ask itself spends nothing and writes no receipt,
   and the context leg of the same transaction may commit `governance.context` when the
   host's ingress or author observation changed (the context branch of the one trusted-governance
   transaction, `application.governance`) — that commit
   changes no digested content, so the document the answer carries stays valid. For configuration, the
   private `governance_context` presentation result carries `document: {text, digest}` beside the private
   dialog it already carries (`application/v2.py:110-150`, `governancePresentation`,
   `contracts/empirica/v2/shared-defs.json:527-539`). For evidence, the carrier is the private
   `trusted_evidence_presentation` **and** `trusted_evidence_confirmation` responses
   (`application/v2.py:132-150`, `application/transaction.py:384-390`). The member is **required**
   (`string | null`, the contract step, PR 13): an absent `document_digest` is refused by the request
   schema itself (`invalid_request`) before any domain logic runs, `null` is the explicit fetch and
   a wrong digest a mismatched call — both refused with
   `evidence.document_mismatch`, nothing spent, nothing admitted, and the refusal's private result
   carries `document: {text, digest}` for the batch named by `key` (a confirmation's key is its own
   presentation's) — so the refusal **is** the fetch: the adapter writes the document it names and
   repeats the call with the digest (PR 11/12's adapters; the presentation leg already retries once,
   `adapters.confirmation.present`). A confirmation must also name the digest its own presentation
   recorded — every admitted presentation records one, so a receipt that claims another document
   binds nothing. The public `GetRun` run view, `GetArgument` and the evaluation
   results never carry the document: its content is not secret, but its digest and path have no reason to
   become an author-facing contract.
2. **Write** — the adapter writes the document bytes to
   `$EMPIRICA_HOME/projects/<p>/runs/<r>/gen-<n>/hitl/<sha256>.json` **once**, through one bound helper
   (`adapters.state.hitl.publish`, review-2 #43/#46): a temp file beside the final name, its bytes fsynced,
   its mode set to **`0400`** (owner-only: the document carries the human's decision surface and nothing
   needs to read it but its owner; the directory is owner-only too), then linked into place with a
   **no-replace hard link** — `os.link`, not `O_EXCL` at the final path — so publication cannot overwrite
   or follow anything planted there. If the final name already exists, its bytes must be identical, else
   the adapter refuses; an interrupted publication leaves only an inert `…tmp…` sibling (its own suffix,
   never the document's name), and a retry writes the same bytes again. Every success — fresh publication,
   identical-bytes idempotence, the raced-retry path — flushes the directory barriers before it answers
   (review-2 #46: fsync on the document's directory always, and on the generation directory until the link
   is proven durable), so a crash never leaves the document recorded in state but missing from disk. The
   directory is the one
   beside-the-record location the bridge already owns per generation (`gen-<n>`: the timeline and the
   location record, ADR-0070, `plugins/empirica/adapters/state/repository.py:36,89-96`,
   `plugins/empirica/adapters/state/timeline.py:34`); none of today's state writes are write-once
   (`adapters/state/fsio.py:98-122` writes 0600 temp + rename; the publish helper is the new sibling, the
   symlink refusal at `fsio.py:78-96` is the pattern).
3. **Present** — the adapter sends `present` with the `document_digest`; the core recomputes the document and
   refuses a mismatch, and a stale run is refused with nothing spent — a mismatch here spends nothing
   either: the reason-code ordering below puts the stale gates first and a mismatch last.
4. **Show** — the form shows a short summary plus the document's path and digest (below).
5. **Decide** — the submitted decision carries the `document_digest`, which the core checks again before
   admitting it (the check extends the decision gate that already binds a submission to its run, revision and
   proposal digest, `core.governance.decision_error`).

### What the forms keep

The forms keep exactly what is being judged: the numbers (budget used and allowed
(the `budgets` rows the confirm and review forms render, `adapters.governance.review_form`)), the amended
values with their `(was …)` priors (the confirm branch beside it), the first
line of the goal, the evidence quotes (the quote is the fact a human confirms or rejects,
`adapters.confirmation._item_text`). The rationale, the claim text and the "why no deterministic check"
reason move to the document; the form carries the path and the digest of the one that holds them.

One clarification (PR 12, decided over both hosts at once): the claim text that moves to the
document is the *research records' per-claim context* — each record's "For claim …" lines, which
repeat what a quote was proposed for, never what the human is deciding. A needs-decision claim's
own text is what the human decides, exactly as a research item's quote is the fact a research
item's dialog exists to judge — so it stays in the form, **in full, on both hosts**, wrapped under
its decision line (Claude's message shows it; Pi's item dialog renders `item.text`, the same shared
`build_items` member), and the truncated field description remains only the dialog's subject line,
never the decided text. Nothing else moves: the source line, the quote, the decision enums and the
note fields stay, and the terminal consent keeps its decision fully worded beside the residual
count. The dialogs' shared strings come from one place — `adapters/confirmation.py` builds the item
titles, descriptions, quotes and claim texts both hosts show (`_research_titles`,
`build_items`), and the document pointer lines the hosts render are pinned word-identical by both
hosts' tests (the pointer line, `Document: sha256:<12 hex>`, the path on its own line, and the
residual-count sentences — `private-transport.ts`'s validator is the shape's authority for Pi,
`_item_text`/`_residual_lines`/`_document_lines` for Claude).

One amendment (PR 19, below): that truncated *shared* description is what Pi reads, and on Claude
the form builder composes an additional **host-only** description beside it — never into the
items — that repeats, in full, what the human decides (the research quote and, when the short title
cut it, the full source; the decision claim's whole text; the terminal consent's residual count).
Claude Code 2.1.304 was observed to fold the message after three lines while rendering property
descriptions in full, so on Claude the visible form carries the whole content while the message
keeps the transcript record; the items every host relays are composed, never edited, so Pi's
output stays byte-identical.

### Hosts

* **Pi**: the Python private bridge binds the document through the same one bound helper
  (`adapters.state.hitl.publish`, through its `_bind_document`) and returns path and digest; the TypeScript
  dialog only displays them (the hand-back is the same today — receipt id, items, path and digest;
  `_governance_context` and the trusted handlers, `adapters/pi/private_bridge.py`).
* **Claude**: the MCP-mediated forms write the document through the same one bound helper
  (`adapters.state.hitl.publish`, called from the governance and confirmation flows' binding call sites)
  before eliciting. Claude Code's form elicitations reference the document by path, never a link — a
  deliberate adapter choice: the supported range starts below the CLI version that added URL-mode
  elicitation (anthropics/claude-code issue #48164 is the context), so the earliest hosts the plugin must
  keep working on would render a link they cannot open.

### State, requests and compatibility

The state schema becomes `empirica.run/4` (`contracts/empirica/v2/state.schema.json:32-33`; the const drives
classification, `plugins/empirica/application/protocol.py:45`,
`plugins/empirica/application/run_state.py:71,160`). There is **no backwards compatibility**: run files
written as `empirica.run/3` classify as corrupt, exactly as `empirica.run/2` does today
(`plugins/empirica/tests/test_run4_binding.py`, the run/2-then-run/3 cells of
`Run4SchemaTests.test_roundtrip_is_deeply_immutable_and_encoder_is_run4_only`). The public wire keeps its axis `empirica/v2`; the
requests carry `document_digest` as a **required** member — on the governance decision payload for every
`approval_kind == "host_ui"` envelope (`present`, the decision, `dismiss`) as a string carrying the digest
pattern, forbidden for `auto` (PR 07) — and on the evidence presentation and confirmation payloads as
`string | null`, where `null` is the explicit fetch (the payloads' other required members beside it:
`run_id`, `receipt_id` and `proposal_digest`/`key`/`decisions` respectively,
`contracts/empirica/v2/request.schema.json:1278,1612,1716`). The staged, optional window (PRs 07/10) is
closed: an **absent** member is refused by the wire itself — the request schema's `invalid_request`, raised
before any domain logic runs — while `null` is the fetch and a wrong value a mismatch. The same discipline
the recorded receipts follow (the state conditional of `governance.receipts[]`: a `host_ui` receipt names
its document, an `auto` receipt records `None` exactly — the twins the `core/governance.invariant` keeps,
beside `presentation_fingerprint`'s):
required for every `host_ui` receipt and every human evidence record, null for `auto`. Because the
payloads require it, the public-tool schemas and the vendor copies follow
(`make empirica-public-tools-sync`, `make vendor-contracts`).

## Consequences

* Good, because the forms shrink to the decision and its reference: short forms get checked, and the record
  proves exactly which words were decided on.
* Good, because one owner renders the decision surface: no host re-words a trust fact, and two readers of
  one document get the same bytes.
* Good, because nothing volatile is inside the digest: no value the human's own answer or the host's surface
  observation can change between the fetch, `present` and the decision, so the core's recompute at the
  decision always matches the document the human read. The one bound value that can move
  between the present and the decision is `budgets[].used` — and it moves only by a refund of
  in-flight work (a settled audit or an orphaned child), which refuses closed: the decision is
  refused with a document mismatch and one review unit is spent, never silently admitted
  (review-2 m13, accepted fail-closed).
* Good, because the digest in state makes a forged or edited provenance story falsifiable from the run's own
  record.
* Bad, because `empirica.run/3` runs do not carry over: they classify corrupt, so an operator's old runs end
  unreadable until re-run (same cost every state bump has carried).
* Bad, because the change touches `IMPLEMENTATION_SOURCE_PATHS` — the core, the application, the adapters,
  `adapters/state/*` and the vendored contracts are all inside the closure
  (`plugins/empirica/adapters/sandbox/common.py:35-137`; the rule at `doc/testing.md:82-84`) — so **every
  installed qualification is invalidated and native requalification is required after release**, and the
  closure-touching PRs of the stack land together (ADR-0071's qualification binding). Within the stack the
  two closure surfaces move at different points (review-1 finding 23): the manifest's **path list** first
  changes in PR 07, when the application first imports `core/hitl.py` (and the adapter sidecar follows in
  PR 08/09); the closure's **content** — the implementation digest over the listed files — already changes
  in PR 06, when the vendored state schema turns run/4.
* Bad, because a human must open a file to read the detail; on Claude the form deliberately does not
  link it (the adapter-choice range above), so the human opens the path by hand.
* The observer listing of the documents (surfacing the digest and the path of a run's HITL documents —
  a copy-command surface for the path) is an
  `empirica-ui-view/v1` change and a **follow-up**, not part of this record. The forms on both hosts
  already leave the path line unwrapped (Q4 below); any observer-side presentation follows the same
  copy-paste rule when it lands.
* Empirica releases this as a major version bump (**7.0.0**), at the end of the stack, with its upgrade guide.

## Sub-decisions (decided; review-1 confirmed the choices)

### Q1 · How the adapter obtains the document before `present` (decided)

The document travels as the **rendered pair** `document: {text, digest}` in the private presentation answers,
never as a dict an adapter renders: for configuration the `governance_context` presentation result carries it
beside the private dialog (`application/v2.py:114-122`); for evidence the private
`trusted_evidence_presentation` response carries it, with the refusal-carries-document pattern — a call
whose `document_digest` is wrong (or `null`, the fetch) is refused with `evidence.document_mismatch`,
nothing is spent,
the refusal's private result carries the pair for the batch named by `key`, and the adapter writes and
repeats the call (`adapters.confirmation.present` — the leg already retries once; the fetch–present window
cannot drift because the refusal is the fetch); an absent member never reaches the domain at all — the request
schema refuses it (`invalid_request`) before any logic runs (PR 13). The context ask itself spends nothing and writes no receipt,
but may commit `governance.context` when the host's ingress or author observation changed
(the context branch of the one trusted-governance transaction, `application.governance`) — a commit that
touches no digested content.

*Rejected*: a new read-only trusted operation (a `hitl_document` handler) — every trusted operation carries a
payload shape, a private-response validation and guard wiring on both hosts (`application/v2.py`,
the trusted-handler blocks of `adapters/bridge.py` and `adapters/pi/private_bridge.py`
(`_HANDLERS`), `request.schema.json` `governanceContextPayload` —
`governanceContextPayload` is `author` and `ingress` only); a second answer to the same question adds that
surface to gain nothing. *Rejected*: carrying the evidence document in the public `GetRun` run view,
`GetArgument` or the evaluation results — that would make the document's digest and path an author-facing
contract surface (the public-tool schemas, the contract digest and the author view all move) for content that
is not secret but has no reason to be author-visible.

### Q2 · Byte format details (decided)

Schema-declared content, but **sorted keys** (the order is `core.canonical.canonical_json`'s, so the bytes never depend
on a schema's field order or on regeneration), `ensure_ascii=False` (literal UTF-8, the owner's rule,
`core.canonical.canonical_json`), 2-space indent, one trailing newline, and the document's numbers are integers only
(floats never reach these payloads; non-finite floats are rejected, `core.canonical.canonical_json`). The
`document_digest` is the SHA-256 of exactly those bytes, newline included. Two additions review-1 fixed:
**every free-text field is display-escaped with the core's one escaper** (`safe_text`,
`core.projection.safe_text`) — the raw wording stays bound by its digests — and **the core hands the adapter
the rendered `text` plus its `digest`, so no adapter or transport ever re-renders a dict** (the pair validates
as a string plus a `digest256`; the governance leg's `governancePresentation.document`, and the private
evidence answers at `response.schema.json:3961`).

*Rejected*: schema-declaration key order (the order of properties is not a serialization contract; two
builders or a regeneration could disagree — sorted is what the core already guarantees everywhere);
no-trailing-newline (a POSIX text file ends one); compact bytes (unreadable; `canonical_json` is for
digests, not for people); re-serializing through `canonical_json` and pretty-printing an independent copy
(two renderers to keep identical — the pretty form and its digest must come from one code path); raw
unescaped free text beside escaped goal and rationale (a bidi override in a citation would reach the file a
human opens).

### Q3 · The refusal reason codes for a digest mismatch (decided)

Two new reason codes, following the domain-prefixed naming the contract already uses (`governance.*`,
`evidence.*`, beside `governance.stale_proposal` at `contracts/empirica/v2/public-contract.json:1917`):
**`governance.document_mismatch`** for the configuration decision and present leg, and
**`evidence.document_mismatch`** for the evidence presentation, the confirmation and the terminal consent.
Both take the `host_recovery` disposition (like `stale_proposal`) and carry the message-and-next-actions the
contract rows give, so the recovery table (`plugins/empirica/skills/empirica/references/recovery.md`,
regenerated from the contract by `make empirica-recovery-reference`) grows its rows from the contract alone.
The evaluation ordering is fixed: `governance.stale_proposal` and the evidence key/staleness gates come first
— a document mismatch is reported only when the proposal and revision (or the batch `key`) still match — **a
mismatch at `present` spends nothing**, and the recovery row for the decision leg says that the present
receipt is already spent, so the recovery is to show the batch again — a **new presentation**, never a
repetition of the refused call — costing **one review
unit** of `PROPOSAL_INTERACTIONS = 3` (`core.governance.PROPOSAL_INTERACTIONS`, the counters the refusal
words stay faithful to, `core.governance.interactions_remaining`).

*Rejected*: reusing `governance.stale_proposal` (its words say the decision was bound to another
revision/digest of the *proposal* — a document mismatch is a different fact with a different recovery: fetch
the document, do not re-propose); one shared `hitl.document_mismatch` (the contract's reason codes are
domain-prefixed, and the two flows offer different next actions); reporting a mismatch before the stale
reasons (the mismatch would hide the more useful fact that the proposal or key itself is stale).

### Q4 · What the short forms show (decided)

The digest as `sha256:` followed by its first 12 hex characters (the scan length the repo already uses for
identifiers, `short_id`, `ID_SIZE = 12`, `application/observer_index.py`), and the document's
**absolute** path on **its own line, never wrapped and never truncated** — the path is a copy-paste
surface and a wrapped or cut path is a corrupted paste. A realistic path on the current layout is about
**274 characters** (the digest alone is 71 of them), far past the wrapped text widths, so the exemption is
the only way the line stays a paste. Both hosts honour it the same way with one difference: Claude never
wraps the line at all, while Pi wraps its rendered transcript in the TUI — but only at `/` boundaries, and
never within the file name (`adapters.confirmation._document_lines` builds Claude's, `dialog-view.ts`'s `documentLines` renders Pi's), so the
printed line equals the path character-for-character however the terminal lays it out. The one owner of
the dialog lengths is `adapters.confirmation`'s width constants;
the digest line and the exempt path line are pinned by the form goldens. The sidecar returns an absolute path
even under a relative `EMPIRICA_HOME` (the home is made absolute at construction, never resolved — it must
not follow a symlink). The full digest and the document path are on the decision record; the form's digest
is for the eyes, not for verification.

*Rejected*: the full 64-hex digest in the form (spends the one line the path needs, and the full value is
in the file's own name and in the run's decision record, not needed on glass — and never in the
document's content, which cannot contain its own digest); a `~`-relative path (shorter, but the operator's
`$EMPIRICA_HOME` need not be under their shell's `~`, and a printed path that a human must expand by hand is a
false convenience for a copy-paste surface; an absolute path is pasted as-is); wrapping the path into the
description text (a wrapped path is a corrupted paste).

**PR 16 amendment (2026-10-28).** The form shows the short digest plus a **verified observer link**
— `Details: http://127.0.0.1:<port>/app/?run=<compact id>&decision=<12 hex>` — and the exact absolute path
only as the **fallback** shown when no observer of this machine's home answers: with a verified link the
path is dropped (decided over the two offered wordings: one short copyable line beats a ~274-character
paste), and the fallback keeps the exempt `File:` line untouched. The link is **presentation only**: the
digest stays what every presentation and decision binds, and the core never sees a URL. A link is trusted
only after discovery proves a live server of this very home: `observe serve` publishes
`<home>/observer/serve.json` — mode `0600` in a `0700` directory, publication and cleanup serialized with a
lock file, the exact member set `{schema, port, pid, instance, home_identity, capabilities}` and **never a
URL** (`instance` a random per-process token, `home_identity` the resolved home's identity, independent of
`--repo`) — and `/api/about` answers the same three (`home_identity`, `instance`, `capabilities`, which
include `"hitl/1"`), so a **running old observer must be restarted after upgrade; it is found unusable and
the form keeps its honest fallback**. The reader (`adapters.observer.discovery.observer_url`) reads the
file no-follow, non-blocking, regular-file-only, owned-by-current-user, capped at 4 KiB, validates schema,
positive pid and port, builds `http://127.0.0.1:<port>` itself, then makes one bounded probe (proxies
disabled, no redirects, at most 16 KiB read, 0.5 s for the whole probe, once per form presentation, not per
item) requiring `home_identity`, `instance` and `"hitl/1"` to match — otherwise it is `None`, and it never
raises. The run's compact id is computed from the run's storage key `<project_id>/<run_id>/<generation>`
beside the bridge (`adapters.observer.discovery.compact_run_id`, the twin of `application.observer_index.short_id`,
pinned to it by test) and never from the opaque `er2:` handle or by importing the whole observer index into
a host form process.

## Confirmation

Shipped with Empirica 7.0.0, the stack's release acceptance. Each part was confirmed red-first as its PR landed:
`hitl.schema.json` and its example fixtures with validator checks (schema), the pure
builders and the byte renderer with goldens over schema-valid documents (core), the write-once
`gen-<n>/hitl/` sidecar — temp-plus-fsync, no-replace hard link, mode `0400`, idempotent identical
bytes, interrupted publication leaving inert temp siblings, every success flushed through the directory
barriers (state adapter) — `empirica.run/4` with the
state const, the 16 state fixtures and the non-run/4-corrupt classification (state), the governing checks —
recompute-then-refuse on present and on decision, the reason codes with recovery rows — with replay and
stale negative controls (core, application), the Claude forms with `test_governance_hosts` and the dialog
golden, the Pi dialog with its 10 rendered goldens and the bridge check (hosts), the require-digest
conditionals with negative controls (contracts), and release acceptance at the top of the stack: this ADR flips
to *accepted* with the upgrade guide (`doc/upgrading-empirica-7.md`), where the operator records the one native
requalification of the released tree and the closeout at each native boundary before the stack reaches users.

### PR 15 · The observer shows the bound document beside the decision (2026-10-13)

The read side completes the binding: what a host's form binds and records, an operator reading the run
sheet can see — `GET /api/run` answers each decision row with its `document` member, resolved from the
run's own sidecar at each request (`application.observer.Observer._documented`, over the new bounded
`adapters.state.hitl.HitlDocuments.read_bounded`: no-follow, unblocking, capped at
`application.observer.HITL_MAX_BYTES`, the digest verified from the bytes, nothing ever written), never
cached with the published detail and never computed for `/api/runs`, `/api/search` or any summary. The
statuses are the store's own honest verdicts — `verified` (a parsed body is served), `missing`,
`mismatch`, `too_large`, `unreadable` — and the wire contract couples the body to `verified` in
`run.schema.json` (`allOf` guards the generators derive, `scripts/ui_view_strategies.py` and
`ui/tests/schema-arbitrary.mjs`; the `$defs.hitlDocument` classifier follows the plugin's own member
set and changes with a plugin version, so a served body's owner stays the plugin, never the schema —
a bump means the sidecar publications are re-verified against what the released tree can read; `empirica-ui-view/v1` is changed together with the plugin in one version because the observer and the UI ship and are served as one server — a running old observer must be restarted after an upgrade, which is what PR 16's `/api/about` `capabilities` will let the forms check). The
configuration ask a host presented and its human has not answered is one `answer: later` row of its
own, superseded presentations are not listed, and `later` reads "not answered" everywhere: an open,
dismissed, timed-out or superseded dialog is never silently read as a deliberate deferral (operator
finding F3). The ETag folds each row's digest and status, so answering a request from a changed store
cannot hide behind a `304`.

### PR 16 · The forms link the decision in the observer UI, verified or honestly absent (2026-10-28)

Q4's amendment above is the decision; this is the record of shipping it. Every host form — the
governance ask, the amendment confirm, the evidence item dialogs and the terminal consent — shows the
written document's `Details`: a live observer's loopback deep link to its decision row plus the short
digest, with the exact absolute path dropped (O5), or, when no verified observer of this home answers,
the honest fallback naming the run's compact id and the `File:`-labelled exact absolute path. Discovery is
hardened end to end (`adapters/observer/discovery.py`, described in the Q4 amendment): the advertise file
is the server's own credential-free notification (no URL, per-process `instance`, `home_identity`
independent of `--repo`, `capabilities` including `"hitl/1"`), the reader verifies owner and mode before
it probes, the probe is one bounded `GET /api/about` that must answer this home and this process, and
`observe serve` reuses a live advertised server of the same home before binding a port and withdraws its
own advertise on a clean exit. The link never crosses the talk-down wire as anything but its two fields —
`url` (null as the honest "no link") and `run12` — carried once per presentation by the same private
answers that carry the digest (`adapters/pi/private_bridge.py`, `private-transport.ts`; the Pi evidence
dialog keeps every composed line within 76 columns and ends on the short digest line, so the host's count-
down lands on a short line — never a path, a quote or a URL). Confirmed red-first as it landed: the
discovery module's own tests (the advertise file's member set and modes, the probe's bounds and
refusals — a foreign home, a stale pid, a redirect, a too-old observer answering no `capabilities`), the
hosts' form tests (the fallback path on its own `File:` line, the path dropped only with a verified link),
the regenerated form goldens on both hosts (the Claude fixture and the Pi screens, including the linked
review screen at the narrow width), and the manifest closure of the new bound module
(`make empirica-manifest-check`).

### PR 18 · Per-item fail-closed: a cancel or timeout mid-batch keeps the answers already given (2026-10-28)

The Lima qualification of `73119e13` (finding N1) showed the batch-level reading of fail-closed
eating a careful answer: the human approved an item, a stray Esc at the *optional* note ended the
whole batch, and nothing was recorded — one of the run's scarce human interactions spent on an
answer that vanished. The amended rule keeps ADR-0067's fail-closed core — no answer is invented,
`later` decides nothing — and makes it apply **per item**, never per batch:

* An answer the human completed — a select ended by choice **and** its note step ended with Enter —
  is kept. A cancel, a label that was not offered, or a timeout at item *k* stops the asking there:
  items 1..k−1 keep their answers, item *k* and every later item are sent as `later`, and the batch
  is submitted once unless nothing was decided — exactly as an all-`later` batch submits nothing today.
  The shared mediator already omits `later` items from the receipt (`adapters.confirmation.decisions`)
  and the core admits a receipt whose items are a subset of the presentation's (`answered <= asked`,
  `core.evaluation.plan_evidence_confirmation`); both are unchanged.
* **Esc at the optional note is never consent and never a cancel** (decided over Lima itself): the
  same item's select is shown again under the same deadline, the human picks again, and nothing is
  lost. An item is only answered when its note step ends with Enter; a *timeout* at the note stops
  the asking there, the item staying `later` — its chosen answer without its note is never recorded
  (O1, decided). An empty note submitted with Enter stays the choice with no note, as before.
* **There is no receipt TTL** (A1, per review 18-astra): admission checks presentation and
  current-batch identity, subjects and the document digest — not elapsed time
  (`core.evaluation.py:1116–1165`). The deadline is host-local: after it passes, the completed
  earlier answers are submitted once and admitted if their binding is still current.
* **The terminal consent shares the dialog**: Esc at its note goes back to the select; Esc at the
  select, or a timeout anywhere, records no approval and the run stays active. There is never an
  earlier answer to keep — the core constructs exactly one terminal item.
* **Claude is unchanged**: its evidence elicitation is one form for the whole batch, and decline or
  cancel there is one action for that batch — so the shared `NOTE_TITLE`
  (`adapters/confirmation.py`) is untouched and the Pi-only wording (the select's promise line
  "Esc or timeout keeps completed answers; this and the rest stay for later." from a second item on
  with an earlier completed answer, and the note prompt's own "· Enter finishes this item · Esc goes
  back") is composed in the Pi dialog source alone. Both lines obey the 76-column rule (PR 16, A12).

Confirmed red-first as it landed: the Pi unit dialog tests (the Lima sequence itself — Approve, Esc at
the note, the select again), the Esc/timeout/invalid-label keeps-earlier-answers cases for every
item position, the note-timeout stop, the terminal `consent_stop`/`freeze_scope` go-back and
no-approval cases, the title width and placement pins), and one real-bridge flow
(`live-bridge-evidence-partial.test.ts`) where a two-item batch cancelled at item 2 admits exactly
one confirmation for item 1 while the observer reads item 2 as `later` — "Not answered", still open —
through `core.observer.decisions`.

### PR 19 · Claude shows three lines of a form's message (2026-10-28)

The Lima qualification of `ac401739` (finding N7) observed Claude Code 2.1.304 rendering only
the **first three lines** of an MCP elicitation `message`, folding the rest as `… (+N more lines)`
with nothing expanding it in place (`ctrl+o` opens the transcript instead), while the fields —
title plus the description rendered under it — render in full. The consent was still informed
(Binding P: the message and the document carry everything), but on three forms the human had
confirmed things they could not see in the form: the configuration review's Details link and
document line were folded, the evidence form's quotes were folded (the description showed only
`C2 · Does the page say this?`), and so was the terminal consent's residual count. The rule
(`adapters/confirmation.py`, the one owner of the constant; `adapters/governance.py` composes its
forms against it):

* Every Claude form's first `CLAUDE_VISIBLE_LINES` lines carry **no blank line**, and stay the
  heading (its countdown riding it), the bound document's `Details:` line, and the copy surface —
  the `Document sha256:<12>` short digest beside a live observer's verified link, or the exact
  absolute `File:` path in the fallback (the copy surface when there is nothing to click, the
  short digest staying below the items) — for the evidence and terminal forms. The review's third
  line is the goal it is judged by; the confirm's visible lines are its budgets line with the
  complete `(was …)` priors and the compact cue `Rationale not regenerated for these values ·
  details below` — the line judged with the numbers — with only then its `Details:` line (the
  one form the amendment grants a fourth visible line: the confirm has no field descriptions to
  carry anything else, and the full contract-owned amendment warning stays below it, whole).
  Every line in the visible window is at most 72 columns, except the URL and `File:` lines,
  which are never wrapped, as before.
* What the human decides per item rides the field **description, in full, on Claude only**:
  a research item's entire quote and — when the field's short title cut it — its whole
  `Source:` reference; a needs-decision claim's entire text; the terminal consent's sentence
  plus the residual count read out of the written document (`… · 3 unresolved obligations would
  remain`). These descriptions are composed in Claude's own `form()` beside the items — the
  items themselves are never edited, so what the bridge relays and **Pi reads is unchanged and
  byte-identical** (PR 16 A12's "quote once" held: each host's own surface shows the quote once —
  Pi's dialog renders it, Claude's composed description carries it — while the shared item
  description stays quote-free).
* The message still carries the whole record below the visible lines, for transcripts, logs and
  hosts that do not fold, so a quote may appear twice in message plus description; on Claude
  only the description copy is visible.

Confirmed red-first as it landed: the evidence and terminal form tests (the visible window with
a live link and in the fallback, the full 500-character quote and the full cut source in the
description, the 300-character claim text, the residual counts of 0, 1, many and unknown, the
grouped-record description, the items left unchanged for Pi), the review and confirm form tests
(both documents, the complete budget priors, the cue and the kept record below the fold), the
regenerated governance goldens with the linked-confirmation case on both `--check` paths, and
the Pi quote-occurrence count through the real bridge (`make check-pi` byte-identical, the
review note that `evidence-ui.ts:120–133` reads every `item.description` and forbids changing
the shared items held exactly).
