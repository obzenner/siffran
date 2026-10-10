---
number: 67
title: Verify every research record by its class and surface every confirmation it still needs
status: proposed
date: 2026-10-06
tags:
- empirica
- evidence
- verification
- trust-boundary
- hosts
links:
- target: 20
  kind: Amends
- target: 25
  kind: Relates to
- target: 42
  kind: Amends
- target: 66
  kind: Relates to
- target: 68
  kind: Amended by
- target: 69
  kind: Amended by
---

# Verify every research record by its class and surface every confirmation it still needs

## Context and Problem Statement

Fold 1 requires every gating claim to have research whose `source_ref` and verbatim `citation` let someone retrieve and check the source (ADR-20; `references/evidence.md:7-12`). ADR-66 checks this at submission for one case: a workspace file. All other research is left to the independent audit, which "independently retrieves every citation" (`references/audit.md` step 4). The surface survey (`.research/citations/reports/L1…L5b`) found that this promise is not backed:

- **Retrieval is assumed, not observed.**
  - The Pi auditor's effective tools are `read, grep, find, ls` in every reviewed pi-subagents version (captured preflight `contract.tools.effectiveAllowlist`, `adapters/pi/test/fixtures/preflight-0.{50,64,74,75}.0.json`). It cannot fetch a URL, and Pi children do not inherit extension tools (pi-subagents `docs/extension-api.md:312-313`).
  - The Claude auditor declares `WebFetch` (`agents/empirica-auditor.md:4`), but nothing observes that it is available or permitted. Claude preflight returns `tools: {}` and `probed_optional: False` (`adapters/claude/preflight.py:21-28`).
- **Requirements surface late.**
  - Nothing is shown when a URL or runtime record is submitted.
  - A citation the auditor cannot retrieve is discovered only as an audit failure, after the budget is spent (L1 §"Requirement surfacing"; L3 §3; L4 §4).
- **Runtime evidence is unqualified.** `runtime` research is accepted like any other source (`SKILL.md:130-146`). An observation that could have been a deterministic spike is accepted without saying why it is not one.
- **There is no human ingress beyond configuration.**
  - The only route for a human decision is the run-configuration approval: `governance_context`/`governance_decision`, receipts bound to `proposal_digest` and `plan_revision` (`core/governance.py:243-335`; `application/v2.py:105-121`).
  - Before this change a `needs-decision` claim was reported with a human next action, but no ingress recorded the answer, so such a claim could never converge.
- **The UI shows nothing about verification.** The observer reports evidence only as `spike | research | none` (`core/observer.py:26-29`). It shows neither how a record was verified nor what it still needs (L5a).

Hosts differ in what they can represent. The design must not weaken a requirement on a weaker host; a missing capability may only add human (HITL) steps.

## Decision Drivers

* **Core first.** The core defines what each record needs; adapters only map host capabilities onto it (ADR-30).
* **Never silently weaker.** A host that cannot do something adds a human confirmation step instead. If it has no human ingress either, the requirement stays open and the run can only stop as residual.
* **Every requirement to complete the run is surfaced as soon as it exists.** It appears in the run view and to the human when the record is submitted, never first at audit time.
* **Observed, not assumed.** A host's audit retrieval capability is an observation recorded at start and re-observed at launch, like `host_runtime` (ADR-42 amendment).
* **Deterministic machine evidence stays the only machine approver.** A spike is preferred over a runtime observation whenever one is possible.
* **One receipt model** for human decisions, distinct from configuration receipts so the two can never be confused.
* **Interrupt rarely, decide item by item.** Industry human-in-the-loop (HITL) practice gathers the pending items at one checkpoint, but each item still gets its own decision:
  * LangChain's HITL middleware: "each action requires a separate decision", and calls that need no decision "are never added to the interrupt batch" (docs.langchain.com/oss/python/langchain/human-in-the-loop).
  * The OpenAI Agents SDK also surfaces pending approvals together, run-wide, and lets unresolved items wait: "You do not need to resolve every pending approval in the same pass" (openai.github.io/openai-agents-python/human_in_the_loop).
  * Alert-fatigue research shows why:
    * acceptance fell 30% for each additional reminder per encounter, and repeats hurt most (Ancker et al. 2017, BMC Med Inform Decis Mak);
    * interruptive pop-ups were accepted least (Hussain et al. 2019, JAMIA);
    * a clear rationale reduces inappropriate overrides (Poly et al. 2019, JMIR Med Inform).
* **No backwards compatibility.** Contract, artifact, store and receipt shapes change in place; earlier runs and receipts are not migrated.

## Considered Options

* **Verify everything verbatim at submission.** Rejected: a URL, a book or a runtime observation cannot be checked by the service.
* **Leave everything that isn't in the workspace to the audit (status quo plus ADR-66).** Rejected: retrieval is assumed, failure is late, and on Pi it is certain for web sources.
* **Have the host fetch URLs at submission.** Rejected for now: it adds network access and pages that change, and it still does not judge the source. It remains open as a future retrieval capability.
* **Classify each record by how it can be verified, require the matching confirmation, observe what each host can do, and fall back to a human receipt.** Chosen.

## Decision Outcome

Chosen option: classify each record by how it can be verified, require the matching confirmation, observe what each host can do, and fall back to a human receipt, because it keeps every requirement explicit on every host and turns each host limitation into a surfaced human step rather than a silent gap.

### Verification class (core, derived)

The core derives each research record's class from its immutable `source_kind` and `source_ref`. The class is not stored, so artifact ids do not change. The rule has one owner, `core/citations.py`.

| class | which records | verified by | when |
|---|---|---|---|
| `verbatim` | `source_ref` is a repo-relative workspace path (any kind) | the service, ADR-66 | at submission; a mismatch is refused |
| `retrievable` | a URL (`web`, `docs`), or an absolute path outside the workspace | the audit, if this host's auditor is observed to retrieve that kind; otherwise a human | the requirement is shown at submission; the human is asked at the end of the pass |
| `observed` | `runtime` with no retrievable ref | a human, always | the requirement is shown at submission; the human is asked at the end of the pass |

* **Tighter refs.** A `code`, `docs` or `web` record whose ref is neither a workspace path nor a URL or absolute path is refused at submission with `research.citation_unverified`, `problem: source_ref_form`. The evidence rule already demands enough provenance to retrieve the source.
* **No bypass by spelling.** An absolute path that names a file inside the workspace is refused the same way, so the verbatim check cannot be skipped by writing the path absolutely; the message says to cite the repository-relative path. The core receives the workspace root as a fact from the application and keeps the rule.
* **URL schemes** match without regard to case.
* **`observed` is a fallback.** A `runtime` record must carry `payload.spike_unavailable`: a nonblank reason a deterministic spike cannot establish this observation. Without it the record is refused (new problem code `spike_reason_missing`). The reason is shown to the human verbatim and confirmed with the record.

### Confirmation requirement and obligations (core)

* **The pending confirmation is part of the claim's obligation.** A `retrievable` or `observed` record that is not yet confirmed shows in its claim's obligation at once, with the route it will take:
  * `confirm: audit` when the host's observed auditor retrieval covers the record's kind;
  * `confirm: human` otherwise;
  * `confirm: unobserved` when a human could be asked but the host has not yet looked for a dialog surface (`invocation.human_surface` is `unobserved`; F7). The route is `unobserved` only for a run with a human configuration ingress that is interactive (or not known to be otherwise) and not delegated; the host looks inside `report_convergence` and the route becomes `human` or `unavailable`. Nothing is presented while it is `unobserved`. The author is told a human is asked at the report if the host can show a dialog, never that nobody can be asked and never that one will be.
  * `confirm: unavailable` when no human can be shown a dialog now: no human configuration ingress, a delegated or non-interactive run (at once, whatever the host would observe), or a host that observed there is no surface. The route is `human` only when the configuration capability is human, the host has observed that it can show a dialog (`invocation.human_surface`), and the run is neither delegated nor non-interactive. The obligation then names what is missing and how to get it, such as running the session in an interactive UI.
* **One reason, one human next action.** The reason is `evidence.confirmation_required`, with parameters `route` (`human | unavailable | unobserved`), `record_ids` and, when some are unanswered, `claim_ids` of the `needs-decision` claims, so the whole batch is one reason. Waiting decisions are asked on their own too: a run whose only open item is a `needs-decision` claim returns this reason with no records. There is no separate decision reason or next action. The route `audit` appears only on an obligation row, because an audit-routed record never holds the run. The human-owned next action is `human.confirm_evidence`; it is offered only for route `human`, since with route `unavailable` nobody can be asked and only `residual.accept` remains. The author's own next action is `convergence.report` (`report_convergence` with the default intent): it leads the list while a human can be asked and nothing has been shown yet (route `unobserved`, or route `human` with `prompt_error: null`), ahead of `residual.accept` (`intent=stop`); it is not offered with route `unavailable`, after the batch was shown (`already_presented`), when the interactions are spent (`interaction_limit`: a limit the run already knows is said before a surface the host has yet to look for or has found, so `unobserved` and exhausted, and `human` and exhausted, read `interaction_limit`; `unavailable` stays `unavailable`, since nobody can be asked whatever the limit, and both end in a stop), or when the host reported that it could not ask (below). The waiting line for `unobserved` reads "a human is asked when you report, if this host can show a dialog" (`prompt_error: unobserved`); the observer UI shows it as an ordinary pending question. One rule owns whether the report is offered (`confirmation_next`): exactly when `prompt_error` is null or `unobserved`.
* **A host that fails to ask says so, on the same call** (A6-1). `report_convergence` asks of its host two things: record whether a dialog can be shown (the *observation*) and show the batch (the *presentation*). Each returns a typed outcome, `ok` or `failed` with a reason, never a bare Boolean and never a silently unchanged result. A failed outcome is sent with the evaluation as `EvaluateRun.host_failure` (`surface_unrecorded | presentation_failed`), a fact about that one call that is never stored, spends nothing and approves nothing. The core projects it as the batch's `prompt_error`, so the same machinery that words every other reason a dialog cannot come words this one: "the host could not … so nobody was asked; stop honestly, or report again after a change". `convergence.report` is not offered, so the first actionable `next:` is `residual.accept` (`report_convergence intent=stop`). What the core already knows outranks what the host reports: `unavailable`, `already_presented` and `interaction_limit` are worded as themselves. When the evaluation that carries the failure itself raises or answers a typed Fault, the host applies what it knows of its own call to the response in hand through the core's own projection rule (`project_prompt_error` / `project_host_failure`, a pure function that also owns `convergence.report` in every confirmation row), so the answer is never the stale one that offers a report which would ask nobody; a batch that was shown but whose re-evaluation fails reads `already_presented` the same way. A literal agent that follows `next:` ends in at most two calls: the report, then the stop, including when the re-evaluation after a failed *presentation* raises. One path is different: when the *observation* fails and the single evaluation that carries it also raises or answers a typed Fault, there is no response to project. The call is then a closed public tool error ("Empirica bridge unavailable." or the typed Fault) with no `next:`. Nothing is promised, approved or spent, and an explicit `report_convergence intent=stop` still succeeds. The Claude Stop directive may still direct one report; the response to that report leads to stop. The notice is registry text (`author_view.labels.waiting_surface_unrecorded`, `waiting_presentation_failed`) and carries no text the host supplies, so both renderers print it identically and nothing in it needs fencing.
* **The claim stays unapproved while it waits.** A claim whose only supporting research awaits human confirmation is not approved. Research awaiting audit confirmation still approves the claim, as today, because the audit gate covers it. Convergence also waits for every pending human confirmation, even on a claim that another record already approves.
* **The dossier states each record's class, status and audit duty.** `project_argument` gives every research record `verification: {class, status, retrieve, receipt?}`:
  * `status` is `verified_by_service | confirmed_by_human | rejected_by_human | pending`;
  * `retrieve` is true when the record's kind is within the host's observed `audit_retrieval`;
  * `note` is the human's note to the author, when the receipt has one. The obligation row of a confirmed record carries it too.
* **The auditor fetches whatever it can.** It must retrieve every `retrieve: true` record and verify that the source exists and contains the cited text, including records the service or a human already confirmed. For the rest it judges relevance, and the dossier says a human confirmed them. The rubric fails a claim whose retrievable source is missing or does not contain the citation.
* **The status is bound into the audit.** `argument_digest` binds the confirmation artifacts, and each claim's evidence digest binds the receipts on it, so an audit that predates a confirmation is stale (ADR-25 coupling preserved). The projected `verification` rows are not themselves in the digest: `retrieve` comes from `invocation.audit_retrieval`, which is fixed when the run starts and re-checked when an audit launches. A capability that could change mid-run would have to enter the digest.

### Asking at the end of the pass (core + adapters)

* **When.** The requirement appears at submission, but the human is asked only at the end of a pass, when the author calls `report_convergence`.
  * The service returns `evidence.confirmation_required` only when nothing else blocks the run, so any other claim blocker is reported first and the author keeps working. This happens before the pass is charged, so the block does not spend a pass. **The view is ordered like the gate:** while a confirmation batch is pending for a scoped claim, the view leads with the report or the confirmation and does not offer the audit (`obligation.audit`), because each claim's verification status is inside the audit's argument digest: an audit launched before the human answers is stale as soon as they do, and a spawn is wasted (Q3, finding F-Q3-2) **The audit launch is ordered the same way:** `child_reserve` for an audit is refused with `evidence.confirmation_required` while the gate would ask a human (the batch and `prompt_error` null or `unobserved`, `core.confirmation.ASKABLE`); when nobody can be asked (`unavailable`, `interaction_limit`, `already_presented`) it is admitted (Q4, finding F-Q4-1).
  * **Blocker order within one claim** (A1-5): the terminal states first; then what the author can act on now, missing or stale research and then a missing or stale spike (`claim.spike_missing`, `claim.spike_stale`); last, waiting on a human. The batch is still projected and still presented at report time whatever leads, so a claim with a missing spike and a URL record asks the human at the end of the same pass.
  * The run view always carries the batch as a fact, whatever reason is returned: `confirmation_batch` is null when nothing waits, otherwise `{key, record_ids, claim_ids, records, prompt_error}`, where `records` gives each record's claim, source kind and source ref for the author's view. `prompt_error` is `unavailable`, `unobserved`, `already_presented`, `interaction_limit`, or, when the host reported it for this call, `surface_unrecorded` or `presentation_failed`; null when a dialog may be shown.
  * At each `report_convergence` the host adapter presents the batch exactly when it is non-null and `prompt_error` is null, whatever reason is returned, the same way it handles run-configuration approval after a public response. The human is therefore still asked while the author has other work.
  * **An interactive auto Stop never settles as an evidence wait** (A2-1/A2-2; the turn end by control mode follows the 2026-10-08 amendment and ADR-0068). In Claude the human can be asked only inside `report_convergence` (MCP elicitation), so an auto Stop that settled on an unshown batch would end the turn and nobody would ask. In deliberative mode the human is present, so the turn does end: `paused_for_evidence` hands them the step (tell the agent to report, where they are asked, if this host can show a dialog). When the confirmation is the sole blocker and `prompt_error` is null, an interactive auto Stop blocks (exit 2) with one plain directive (`settlement_notices.evidence_report`): call `report_convergence`, the human is asked in that call; a deliberative turn end pauses with `settlement_notices.paused_for_evidence` instead (ADR-0068). Any other blocker gives the real reason. The Pi report gate presents the batch in-process and settles on the `evidence_wait` notice only after the same call has shown it (or could not).
* **A presentation bounds the batch.** Before a host may submit decisions it records a presentation, as it records a presentation before a configuration decision.
  * **A presentation names the batch it shows** (A1-1). The ingress carries the expected `confirmation_batch.key`; the core admits it only while that equals the current batch key and otherwise refuses with a `conflict` fault and spends nothing. The key digests the batch's contents: each shown record's id (content-addressed, so it binds the evidence digest) and each decision claim's id and wording digest, plus the derivation. The shared presenter reads the batch, builds the items, presents, and on a changed key re-reads and tries once more; a second change shows nothing and spends nothing. So a record that was not on screen is never marked presented, whatever the interleaving.
  * The presentation is keyed on the derivation digest and exactly the ids it shows: the pending `needs-decision` claim ids with their wording digests, then the pending record ids. Its receipt id names it.
  * The core caps the batch at the receipt schema's `maxItems` (64), the only owner of that limit. The order is deterministic: decisions first, in graph order, then records in their claim's graph order and then by submission. The projected batch and the stored presentation name exactly the shown ids; the host shows what the core projects and caps nothing.
  * Sending the same receipt id again is inert. A second presentation of a key that was already presented, or of nothing, is refused with a `conflict` fault.
  * Each presentation spends one unit of the human interaction budget, which is the one count governance already keeps: `core/governance.py` counts configuration receipts and evidence presentations together, and every path that admits or shows an interaction reads that count.
  * So the pending set is shown once per derivation, and at most 64 items per batch. A changed derivation is any change to the derivation digest: a decision, new or changed research, or a changed claim. Confirming a batch changes it, so the next batch is new. A changed derivation, including a smaller pending set, is a new key and may be shown once more. A route change never makes a presented key new. The real bound is the interaction budget together with this rule; it is not `max_passes`, because the block comes before the pass is charged.
* **One decision per item.** For each item the human chooses **Confirm**, **Reject**, with an optional note to the author, or **Later**. There is no "approve all".
* **Nothing is asked twice.** Only records that need a human appear, and a decided record is never asked again; the receipt is bound to the record. **Later**, cancel and timeout leave the items pending. Nothing asks again by itself (a Stop pause or an auto continuation never presents), but the next explicit `report_convergence` shows them again, one human interaction per showing, until the run's interaction limit ends it; the consent to end the run is asked again by each explicit stop, ADR-0063 amendment (G14). The dialog names its subject in every field: a title of at most 40 columns (the short source, or the claim) and the item in the field's `description` (claim ids, a quote prefix, the question), because Claude cuts a title at about 45 columns and collapses the message, whose item lines come before its instructions (the end of a run has its own heading), and one source quoted the same way for several claims is one item whose answer decides every record. The run view then shows `prompt_error: already_presented` (`interaction_limit` once the interactions are spent, which outranks it), the Stop and the gate block with the real reason, and `residual.accept` stays on offer.

### Human receipt (core + application)

* **New ingress.** A private ingress `evidence_confirmation` carries `{run_id, receipt_id, decisions[]}`, admitted only through the profile's human `approval_ingress`.
  * The `receipt_id` is the id of the presentation it answers. The receipt is refused unless that presentation is the current one for the pending set, and each decision must be about something the presentation showed.
  * A decision is `{subject: research, artifact_id, outcome: confirm|reject, note?}` or `{subject: claim, claim_id, claim_digest, outcome: approve|reject, answer?}`; a claim answer is at most 2000 characters and is bound to the claim's wording digest. The service sets `approval_kind` to `host_ui`; the host does not supply it.
  * It records one content-addressed `evidence_confirmation` artifact per decision. A research artifact names the record as `record_id`, because `artifact_id` already names the confirmation itself.
  * A new private ingress `evidence_presentation` carries `{run_id, receipt_id}`, as above.
* **Write and replay rules.**
  * The artifacts enter the claim's active evidence, which changes the evidence digest. An audit that predates a confirmation is therefore stale (ADR-25).
  * Admission follows `_trusted_artifact` and compare-and-set, with the replay and conflict rules of governance receipts: an identical replay is inert; a different receipt for the same record, a receipt id reused for other content, a stale presentation, an unknown or already decided item, and a subject listed twice fail closed. Nothing is admitted unless every decision is.
  * Only the presentation spends a unit of the human interaction budget; the receipt does not.
* **Outcomes.** `confirm` satisfies the record's confirmation. `reject` makes the record inactive and does not refute the claim; rejecting the last supporting record drops the claim to `claim.research_missing`. The human's note is shown to the author on the record and on the claim's obligation row. A rejected decision keeps its answer.
* **`needs-decision`.** The same ingress records the human's answer to the decision in the batch. `approve` approves the claim (human-owned) and `reject` discards it. The claim is `blocked` until it is answered. If an audit later fails, the answer stands because it is bound to the wording digest, and the run re-audits without asking again; a changed wording asks again. This closes the gap where a `needs-decision` claim could never converge.
* **Human-owned claims sit outside producer independence.** A `needs-decision` claim a human approved, with no evidence, has no producer, so it contributes none. When every reviewed claim is human-owned and the auditor's identity was observed by the host, independence is `human_owned`, a named outcome of its own, and it is eligible for convergence; an audit over the argument is still required, and with no host-observed auditor identity the verdict stays unverified. A human-approved claim that also has research keeps its research producers.

### Auditor retrieval capability (adapters, observed)

`StartRun` records `audit_retrieval`, a closed set of `workspace | local | web`, in `invocation`, where the run view shows it; it is not part of the opaque `host_runtime`. Audit launch re-observes it against the durable plan, and any divergence rejects the reserved child and refuses the launch, as an owner change does today. Pi derives it from the live preflight (`adapters/pi/src/audit-retrieval.ts`) and Claude from the plugin-owned auditor definition (`adapters/invocation.py`).

* **Pi.**
  * Map the canonical auditor's preflight `contract.tools.effectiveAllowlist` through a reviewed table from tool name to kind: `read` gives `workspace` and `local`. A web kind comes only from a reviewed web-fetch tool that an extension supplies to the child (`tools` + `extensions`/`subagentOnlyExtensions`).
  * The subagents matrix pins the table per reviewed version.
* **Claude.**
  * Claude cannot preflight child tools, so `audit_retrieval` is `{workspace, local}`, read from the plugin-owned auditor definition.
  * `web` is never claimed, so URLs route to `confirm: human`. Observing the auditor's own `WebFetch` calls is deferred.
* **Codex.** No auditor and no human ingress, so every `retrievable` and `observed` record shows `confirm: unavailable` at submission. This is consistent with its `wip_unsupported` profile.

### Human surfaces (adapters)

Each batch item shows the claim, the source, the complete verbatim quote (no truncation, wrapped as needed, in the same dialog as the answer) and, for `observed`, the `spike_unavailable` reason. **Later** leaves the requirement open and grants nothing.

* **Pi.**
  * The end-of-pass batch is a `pi_ui` dialog built from standard `ctx.ui.select`/`input` dialogs, which RPC forwards, rather than a custom component.
  * In print or JSON mode it is `unavailable`, and the open requirement is notified.
* **Claude.**
  * A new MCP form-elicitation schema, separate from the configuration form.
  * Without form elicitation, or for a delegated or non-interactive run, it is `unavailable`.
  * **A failed observation fails closed** (A1-2, host side only; A2-1 removed the cross-process note). When a surface write fails (an exception or a typed Fault, on the first write or on the fallback), the mediator shows nothing for that call. A stale stored `human` can then only change the wording of the route; it can never cause a pause, because on a first Stop the Claude Stop does not pause for evidence (see the 2026-10-08 amendment), so Stop and the mediator need no shared state and no machine-local note exists (the earlier `surface_mark` note erased or kept by overlapping observations is gone). Pi needs none either: its gate and its surface write run in one process, the recorded/unrecorded result is a value computed fresh by that same call (`observeSurface`), and the gate sends `surface_unrecorded` with the evaluation and blocks with the registry notice and the stop next action, so no second reader exists. A failed *presentation* is guarded the same way on both hosts (A6-1): Claude evaluates again with `presentation_failed` and its response leads to stop; the Pi gate does the same and never settles as an evidence wait for a batch nobody was shown.
  * Both hosts report whether they can show a dialog (`human_surface`) through a private ingress at every `report_convergence`; the latest observation counts. Claude cannot know at the start hook whether the client will elicit, so it starts `unobserved` (F7: it previously started `unavailable`, which told the author that no human could be asked before the host had looked, and the agent stopped with nobody asked). Pi sends `ctx.hasUI` at start and at every report, so a Pi run is never `unobserved`. Codex has no human ingress and starts `unavailable`.
* **Observer UI.**
  * The contract gains per-record `verification` (class, status, route) and pending confirmations in the run detail.
  * The UI words them; it never derives them (CONVENTIONS X1).

### Consequences

* Good, because every record's verification is explicit and machine-checked where possible. What is not machine-checked is confirmed by an observed auditor retrieval or by a human, never assumed.
* Good, because the author and the human learn what a record still needs the moment it is recorded.
* Good, because runtime observations stop standing in for spikes without a stated, human-confirmed reason.
* Good, because `needs-decision` becomes completable.
* Bad, because the contract grows:
  * a payload field (`spike_unavailable`);
  * a reason and its problem codes;
  * a next action, an ingress and an artifact kind;
  * the `invocation` field `audit_retrieval`;
  * the observer fields.
  Each of these changes regenerates the fixtures (the contract digest in them is a placeholder, filled in when they are loaded), the goldens, the recovery reference, the vendor copies and the public tools (L5b §1).
* Bad, because the live receipts are replaced by a v3 format carrying confirmation receipts and `audit_retrieval`, and fresh Claude and Pi receipts must be captured.
* Bad, because humans see more prompts on hosts whose auditor cannot retrieve the web: Claude for now, and Pi without a web extension. Each derivation is shown once, which bounds the prompts; a Pi batch is up to two dialogs per item, a select and a note.

### Confirmation

* **Classification.** A unit test per class; a refusal test per problem code.
* **Batching.** One presentation spends one interaction, and the same key may be presented again by an explicit report until the limit; a receipt without a current presentation refused; one decision per item, no "approve all"; decided items never re-asked; and **Later** items shown again only by an explicit report.
* **Independence.** A run of human decisions only reports `human_owned` and still requires an audit; a human-approved claim with research keeps its producers.
* **Surfacing.**
  * A test that recording a `retrievable` or `observed` record puts the confirmation requirement in `GetRun` obligations in the same response, for each route.
  * An author-view golden and an observer fixture for each.
* **Ingress.** Replay, conflict, stale-record and reject tests mirror the governance receipt suite.
* **Capability.**
  * The Pi matrix asserts `audit_retrieval` per reviewed version.
  * A divergence test at audit launch.
  * The Claude `audit_retrieval` omits `web`, and a test pins that `WebFetch` is never credited.
* **Hosts.** Pi dialog unit tests (TUI, RPC, print); Claude elicitation tests (with and without form support); Claude Stop and the Pi gate tests that an open claim, an already presented key and exhausted interactions block with the real reason.
* **Receipts.** `empirica-live-receipt/v3` carries `audit_retrieval` and any confirmation receipts, and the verifier rejects receipts without them. Capture and inspect bind the artifact store to the repository the run recorded (`projects/<project>/project.json`), comparing canonical Git common-directory identity (a worktree is legitimate); a mismatched, absent or unreadable record is refused (A1-6).
* **What the author reads.** Both renderers (Python and Pi) show a "Waiting on a human" section from `confirmation_batch` (each record's source and claim, each decision claim, and in plain words whether a human is asked when you report, and whether this host can show a dialog), each citation's verification (checked by the service, for the audit to retrieve, waiting for a human, confirmed or rejected by a human) with the human's note, and each decision's outcome and answer. All human text is fenced as untrusted; no receipt id or digest appears (A1-4).
* **The response after a presentation is fresh** (m2). Once a presentation was admitted, the mediator re-evaluates whatever the human did (Later, cancel, timeout, a failing dialog) and returns the refreshed result.
* **Spelling is refused, not interpreted** (m1). A `source_ref` that is an absolute or `~` path with `.` or `..` segments, a leading `//` or `//` inside, or a trailing slash is refused with `source_ref_form`; the check is lexical and reads no filesystem. The runtime-policy path check applies the same rule. Whether a symlink points elsewhere is the audit's to see.

### Decisions taken (maintainer)

* **Claude web.** No transcript observation for now, so Claude routes URLs to a human.
* **Timing.** Ask at the end of the pass, in one batch with a decision per item.
* **Capable auditors.** They fetch and verify existence and content for every record they can retrieve, whether or not a human confirmed it.
* **Scope.** The `needs-decision` answer ships in the same change.

### Scenarios

The outcomes below are tested one row at a time in `tests/v2/test_evidence_scenarios.py`, the Claude adapter tests and the Pi gate tests. H is a host with a human to ask; U is one without.

| Host | Situation | Returned reason | Human is asked | After |
|---|---|---|---|---|
| H | Verbatim record only | `audit.required` | No | No batch |
| H | New research after an earlier look (route `human`, nothing shown for this derivation) | Same reason; next `convergence.report` | At the next report | Reading `next:` alone reaches the dialog |
| H | A URL record only | `evidence.confirmation_required` | Once, at the end of the pass | Confirm releases the record |
| H | A URL record while another claim is open | The open claim's reason | Yes, at the end of the pass | The open claim still leads |
| H | Later, cancel or timeout | Same reason with `asking: again`, `prompt_error: already_presented`; the text directs an explicit `report_convergence` (G15) while the limit allows one, else the plain waiting text | Not by itself; an explicit report asks again, within the interaction limit | Stop blocks; `report_convergence` asks again, otherwise `residual.accept` |
| H | Reject with a note on the last supporting record | `claim.research_missing` | Not again | The note shows on the obligation row |
| H | Interactions exhausted | Same reason, `prompt_error: interaction_limit` | No | Stop blocks with the real reason |
| H | Only a `needs-decision` claim | `evidence.confirmation_required` with `claim_ids` | Once | Approve converges after an audit as `human_owned` |
| H | An audit fails after approval | `audit.failed` | No | The answer stands; a changed wording asks again |
| H | More than 64 items | The same reason | The first 64 | The rest are shown after the next change |
| H | Claude, interactive, before the first report | Same reason, route `unobserved`, `prompt_error: unobserved`; next `convergence.report` then `residual.accept` | At the first `report_convergence`, once the host has looked | The report records the surface (`human`), then presents |
| U | Claude, interactive, the client declared no form | Route `unobserved` until the first report, then `unavailable` | No | After the first report: only `residual.accept` remains |
| U | Delegated, non-interactive, or no form or dialog | Same reason, route `unavailable`, `prompt_error: unavailable` | No | Only `residual.accept` remains |
| U | Codex | Same batch, `prompt_error: unavailable` | No | Same |
| H | A record joins between the host's read and its presentation | Same | The first read's items are not recorded; the host re-reads once and shows all of them | A second change shows nothing and spends nothing |
| H | A missing or stale spike beside a URL record on one claim | `claim.spike_missing` or `claim.spike_stale` | Yes, at the end of the pass | The spike leads; Stop and the gate block with it, not a pause |
| H | The host cannot record its surface | The same reason, `prompt_error: surface_unrecorded` | No, and the response says nobody was asked | The mediator shows nothing for that call; `convergence.report` is not offered, the first `next:` is `residual.accept`; the Claude Stop blocks with the directive or the real reason (it never pauses); the Pi gate blocks with the registry notice and the stop next action; the next recorded observation clears it |
| H | The host cannot show the batch (its presentation ingress faults, throws or is refused) | The same reason, `prompt_error: presentation_failed` | No, and the response says nobody was asked | Nothing is spent, approved or invented. A literal agent ends in two calls (report, then stop); the Pi gate returns the registry notice with the stop next action and never settles as an evidence wait |
| H | Claude, interactive, before the first report (route `unobserved`) or after a look (route `human`), and the human interactions are already spent (configuration used them) | Same reason, `prompt_error: interaction_limit` | No | `convergence.report` is not offered and the waiting line says the limit is reached; Stop blocks with the real reason |
| U | Delegated, non-interactive or no dialog (route `unavailable`), and the interactions are spent | Same reason, `prompt_error: unavailable` | No | `unavailable` is not replaced by the limit (nobody could be asked anyway); only `residual.accept` remains |
| H | The presentation failed and the evaluation that reports it raises or answers a Fault | The response in hand with `prompt_error: presentation_failed` applied by the shared projection | No | `convergence.report` is not offered; the first `next:` is `residual.accept`; repeated default reports give the same answer |
| H | A long quote | Same | The whole quote is shown | Nothing is truncated |
| H | Later, then a fresh `get_run` | Same reason, `prompt_error: already_presented` | Not by itself | The author's view says it was shown and an explicit report asks again |
| H | A path spelled with `..`, `//` or a trailing slash | `research.citation_unverified` (`source_ref_form`) | No | Nothing is recorded |

## Amendment (2026-10-08): a deliberative turn end pauses for evidence; an interactive auto Stop still never settles as an evidence wait

[ADR-0068](0068-record-host-boundaries-as-data-and-end-every-turn-by-control-mode.md) fixes the turn end by control mode on every host. The rule for an interactive auto run stays as the bullet "An interactive auto Stop never settles as an evidence wait" above states it. What changed is the deliberative turn end: it pauses, on its first turn end as on a later one, with `paused_for_evidence` instead of being directed to report.

## Amendment (2026-10-08): the auditor's observed WebFetch was considered and not built

[ADR-0069](0069-admit-hosts-and-audit-runtimes-by-range-and-observed-capability.md) records that confirming a `web` record by the auditor's own observed `WebFetch` (a new `audit-observed` route) was considered and stopped at its premise check: no captured child transcript shows a `WebFetch` result, and a cross-host redirect is returned as a successful text result for a URL that was not retrieved. The rule above is unchanged: a web record is confirmed by the audit only when the start-time `audit_retrieval` contains `web`.
