// The end-of-pass human confirmation on Pi (ADR-0067). The batch, its receipt and every rule come from
// the shared Python flow (adapters/confirmation.py) through the private bridge; this file only *shows*
// the items with Pi's standard dialogs and reports the answers. `ctx.ui.select` and `ctx.ui.input` are
// RPC-forwardable, unlike `ctx.ui.custom`, so the same dialog works in the TUI and in RPC mode. Without a
// UI (print, JSON) nothing is shown: a presentable batch then blocks with the presentation_failed notice.
//
// Fail closed per item (PR 18, ADR-0072 amendment): a cancel, a wrong label or a timeout at item k keeps every
// answer completed before k and sends item k and the rest as `later`, which grants nothing — only a batch with no
// decided item submits nothing at all. Esc at an item's optional note never cancels and is never consent (A3): the
// same item's select is shown again under the same deadline, so nothing is lost. Without a UI (print, JSON) nothing
// is shown: a presentable batch then blocks with the presentation_failed notice.
// "Later" is sent as `choice: later`; the core records it as nothing decided.

import type { ExtensionContext } from "./pi-types.ts";
import type { EvidenceAnswer, EvidenceDocument, EvidenceItem, PrivateIngress } from "./private-transport.ts";
import { hardWrapLines } from "./dialog-view.ts";
import { isTerminalBatch, type ConfirmationBatch, type HostFailure, type Response } from "./contract.ts";
import { hostFailureNotice } from "./author-view.ts";
import { PUBLIC_TOOLS } from "./public-tools.ts";

export const EVIDENCE_REASON = "evidence.confirmation_required";
export const EVIDENCE_WAIT_NOTICE = PUBLIC_TOOLS.settlement_notices.evidence_wait;

/** The confirmation batch of an active run, whatever its `prompt_error`. */
function activeBatch(result: Response["result"]): ConfirmationBatch | null {
  const run = "run" in result ? result.run : undefined;
  return run?.status === "active" ? run.confirmation_batch ?? null : null;
}

/** Whether this host can show a human the dialogs now: a UI with `select` and `input` (print and JSON mode have none).
 * The one definition: the run records `human_surface` with it at start and at every report. */
export function canPresent(ctx: ExtensionContext): boolean {
  return Boolean(ctx.hasUI && ctx.ui?.select && ctx.ui?.input);
}

/**
 * Where the session can be found again, exactly as Pi's session manager reports it: its id (the label), and its file when it
 * has one (otherwise the id is the locator). Nothing is invented: a manager that reports no id, throws or is absent gives
 * none, and the run's session then reads as unknown.
 */
export function sessionLocator(ctx: ExtensionContext): { session?: { label: string; locator: string } } {
  const read = <T>(get: (() => T) | undefined): T | undefined => {
    try { return get?.call(ctx.sessionManager); } catch { return undefined; }
  };
  const id = read(ctx.sessionManager?.getSessionId);
  if (typeof id !== "string" || id === "") return {};
  const file = read(ctx.sessionManager?.getSessionFile);
  return { session: { label: id, locator: typeof file === "string" && file !== "" ? file : id } };
}

/** The batch a human can be shown now: the core projects it whatever else blocks the run, and says in
 * `prompt_error` why it cannot be shown. The host presents exactly when there is one and no error. */
export function presentableBatch(result: Response["result"]): ConfirmationBatch | null {
  const batch = activeBatch(result);
  return batch && batch.prompt_error === null ? batch : null;
}

/** The batch an explicit report may show (`confirmEvidence`): presentable, or only already shown, which Later, cancel and
 * timeout leave open. The core's interaction ceiling bounds it (`interaction_limit` is not here). Python: `REPRESENTABLE`. */
export function askableBatch(result: Response["result"]): ConfirmationBatch | null {
  const batch = activeBatch(result);
  return batch && (batch.prompt_error === null || batch.prompt_error === "already_presented") ? batch : null;
}

/** The presentation question: can THIS host show the sole evidence blocker to a human now? True only when the confirmation is
 * the sole blocker (the core returns it only when nothing else blocks), its route is `human`, and the batch is presentable
 * (`prompt_error` null: not shown for this derivation, interactions left). Otherwise the block stands with its real reason.
 * It is not the turn-end question "may a report still ask" (`evidenceAskable` in turn-end.ts, which also accepts an
 * `unobserved` surface and needs no route): that one decides what the adapter says at the end of a turn. */
export function evidenceConfirmationWait(result: Response["result"]): boolean {
  return soleEvidenceBlock(result)
    && (result.reasons[0].parameters as { route?: string } | undefined)?.route === "human"
    && presentableBatch(result) !== null;
}

/** A Block whose only reason is the evidence confirmation (the core returns it only when nothing else blocks). */
function soleEvidenceBlock(result: Response["result"]): result is Extract<Response["result"], { type: "Block" }> {
  return result.type === "Block" && result.reasons.length === 1 && result.reasons[0].code === EVIDENCE_REASON;
}

/**
 * The notice for a confirmation that is the sole blocker when the host could not ask: nobody was asked, so the gate
 * neither pauses for a wait nor promises another question. The core's projection of the failure (`prompt_error`) says
 * it; `reported`, what this same call told the core, says it too when the core still shows a batch it believes can be
 * presented (a gate never settles on a wait it did not ask about, whatever the core answered). A refusal the core
 * already knows (`already_presented`, `interaction_limit`, `unavailable`) is not this notice. Otherwise `null`.
 */
export function evidenceHostFailure(result: Response["result"], reported?: HostFailure): string | null {
  if (!soleEvidenceBlock(result)) return null;
  const projected = hostFailureNotice(("run" in result ? result.run?.confirmation_batch : null)?.prompt_error);
  return projected ?? (reported !== undefined && evidenceConfirmationWait(result) ? hostFailureNotice(reported) : null);
}

/** What the human reads above the choices; the labels are the whole vocabulary of the dialog. The first line is the
 * heading, the second the item's own short title and the third its description (the same texts Claude shows as the field
 * title and under the field; both are built in `adapters/confirmation.py`, the one owner of their lengths), then the item's
 * own lines. The end of a deliberative run has its own heading: it is not evidence.
 *
 * PR 12 (ADR-0072 "What the forms keep"): the dialogs mirror Claude's short forms line for line.
 * A research item keeps its source line and its quote — the quote is the fact judged — and every
 * claim context ("For claim …") and every "why no deterministic check" reason moved to the
 * document; the pointer line under them names exactly that, in the same words Claude's form uses.
 * A needs-decision claim item keeps its own text in FULL: the claim text is what the human
 * decides — the research records' per-claim context moved, decided claims did not; its dialog has
 * no pointer, because nothing of it moved. The terminal item keeps the full decision wording and
 * names the residual count read out of the written document itself. Every dialog ends with the
 * document's Details (PR 16, A11/A12): a live observer's link or the honest fallback with the
 * path broken at every `/` (no Empirica code shortens or re-derives a path — every piece is a
 * real slice, and the joined lines rebuild the exact record), and the LAST title line is the
 * short digest `Document sha256:<12>`, so Pi's countdown — which `ui.select` attaches to the
 * title's last line — never lands on a path, a quote or a URL (A12).
 *
 * PR 18 (A4): from item 2 on, when an earlier item's answer was completed, one promise line sits
 * between the item's own lines and the Details — `KEEP_COMPLETED` — so the human knows what Esc
 * does before pressing it. The earlier answers come with the asking dialog (`history`, which
 * `askItems` passes); a pure reader passes none and so makes no promise. The promise line obeys
 * the same 76-column rule (A12) as every composed line; the item's own full text stays unwrapped. */
export function itemTitle(item: EvidenceItem, number: number, count: number, document: EvidenceDocument,
                          history: readonly EvidenceAnswer[] = []): string {
  const head = item.subject === "claim" && item.terminal
    ? "Empirica · end this run without converging?"
    : `Empirica · evidence that needs you (${number} of ${count})`;
  const lines = [head, item.title, item.description];
  if (item.subject === "claim") {
    lines.push(item.text);
    if (item.terminal) lines.push(...residualLines(document));
    if (number > 1 && keepsAnswer(history)) lines.push(KEEP_COMPLETED);   // PR 18 (A4): the promise, before the Details
    return [...lines, ...documentLines(document)].join("\n");
  }
  lines.push(`${item.source_kind} source: ${item.source_ref}`, `Quote: "${item.quote}"`);
  // One pointer line, in Claude's exact wording, naming what moved to the document.
  lines.push(POINTER);
  if (number > 1 && keepsAnswer(history)) lines.push(KEEP_COMPLETED);   // PR 18 (A4): the promise, before the Details
  return [...lines, ...documentLines(document)].join("\n");
}

/** PR 18 (A4): the promise needs an earlier answer that decided something; an earlier Later keeps nothing. */
const keepsAnswer = (history: readonly EvidenceAnswer[]): boolean => history.some((answer) => answer.choice !== "later");

/** The pointer of a research item's dialog (mirrors `adapters/confirmation.py`'s form pointer, `_item_text`). */
const POINTER = "Claim texts and their no-deterministic-check reasons: in the document.";
/** PR 18 (A4): what the select promises once an earlier item's answer is complete — Esc or a timeout
 * keeps it, this and the rest stay for later. Fits the 76-column rule (A12); the FIRST item's select
 * carries no earlier answer and so makes no promise on this host. */
export const KEEP_COMPLETED = "Esc or timeout keeps completed answers; this and the rest stay for later.";
/** PR 18 (A4): the Pi note prompt's own instructions, composed in this file alone — the shared
 * `note_title` (`adapters/confirmation.py`'s `NOTE_TITLE`) is untouched, because on Claude the one
 * form for the whole batch is cancelled whole by Esc; only Pi's one-item-at-a-time dialog can
 * promise that Esc returns to the item's choice. "finishes" (never "saves"): Enter records the
 * typed note, or the same choice without one. The instructions stay whole; a longer shared title
 * is shortened first, the same `…` convention the Python builder itself uses. */
const NOTE_INSTRUCTIONS = " · Enter finishes this item · Esc goes back";
/** The note prompt's title: the shared `note_title` plus Pi's own instructions, never over 76 columns. */
export const notePrompt = (noteTitle: string): string => {
  const room = TITLE_WIDTH - NOTE_INSTRUCTIONS.length;
  const fit = noteTitle.length <= room ? noteTitle
    : room <= 1 ? "" : noteTitle.slice(0, room - 1).trimEnd() + "…";
  return fit + NOTE_INSTRUCTIONS;
};
/** The widest line the dialog composes itself (A12): the Details fallback lines are shorter than
 * this, and a five-digit port's URL line is about 74 columns, so every composed line fits
 * without any renderer's help — nothing in the dialog waits on a wrap it never gets. */
const TITLE_WIDTH = 76;
/** This host's observer-UI command (README.md "Usage"), the one its fallback Details names (A11)
 * — Claude's form names its own, and the cross-host caption pin (``tests/v2/test_evidence_hosts.py``)
 * holds the words. */
const UI_COMMAND = "/empirica-ui";
/** PR 16 (A11): the bound document's Details. A live observer's deep link —
 * ``http://127.0.0.1:<port>/app/?run=<compact id>&decision=<12 hex>`` — plus the short digest;
 * or, without one, the honest fallback: this host's own command and the run's compact id, the
 * exact path broken at every ``/`` (explicit newlines — the ``File: `` label opens the first
 * piece), and the short digest **last** (A12: ``ui.select`` owns the countdown and attaches it
 * to the title's last line, so it lands on ``Document sha256:<12>``, a short line, never on a
 * path, a quote or a URL). O5, decided: with a verified link the path is dropped entirely. */
const documentLines = (document: EvidenceDocument): readonly string[] => {
  const short = `Document ${document.digest.slice(0, 19)}`;
  if (document.url !== null) return [`Details: ${document.url}`, short];
  // Follow-up review 16-1: one short arrow line (58 columns with the 12-hex run), never the
  // old two-line fallback that broke after "Human" at the wrap width; `hardWrapLines` labels
  // the first piece `Details: ` (the one `/` is the command's own, so the line never splits).
  const details = UI_COMMAND
    + (document.run12 === null ? "" : ` → run ${document.run12}`) + " → Human decisions";
  return [
    ...hardWrapLines(details, TITLE_WIDTH, "Details: "),
    ...hardWrapLines(document.path, TITLE_WIDTH, "File: "),
    short,
  ];
};
/** The terminal dialog's count line, the same sentences `adapters/confirmation.py`
 * `_residual_lines` renders — shown only when the presentation read a count out of the document. */
const residualLines = (document: EvidenceDocument): readonly string[] =>
  document.residuals === undefined ? [] : document.residuals === 0
    ? ["Nothing would stay unresolved; the record is in the document."]
    : [`${document.residuals} unresolved ${document.residuals === 1 ? "obligation" : "obligations"}`
        + " would remain — they are listed in the document."];

const LABELS: Readonly<Record<EvidenceItem["subject"], readonly string[]>> = {
  research: ["Confirm", "Reject", "Later"],
  claim: ["Approve", "Reject", "Later"],
};

/** Ask about every item in turn; every dialog names the written document it is bound to (PR 12), so
 * the human can open what is judged. `null` is only the missing-UI case (`canPresent`); every human
 * walk returns the answers to submit.
 *
 * PR 18 (per-item fail-closed, ADR-0072 amendment): a completed answer is kept, whatever happens next.
 * A cancel, a wrong label or a timeout at item k's select stops the asking there — items 1..k-1 keep
 * their answers, item k and the rest are answered `later`, and the batch is submitted once by
 * `confirmEvidence` unless nothing was decided. Esc at an item's optional note is never consent and
 * never a cancel (A3): the same item's select is shown again under the same deadline, and only a
 * note step that ends with Enter completes the item; a timeout at the note — the deadline passed
 * while it was open — stops there like a cancel at the select would, the item staying `later`
 * rather than its chosen answer without its note (O1, decided). An empty note submitted with Enter
 * stays the choice with no note, as before. The deadline is host-local only (A1): there is no
 * receipt TTL, and `confirmEvidence` submits what completed while it could still be asked. */
export async function askItems(items: readonly EvidenceItem[], ctx: ExtensionContext,
                               deadline: number, document: EvidenceDocument): Promise<EvidenceAnswer[] | null> {
  const ui = ctx.ui;
  if (!canPresent(ctx) || !ui.select || !ui.input) return null;  // canPresent is the rule; the tests narrow the types
  const answers: EvidenceAnswer[] = [];
  const later = (item: EvidenceItem): EvidenceAnswer => ({ subject: item.subject, id: item.id, choice: "later", note: null });
  for (const [index, item] of items.entries()) {
    if (Date.now() >= deadline) {
      answers.push(...items.slice(index).map(later));   // this item and the rest stay for later
      break;
    }
    const labels = LABELS[item.subject];
    while (true) {
      const picked = await ui.select(itemTitle(item, index + 1, items.length, document, answers),
        [...labels], { timeout: Math.max(1, deadline - Date.now()) });
      if (picked === undefined || !labels.includes(picked)) {
        // Esc, a timeout or a label that was not offered at the select: fail closed from this item on.
        answers.push(...items.slice(index).map(later));
        return answers;
      }
      const choice = picked.toLowerCase();
      if (choice === "later") { answers.push(later(item)); break; }
      const typed = await ui.input(notePrompt(item.note_title), undefined,
        { timeout: Math.max(1, deadline - Date.now()) });
      if (typed === undefined && Date.now() >= deadline) {
        // The deadline passed while the note was open: the item stays `later` (its answer minus its note
        // is never recorded), and the asking stops here — the completed answers before it are kept.
        answers.push(...items.slice(index).map(later));
        return answers;
      }
      if (typed === undefined) continue;   // Esc at the note (A3): the same item's select is shown again
      answers.push({ subject: item.subject, id: item.id, choice, note: typed.trim() === "" ? null : typed });
      break;
    }
  }
  return answers;
}

/**
 * Tell the core whether this host can show a dialog to a human now; the latest observation counts. Resolves true when
 * the core holds the latest observation. A surface that could not be recorded as present is retried as absent; when
 * even that is not recorded the host fails closed: the caller presents nothing and the gate does not pause, since
 * the core was never told a question would be asked. A typed fault is a failure like a thrown error.
 */
export async function observeSurface(runId: string, ctx: ExtensionContext, trusted: PrivateIngress): Promise<boolean> {
  const present = canPresent(ctx);
  const record = async (surface: "human" | "unavailable"): Promise<boolean> => {
    try {
      const reply = await trusted({ operation: "human_surface", run_id: runId, payload: { surface } }) as
        { type?: string; result?: { type?: string } } | null;
      return (reply?.result ?? reply)?.type !== "Fault";
    } catch { return false; }
  };
  return (await record(present ? "human" : "unavailable")) || (present && await record("unavailable"));
}

/** The document pair of a presentation answer, checked before anything may be shown: the same
 * shape `private-transport.ts` holds the sole transport validator for (defense in depth — a
 * hostile or buggy ingress cannot make a dialog out of an unproven document, so "nothing shown"`)
 * and never its bytes. PR 16 (A10–A12): the pair also carries the presentation-only link fields
 * — `url` (the observer's deep link, `null` without a live observer of this home) and `run12`
 * (the run's compact id) — which this validator dropped with every extra field; they now cross,
 * as data for the dialog's Details lines, and never reach any receipt. */
function presentedDocument(value: unknown): EvidenceDocument | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const { digest, path, url, run12, ...rest } = value as Record<string, unknown>;
  if (typeof digest !== "string" || !/^sha256:[0-9a-f]{64}$/.test(digest)) return null;
  if (typeof path !== "string" || !path.startsWith("/")) return null;
  if (url !== null && (typeof url !== "string"
    || !/^http:\/\/127\.0\.0\.1:[0-9]{1,5}(\/.*)?$/.test(url))) return null;
  if (run12 !== null && (typeof run12 !== "string" || !/^[0-9a-f]{12}$/.test(run12))) return null;
  const residuals = (value as Record<string, unknown>).residuals;
  if (residuals !== undefined && (typeof residuals !== "number" || !Number.isSafeInteger(residuals) || residuals < 0))
    return null;
  if ("text" in rest) return null;   // bytes never cross the transport at all
  return { digest, path, url, run12, ...(residuals === undefined ? {} : { residuals }) };
}

/**
 * Present the core's batch once (whatever else blocks the run), submit the human's per-item decisions as
 * one receipt and return the response to act on, with the host failure to report (`failure`, `undefined` when the
 * batch was shown or there was none). Once the batch was shown, the run is evaluated again whatever the human did:
 * Later, cancel and timeout grant nothing but spend this derivation, and the fresh response says so
 * (`prompt_error: already_presented`) instead of promising another dialog; the end of a deliberative run is spent per request
 * instead (its response says `case: declined`, and the next explicit stop asks again). A batch that could not be presented (the
 * ingress faulted, threw, or refused) is evaluated again with `presentation_failed`, so the response says nobody was
 * asked; the core's own refusal (`already_presented`, `interaction_limit`) outranks it. A presentable batch with no
 * dialog to show it in is the same failure to ask: nothing is spent and nothing is evaluated, but `failure` says so,
 * so the gate never settles on a wait nobody was asked about. Review-2 m5 (round 12, decision 7c): a submit the core
 * refused — a Block, a Fault, or one that never answered (the bridge threw between present and confirm) — admitted
 * nobody's answer, so it is reported and re-evaluated as the same `presentation_failed`: the run reads `host_failed`
 * (recovered only by showing the batch again), never the human's decline.
 *
 * PR 12 (ADR-0072): a presentation answer also names the document its presentation wrote, and it must
 * be a proven pair before a dialog may show it — an answer without one is "nothing shown" and the
 * same `presentation_failed` as any refusal. Every item dialog ends with the document's Details
 * and its short digest (PR 16: a live observer's link, or this host's command with the exact
 * path — the path is dropped only when a verified link is shown), and the receipt repeats the
 * presentation's digest (`document_digest`), so
 * the core records that what was submitted was decided against the document the human was shown;
 * the pair itself, its path included, never leaves the private lane.
 */
export async function confirmEvidence(response: Response, runId: string, ctx: ExtensionContext,
                                      trusted: PrivateIngress, reevaluate: (hostFailure?: HostFailure) => Promise<Response>,
                                      deadline: number): Promise<{ response: Response; failure?: HostFailure }> {
  const batch = askableBatch(response.result);
  if (batch === null) return { response };
  if (!canPresent(ctx)) return { response, failure: "presentation_failed" };
  let shown = false;
  let refused = false;   // review-2 m5 (round 12, decision 7c): the submit admitted nobody's answer
  try {
    // The end of a deliberative run is asked by the evaluation that reaches it: the core's batch travels with the call.
    const presented = await trusted({ operation: "evidence_presentation", run_id: runId,
      payload: isTerminalBatch(batch) ? { batch } : {} }) as
      { type: string; receipt_id?: string; items?: EvidenceItem[]; document?: unknown };
    const document = presented.type === "evidence_presentation" ? presentedDocument(presented.document) : null;
    if (presented.type === "evidence_presentation" && document !== null
      && presented.receipt_id && presented.items) {
      shown = true;
      const answers = await askItems(presented.items, ctx, deadline, document);
      // PR 18: a batch with no decided item — every answer `later`, including the items a cancel or a
      // timeout left from that step on — submits nothing, as before; a partial batch submits the
      // answers completed before the stop, once, and the core's own current-state checks admit them.
      if (answers !== null && !answers.every((answer) => answer.choice === "later")) {
        const confirmed = await trusted({ operation: "evidence_confirmation", run_id: runId,
          payload: { receipt_id: presented.receipt_id, items: presented.items, answers,
            document_digest: document.digest } }) as
          { type?: string; result?: { type?: string } };
        // The submit's outcome decides what was recorded: an admitted receipt (Allow, Inert) or the
        // genuine human nothing (evidence_none, when the answers derived no decision) recorded the
        // human's word; a refused one (a Block — the residuals moved while the dialog was open — a
        // Fault, or anything malformed) admitted nobody's answer, so the run reads host_failed
        // (recovered only by showing the batch again), never the human's decline.
        refused = !(confirmed?.type === "evidence_none"
          || confirmed?.type === "evidence_receipt"
          && (confirmed.result?.type === "Allow" || confirmed.result?.type === "Inert"));
      }
    }
  } catch {
    /* nothing is granted by a failure; the batch was shown only if `shown` is set */
    refused = shown;   // a throw after the showing recorded nobody's answer either
  }
  const failure: HostFailure | undefined = !shown || refused ? "presentation_failed" : undefined;
  try { return { response: await reevaluate(failure), failure }; } catch { return { response, failure }; }
}
