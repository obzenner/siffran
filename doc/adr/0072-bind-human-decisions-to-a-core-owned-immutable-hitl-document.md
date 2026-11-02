---
number: 72
title: Bind human decisions to a core-owned immutable HITL document
status: proposed
date: 2026-10-28
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
evidence confirmation batch). Status *proposed*: this record governs the forms workstream; it is confirmed
when its stack ships (the acceptance PR flips it to accepted, as the plan fixes).

## Context and Problem Statement

Every human-in-the-loop decision is asked in a native host form that carries everything itself. Claude's
elicitation form for the configuration quotes the goal, renders the whole sizing rationale wrapped beneath its
border, and lists every budget with used and allowed (`plugins/empirica/adapters/governance.py:95-109`, the
rationale through `:88-93`); the edited-configuration confirmation repeats the values with their "(was …)"
priors and the amendment warning (`:110-132`). The end-of-pass batch embeds each claim's full text and each
quote in the item itself, and carries a record's "why no deterministic check" reason
(`plugins/empirica/adapters/confirmation.py:131-155`, the file before the workstream; the reason is `spike_unavailable`, `:153-155`); the
terminal consent question is again a fully worded form item (`:127-134`). Pi renders the same content in its
TUI dialog (`plugins/empirica/adapters/pi/src/dialog-view.ts:115-142`), the two hosts sharing one builder.

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
* Both hosts must keep working: Claude Code mediates decisions only through MCP form elicitations, and the
  Claude CLI cannot open a URL a form carries (anthropics/claude-code issue #48164); Pi shows dialogs in its
  TUI through the Python private bridge.
* Immutable bytes are checkable: two readers must get one answer, and the answer must have an exact digest.
* No backwards compatibility is owed inside a run: a state-schema version bump already classifies old run
  files as corrupt, as `empirica.run/2` does today
  (`plugins/empirica/tests/test_run4_binding.py:300-307`, `current_corrupt`).

## Considered Options

* Keep everything in the forms (rejected: the observed outcome — too long to check, provenance rarely opened).
* Markdown documents instead of JSON (rejected: no machine-checkable structure, so no exact digest of the
  decision surface; the user chose JSON).
* YAML documents (rejected: another dependency and no canonical byte form; JSON already has one owner,
  `core/canonical.py`).
* Digest recorded only in the sidecar file and the form, not in state ("not-P", rejected: the record could not
  prove which document a human decided on without trusting the adapter or the file system; the decision's
  fingerprint covers only the payload the adapter submits, `core/governance.py:57,287`).
* A recorded "what the dialog stood beside" block inside the digested document — reviews left, the human-surface
  observation, a separate budgets-used map (rejected, review-1 finding 1: the core re-renders the document and
  refuses a mismatch at every decision, but `present` itself appends a receipt that the interaction counters
  count (`application/governance.py:103-110`, `core/governance.py:173-176`), and `trusted_human_surface` rewrites
  `human_surface` between calls (`application/transaction.py:417-419`), so a digest over those values changes under
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
  the `proposal_digest` the decision binds — `core/governance.py:70-76` — and authorises later automatic
  acceptances), every budget with used/minimum/maximum, the amendment and its `(was …)` priors on a
  `confirm_amendment` document, and a `context_when_shown` block of **run-fixed invocation facts only**
  (host, signal, interactive, delegation, audit_retrieval — `core/projection.py:299-305`; recorded at
  StartRun and never rewritten, unlike `human_surface`, which `trusted_human_surface` rewrites,
  `application/transaction.py:417-419`). The block is marked **not part of the decision**: it explains what
  the dialog stood beside. Nothing volatile is inside it — no `reviews_left` (the `present` receipt changes
  it, `core/governance.py:173-176`), no `human_surface` (rewritten between calls), no separate budgets-used
  map (`budgets[].used` already carries it): the form shows reviews-left live from the dialog instead
  (`contracts/empirica/v2/shared-defs.json:383-391`, `plugins/empirica/adapters/governance.py:79-86`).
* **evidence** — what the end-of-pass evidence confirmation batch asks, and the terminal "end this run
  without converging" consent (ADR-0067): every record with its source, quote, verification class,
  **`claim_text`** (checked against the record's `claim_digest`, the digest binding that claim's id and
  text, `core/evaluation.py:277-280` — the text is the content the human judges a quote against) and
  **`outcome`** (supporting or refuting — a human cannot judge a quote without knowing what it claims to
  be), the "why no deterministic check" reason where one exists, every claim with its text, and the
  terminal consent's status and unresolved residue
  (`plugins/empirica/adapters/confirmation.py:143-176`, `plugins/empirica/core/evaluation.py:1443-1525`).

Which rows count as the terminal residual content is **selected by one core function** from a GetRun-shaped
snapshot — the run view's active obligation rows that are not satisfied, excluding the terminal consent row
itself (`core/projection.py:139-147` for a row's shape, `:188-205` for the active row set, whose content
depends on the snapshot's command, which is why the GetRun shape is the one source; implemented in PR 10, the
presenting leg) — so the presentation and the consent cannot disagree on what ending now would leave
unresolved.

### The question and the amendment's priors

Which configuration form is shown is an **explicit `question` discriminator in the document** — `approve` or
`confirm_amendment` — derived by the core, never by the adapter: it is `confirm_amendment` iff some receipt
has `outcome == "amend"` and `plan_revision + 1 == governance.plan_revision`; otherwise `approve`. The
governance state word is **not** the discriminator (`HUMAN_WAIT_STATES` includes `rejected`, a state in which
a re-present is admitted, `core/governance.py:189`): the most common edit — a human amendment before the
first approval — goes through `revise`, which leaves the state `pending` whenever `approval_kind` is `None`
(`core/governance.py:137-138`); an author revision after the first approval yields `revision_pending` with
no human amendment (an author `configure_run` goes through the same `revise`,
`core/evaluation.py:1641-1656`); the adapters themselves already show the review
form whenever no prior dialog exists (`adapters/governance.py:233-234`). The document still carries
`governance_state` as information, covering `pending`, `revision_pending` and `rejected`.

The confirm document's `(was …)` priors are not derivable any other way — the state's proposal has already
been replaced at the amend commit (`application/governance.py:82-97`) — so run/4 records them
**on the receipt the amendment finalises**: `receipts[].superseded_budgets`, written by the sole receipt
writer (`application/governance.py:103-110`) from `state.governance["proposal"]["budgets"]` — read from the
state, not the already-revised `governed` — and `null` for every other outcome. The invariant checks:
non-null if and only if `outcome == "amend"`, and its keys are exactly the ceilings
(`core/governance.py:46`).

### Deterministic bytes and the digest

The core renders the document as deterministic pretty JSON: sorted keys, 2-space indent, one trailing
newline, literal UTF-8 (no ASCII escaping), built on the one canonical JSON owner so it inherits the old
guarantees — string keys are sorted, non-finite floats and unordered containers are rejected, one writer
(`plugins/empirica/core/canonical.py:13-41`; the pretty renderer is new code beside it). The
`document_digest` is the `sha256:`-prefixed SHA-256 of exactly those bytes, serialized and verified the way
every other digest in the core is (`core/canonical.py:34-37`). Because the rendering is a pure function of
run state, two readers get byte-identical documents.

**Every free-text field is display-escaped through the core's one escaper** — `safe_text`, which doubles a
backslash and turns bidi and zero-width characters into their `\uXXXX` escapes rather than emitting them
literally (`core/projection.py:287-293`): goal and rationale arrive through it with the dialog, and the
builder applies it to every remaining free-text field (claim text, citation, source_ref, the
"why no deterministic check" reason, a residual's `required` line). A bidi override or a zero-width space
must never reach the file a human opens in a terminal — this document is the one surface meant to be trusted.
The raw wording stays bound by its digests (`claim_digest`, `statement_digest`), which the escape cannot
change because the digests are computed before the document is built.

**The core hands the adapter the rendered document as `document: {text, digest}`** — the exact rendered
UTF-8 string plus its digest, published in the private presentation answers (below). No adapter or transport
ever re-renders a dict, so there is exactly one renderer of the decision surface, and the private response
schemas validate the pair as a string plus a `digest256`
(`contracts/empirica/v2/response.schema.json:2413`, `contracts/empirica/v2/request.schema.json:275`) —
which is also why `hitl.schema.json` stays source-only and **not vendored**:
nothing in a private response is ever validated against the hitl schema, only against the string+digest pair.

### The digest is recorded where the decision is recorded ("P")

The core renders the document and verifies the digest; the decision is only admitted when it carries the
digest of the document the core renders **then**. A mismatch or a stale run is refused with nothing spent,
the way a decision bound to an older revision is refused today
(`plugins/empirica/core/governance.py:282-331`, `governance.stale_proposal` at `:307-308`). The accepted
digest is recorded in the run's own state — `governance.receipts[].document_digest` in `run.json`
(`contracts/empirica/v2/state.schema.json:396-425`; the sole receipt writer is
`plugins/empirica/application/governance.py:94-99`) — and in the evidence presentation and evidence
confirmation artifact bodies, beside the `receipt_id`, `key`, `record_ids`, `claims` and stamps they already
carry (`plugins/empirica/core/evaluation.py:1066-1093` and `:1023-1050`). The record, not the adapter, names
what was decided on. Rejected alternative (*not-P*): recording the digest only in the sidecar/form, since the
record must prove which document a human decided on without trusting the adapter or the file system (the
fingerprint covers only the submitted payload, `core/governance.py:287`, and the sidecar lives in a
machine-local directory the adapter writes).

### The write-first flow

The order is fixed, and spending follows the successful check, never the reverse:

1. **Ask** — the adapter asks the core for the document. The ask itself spends nothing and writes no receipt,
   and the context leg of the same transaction may commit `governance.context` when the
   host's ingress or author observation changed (`application/governance.py:40-49,103-104`) — that commit
   changes no digested content, so the document the answer carries stays valid. For configuration, the
   private `governance_context` presentation result carries `document: {text, digest}` beside the private
   dialog it already carries (`application/v2.py:114-122`, `governancePresentation`,
   `contracts/empirica/v2/shared-defs.json:383-391`). For evidence, the carrier is the private
   `trusted_evidence_presentation` **and** `trusted_evidence_confirmation` responses
   (`application/v2.py:132-150`, `application/transaction.py:384-390`). At this stage (PR 10, the expand
   step) the member is staged so today's adapters keep working: **absent** keeps today's behaviour
   exactly (the legacy adapters of the PRs before the binding, removed by the contract step below);
   **`null` is the explicit fetch** and **a wrong digest a mismatched call** — both refused with
   `evidence.document_mismatch`, nothing spent, nothing admitted, and the refusal's private result
   carries `document: {text, digest}` for the batch named by `key` (a confirmation's key is its own
   presentation's) — so the refusal **is** the fetch: the adapter writes the document it names and
   repeats the call with the digest (PR 11/12's adapters; the presentation leg already retries once,
   `adapters/confirmation.py:299-330`). A confirmation must also name the digest its own presentation
   recorded — a presentation that recorded none (legacy) confirms only the legacy way. The public `GetRun` run view, `GetArgument` and the evaluation
   results never carry the document: its content is not secret, but its digest and path have no reason to
   become an author-facing contract.
2. **Write** — the adapter writes the document bytes to
   `$EMPIRICA_HOME/projects/<p>/runs/<r>/gen-<n>/hitl/<sha256>.json` **once**: `O_EXCL`, mode `0444`; if the
   file already exists, its bytes must be identical, else the adapter refuses. The directory is the one
   beside-the-record location the bridge already owns per generation (`gen-<n>`: the timeline and the
   location record, ADR-0070, `plugins/empirica/adapters/state/repository.py:36,89-96`,
   `plugins/empirica/adapters/state/timeline.py:34`); none of today's state writes are write-once
   (`adapters/state/fsio.py:98-122` writes 0600 temp + rename; a new sibling is the implementation, the
   symlink refusal at `fsio.py:78-96` is the pattern).
3. **Present** — the adapter sends `present` with the `document_digest`; the core recomputes the document and
   refuses a mismatch, and a stale run is refused with nothing spent — a mismatch here spends nothing
   either: the reason-code ordering below puts the stale gates first and a mismatch last.
4. **Show** — the form shows a short summary plus the document's path and digest (below).
5. **Decide** — the submitted decision carries the `document_digest`, which the core checks again before
   admitting it (the check extends the decision gate that already binds a submission to its run, revision and
   proposal digest, `core/governance.py:282-331`).

### What the forms keep

The forms keep exactly what is being judged: the numbers (budget used and allowed
(`adapters/governance.py:79-86`)), the amended values with their `(was …)` priors (`:110-115`), the first
line of the goal, the evidence quotes (the quote is the fact a human confirms or rejects,
`adapters/confirmation.py:399-409`). The rationale, the claim text and the "why no deterministic check"
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

### Hosts

* **Pi**: the Python private bridge writes the document and returns path and digest; the TypeScript dialog
  only displays them (`adapters/pi/private_bridge.py:99-106` is the same hand-back today — receipt id and
  items; the handler list at `:133-149`).
* **Claude**: the MCP server writes the document before eliciting
  (`plugins/empirica/adapters/mcp_server.py:95-96,135-138`). The Claude CLI supports form-mode elicitation
  only — a form cannot open the document for the human (URL mode is unsupported:
  anthropics/claude-code issue #48164) — so the form references the document by path, never a link.

### State, requests and compatibility

The state schema becomes `empirica.run/4` (`contracts/empirica/v2/state.schema.json:32-33`; the const drives
classification, `plugins/empirica/application/protocol.py:45`,
`plugins/empirica/application/run_state.py:71,160`). There is **no backwards compatibility**: run files
written as `empirica.run/3` classify as corrupt, exactly as `empirica.run/2` does today
(`plugins/empirica/tests/test_run4_binding.py:300-307`). The public wire keeps its axis `empirica/v2`; the
requests gain `document_digest` — on the governance decision payload, the evidence presentation payload and
the evidence confirmation payload (today required: `run_id`, `receipt_id` and `proposal_digest`/`key`/
`decisions` respectively, `contracts/empirica/v2/request.schema.json:1278,1571,1662`). It is **optional and
staged** (its value is `null` or `sha256:<64hex>`, PRs 07/10): absent keeps today's behaviour exactly and is
checked whenever present; `null` is the explicit fetch and a wrong value a mismatch. The contract step
(PR 13) makes the member required as `string | null`, so "absent" disappears and step 1's fetch sentence
becomes literally true — and it becomes
required for every `host_ui` receipt and every human evidence record, absent for `auto` (the same discipline
`presentation_fingerprint` already has: `None` if and only if `auto`,
`plugins/empirica/core/governance.py:353-356`). Because the payloads change, the public-tool schemas and the
vendor copies follow (`make empirica-public-tools-sync`, `make vendor-contracts`).

## Consequences

* Good, because the forms shrink to the decision and its reference: short forms get checked, and the record
  proves exactly which words were decided on.
* Good, because one owner renders the decision surface: no host re-words a trust fact, and two readers of
  one document get the same bytes.
* Good, because nothing volatile is inside the digest: no value the human's own answer or the host's surface
  observation can change between the fetch, `present` and the decision, so the core's recompute at the
  decision always matches the document the human read.
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
* Bad, because a human must open a file to read the detail; on Claude the form cannot do it for them.
* The observer listing of the documents (surfacing the digest and the path of a run's HITL documents) is an
  `empirica-ui-view/v1` change and a **follow-up**, not part of this record.
* Empirica releases this as a major version bump (**7.0.0**), at the end of the stack, with its upgrade guide.

## Sub-decisions (decided; review-1 confirmed the choices)

### Q1 · How the adapter obtains the document before `present` (decided)

The document travels as the **rendered pair** `document: {text, digest}` in the private presentation answers,
never as a dict an adapter renders: for configuration the `governance_context` presentation result carries it
beside the private dialog (`application/v2.py:114-122`); for evidence the private
`trusted_evidence_presentation` response carries it, with the refusal-carries-document pattern — a call
whose `document_digest` is absent or wrong is refused with `evidence.document_mismatch`, nothing is spent,
the refusal's private result carries the pair for the batch named by `key`, and the adapter writes and
repeats the call (`adapters/confirmation.py:299-330` — the leg already retries once; the fetch–present window
cannot drift because the refusal is the fetch). The context ask itself spends nothing and writes no receipt,
but may commit `governance.context` when the host's ingress or author observation changed
(`application/governance.py:40-49,103-104`) — a commit that touches no digested content.

*Rejected*: a new read-only trusted operation (a `hitl_document` handler) — every trusted operation carries a
payload shape, a private-response validation and guard wiring on both hosts (`application/v2.py:111-150`,
`adapters/bridge.py:234-256`, `adapters/pi/private_bridge.py:133-149`, `request.schema.json:1250` —
`governanceContextPayload` is `author` and `ingress` only); a second answer to the same question adds that
surface to gain nothing. *Rejected*: carrying the evidence document in the public `GetRun` run view,
`GetArgument` or the evaluation results — that would make the document's digest and path an author-facing
contract surface (the public-tool schemas, the contract digest and the author view all move) for content that
is not secret but has no reason to be author-visible.

### Q2 · Byte format details (decided)

Schema-declared content, but **sorted keys** (the order is `core/canonical.py`'s, so the bytes never depend
on a schema's field order or on regeneration), `ensure_ascii=False` (literal UTF-8, the owner's rule,
`canonical.py:18,24`), 2-space indent, one trailing newline, and the document's numbers are integers only
(floats never reach these payloads; non-finite floats are rejected, `canonical.py:14-16`). The
`document_digest` is the SHA-256 of exactly those bytes, newline included. Two additions review-1 fixed:
**every free-text field is display-escaped with the core's one escaper** (`safe_text`,
`core/projection.py:287-293`) — the raw wording stays bound by its digests — and **the core hands the adapter
the rendered `text` plus its `digest`, so no adapter or transport ever re-renders a dict** (the private
response validates the pair as a string plus a `digest256`, `response.schema.json:2413`).

*Rejected*: schema-declaration key order (the order of properties is not a serialization contract; two
builders or a regeneration could disagree — sorted is what the core already guarantees everywhere);
no-trailing-newline (a POSIX text file ends one); compact bytes (unreadable; `canonical_json` is for
digests, not for people); re-serializing through `canonical_json` and pretty-printing an independent copy
(two renderers to keep identical — the pretty form and its digest must come from one code path); raw
unescaped free text beside escaped goal and rationale (a bidi override in a citation would reach the file a
human opens).

### Q3 · The refusal reason codes for a digest mismatch (decided)

Two new reason codes, following the domain-prefixed naming the contract already uses (`governance.*`,
`evidence.*`, beside `governance.stale_proposal` at `contracts/empirica/v2/public-contract.json:1899`):
**`governance.document_mismatch`** for the configuration decision and present leg, and
**`evidence.document_mismatch`** for the evidence presentation, the confirmation and the terminal consent.
Both take the `host_recovery` disposition (like `stale_proposal`) and carry the message-and-next-actions the
contract rows give, so the recovery table (`plugins/empirica/skills/empirica/references/recovery.md`,
regenerated from the contract by `make empirica-recovery-reference`) grows its rows from the contract alone.
The evaluation ordering is fixed: `governance.stale_proposal` and the evidence key/staleness gates come first
— a document mismatch is reported only when the proposal and revision (or the batch `key`) still match — **a
mismatch at `present` spends nothing**, and the recovery row for the decision leg says that the present
receipt is already spent, so the recovery is to fetch the document and present again, costing **one review
unit** of `PROPOSAL_INTERACTIONS = 3` (`core/governance.py:22`, the counters the refusal words stay
faithful to, `core/governance.py:168-177`).

*Rejected*: reusing `governance.stale_proposal` (its words say the decision was bound to another
revision/digest of the *proposal* — a document mismatch is a different fact with a different recovery: fetch
the document, do not re-propose); one shared `hitl.document_mismatch` (the contract's reason codes are
domain-prefixed, and the two flows offer different next actions); reporting a mismatch before the stale
reasons (the mismatch would hide the more useful fact that the proposal or key itself is stale).

### Q4 · What the short forms show (decided)

The digest as `sha256:` followed by its first 12 hex characters (the scan length the repo already uses for
identifiers, `short_id`, `ID_SIZE = 12`, `application/observer_index.py:66,75`), and the document's
**absolute** path on **its own line, exempt from wrapping** — the path is a copy-paste surface, wrapping
breaks the paste, and a realistic path under `$EMPIRICA_HOME` (roughly 160–200 characters) plainly does not
fit the wrapped text widths anyway (`adapters/confirmation.py:48-66`, the one owner of the dialog lengths);
the digest line and the exempt path line are pinned by the form goldens. The sidecar returns an absolute path
even under a relative `EMPIRICA_HOME` (the home is made absolute at construction, never resolved — it must
not follow a symlink). The full digest and the document path are on the decision record; the form's digest
is for the eyes, not for verification.

*Rejected*: the full 64-hex digest in the form (spends the one line the path needs, and the full value is in
the document and in the run's state, not needed on glass); a `~`-relative path (shorter, but the operator's
`$EMPIRICA_HOME` need not be under their shell's `~`, and a printed path that a human must expand by hand is a
false convenience for a copy-paste surface; an absolute path is pasted as-is); wrapping the path into the
description text (a wrapped path is a corrupted paste).

## Confirmation

Not yet shipped; this is the record of the workstream's decided shape. Its PRs confirm each part as they
land, red-first: `hitl.schema.json` and its example fixtures with validator checks (schema), the pure
builders and the byte renderer with goldens over schema-valid documents (core), the write-once
`gen-<n>/hitl/` sidecar with O_EXCL/0444/identical-bytes tests (state adapter), `empirica.run/4` with the
state const, the 16 state fixtures and the non-run/4-corrupt classification (state), the governing checks —
recompute-then-refuse on present and on decision, the reason codes with recovery rows — with replay and
stale negative controls (core, application), the Claude forms with `test_governance_hosts` and the dialog
golden, the Pi dialog with its 10 rendered goldens and the bridge check (hosts), the require-digest
conditionals with negative controls (contracts), and release acceptance at the top of the stack: one native
requalification of the merged tree, the operator's closeout at each native boundary, then this ADR flips to
*accepted* with the upgrade guide.
