# Upgrading to Empirica 7

Empirica `7.0.0` uses public wire `empirica/v2`, persisted state `empirica.run/4`, observer view
`empirica-ui-view/v1`, and the human-decision document kind `empirica.hitl/1`. Install the matching
Claude, Codex, or Pi package as a unit; do not mix plugin sources or vendored contracts from
different builds.

## Who is affected

* Every installer of the plugin package: the persisted-state discriminator moves from
  `empirica.run/3` to `empirica.run/4`, and every human decision made through a host form (HITL)
  now binds an immutable document.
* Any operator with a run in progress under 6.x, and any operator holding old run directories
  meant to be read again.
* Host surfaces that render the governance and evidence forms: the forms are short, and the detail
  they used to carry is now a file they name.

## Start fresh runs

Empirica 7 reads only exact `empirica.run/4` documents. Documents with any other state
discriminator are rejected as corrupt (`run.corrupt`) and are not rewritten; `empirica.run/3`
included. **A run in progress under 6.x cannot be resumed**: its state classifies as corrupt, and
no migration, repair, or carry-over of approval exists. Start a fresh run after installing
7.0.0 and let it re-decide its configuration; do not copy operational state, backend bindings,
request artifacts, or mutable pointers between generations. The same rule every state bump has
carried (`empirica.run/2` before it) applies.

Spike authority is unchanged from Empirica 6: a new run binds it once at `StartRun` — either
`{ "kind": "spikes_unavailable" }` or an exact generic qualified binding for
`docker-isolated-v1` or `docker-networked-v1` — and every operation resolves and revalidates the
run's historical record. After changing plugin code or environment, qualify and install again
through the operator commands; the new pointer affects future runs only. The exact boundaries are
the [Empirica 6 upgrade guide](upgrading-empirica-6.md) and [hosts](hosts.md).

## Human decisions bind an immutable document

Every human decision made through a host form — the run-configuration approval, its amendment
confirmation, the end-of-pass evidence batch, and the terminal consent to end a run without
converging — is now made against a core-rendered, immutable JSON document
(`empirica.hitl/1`). The bytes are deterministic (sorted keys, 2-space indent, one trailing
newline, literal UTF-8), named by their own SHA-256 digest, and never rewritten.

* **Where the documents live.** Each decision's document is written once, beside the run's
  record, at `<EMPIRICA_HOME>/projects/<project-id>/runs/<run-id>/gen-<generation>/hitl/<sha256>.json`.
  The `hitl` directory is owner-only, and each document file is mode `0400` (owner read-only).
* **They are evidence.** The digest of the exact document a human decided on is recorded in the
  run's own state (`governance.receipts[].document_digest` and the evidence artifacts), required
  on every `host_ui` receipt and forbidden for `auto`. Never edit, move, re-create, or delete one:
  the core re-renders the document and refuses a mismatch at every decision, so an edited document
  makes the decision it names impossible to record. The documents are the record of what was
  decided, not scratch; an interrupted publication leaves only inert, ignored temporary
  siblings — never a torn or partial document — and a retry publishes the same bytes again.
* **Reading them.** No special tooling is needed: the files are pretty-printed JSON. Opening one
  is the way to read the detail a form no longer carries (the full sizing rationale, each record's
  claim text and its "why no deterministic check" reason, the residual list of a terminal stop).

## The forms are short and name the document

The native forms on Claude Code and Pi keep only what is being decided: the budget numbers (with
their `(was …)` priors on an amendment), the first line of the goal, and each evidence record's
source and quote; a needs-decision claim keeps its full text. The sizing rationale, the per-record
claim context, the no-deterministic-check reasons, and the terminal residual list moved into the
document. Each form and item dialog carries the document's **Details**: when an observer of the
same home is live, a short one-line link to the decision in the browser UI
(`http://127.0.0.1:<port>/app/?run=<compact id>&decision=<12 hex>`, verified before it is shown)
plus the document's short digest (`Document sha256:<first 12 hex>`). Without a live observer the
form keeps the honest fallback — its own host's command to open the UI, the run's compact id,
and the exact **absolute path**. The path is a copy-paste surface: Claude prints it on one
unwrapped `File:` line, while a Pi dialog too narrow for it wraps only at `/` boundaries, never
cutting a segment, so the printed pieces join back into the exact path; the path is dropped
only when a verified link is shown in its place.

New refusal reasons bind the document digest, both with `host_recovery`:

* `governance.document_mismatch` — the configuration dialog named a document that no longer
  matches the run's state. Resubmit `configure_run` so the host shows a current one; a refused
  present spent nothing, and a re-present after a refused decision costs one review unit (3 per
  configuration).
* `evidence.document_mismatch` — the evidence presentation, its confirmation, or the terminal
  consent named a different document than this batch renders, and nothing was spent. Report again
  (`report_convergence`) to have the same batch shown with the document the host binds then, or
  stop with the residual. A batch already shown is recovered only by a fresh showing, never by
  repeating the refused call.

A confirmation the service refused — a Block, a Fault, or a submit that never landed — admitted
nobody's answer: the run reads `host_failed` and the recovery is to show the batch again. It is
never recorded as the human's decline.

On Pi, the end-of-pass evidence dialog fails closed **per item** (PR 18): a cancel or timeout at
item k keeps every answer completed before it, sends that item and the rest as `later`, and submits
what was completed once; Esc at an item's optional note never cancels — the same item's choice is
offered again — while Claude's one shared form is unchanged.

## Qualified hosts

The host versions 7.0.0 was qualified on are recorded here after the operator's native
requalification of this release tree completes, together with the exact Docker profile
qualification records.

QUALIFIED-ON: pending operator requalification
