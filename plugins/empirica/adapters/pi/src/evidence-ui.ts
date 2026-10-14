// The end-of-pass human confirmation on Pi (ADR-0067). The batch, its receipt and every rule come from
// the shared Python flow (adapters/confirmation.py) through the private bridge; this file only *shows*
// the items with Pi's standard dialogs and reports the answers. `ctx.ui.select` and `ctx.ui.input` are
// RPC-forwardable, unlike `ctx.ui.custom`, so the same dialog works in the TUI and in RPC mode. Without a
// UI (print, JSON) nothing is shown: a presentable batch then blocks with the presentation_failed notice.
//
// Fail closed: a cancelled or timed-out dialog, a missing UI, or any error submits nothing beyond the
// presentation already recorded, and grants nothing. "Later" is sent as `choice: later`; the core records it as nothing decided.

import type { ExtensionContext } from "./pi-types.ts";
import type { EvidenceAnswer, EvidenceItem, PrivateIngress } from "./private-transport.ts";
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
 * title and under the field; both are built in `adapters/confirmation.py`, the one owner of their lengths), then the full
 * detail. The end of a deliberative run has its own heading: it is not evidence. */
export function itemTitle(item: EvidenceItem, number: number, count: number): string {
  const head = item.subject === "claim" && item.terminal
    ? "Empirica · end this run without converging?"
    : `Empirica · evidence that needs you (${number} of ${count})`;
  if (item.subject === "claim") return `${head}\n${item.title}\n${item.description}\n${item.text}`;
  const lines = [head, item.title, item.description, `${item.source_kind} source: ${item.source_ref}`, `Quote: "${item.quote}"`,
    ...item.claims.map((claim) => `For claim ${claim.id}: ${claim.text}`)];
  if (item.reason) lines.push(`Why no deterministic check: ${item.reason}`);
  return lines.join("\n");
}

const LABELS: Readonly<Record<EvidenceItem["subject"], readonly string[]>> = {
  research: ["Confirm", "Reject", "Later"],
  claim: ["Approve", "Reject", "Later"],
};

/** Ask about every item in turn; `null` (nothing is submitted) when the human cancels any dialog. */
export async function askItems(items: readonly EvidenceItem[], ctx: ExtensionContext,
                               deadline: number): Promise<EvidenceAnswer[] | null> {
  const ui = ctx.ui;
  if (!canPresent(ctx) || !ui.select || !ui.input) return null;  // canPresent is the rule; the tests narrow the types
  const answers: EvidenceAnswer[] = [];
  for (const [index, item] of items.entries()) {
    if (Date.now() >= deadline) return null;
    const picked = await ui.select(itemTitle(item, index + 1, items.length), [...LABELS[item.subject]],
      { timeout: Math.max(1, deadline - Date.now()) });
    if (picked === undefined || !LABELS[item.subject].includes(picked)) return null;
    const choice = picked.toLowerCase();
    let note: string | null = null;
    if (choice !== "later") {
      const typed = await ui.input(item.note_title, undefined, { timeout: Math.max(1, deadline - Date.now()) });
      if (typed === undefined) return null;
      note = typed.trim() === "" ? null : typed;
    }
    answers.push({ subject: item.subject, id: item.id, choice, note });
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
 * so the gate never settles on a wait nobody was asked about.
 */
export async function confirmEvidence(response: Response, runId: string, ctx: ExtensionContext,
                                      trusted: PrivateIngress, reevaluate: (hostFailure?: HostFailure) => Promise<Response>,
                                      deadline: number): Promise<{ response: Response; failure?: HostFailure }> {
  const batch = askableBatch(response.result);
  if (batch === null) return { response };
  if (!canPresent(ctx)) return { response, failure: "presentation_failed" };
  let shown = false;
  try {
    // The end of a deliberative run is asked by the evaluation that reaches it: the core's batch travels with the call.
    const presented = await trusted({ operation: "evidence_presentation", run_id: runId,
      payload: isTerminalBatch(batch) ? { batch } : {} }) as
      { type: string; receipt_id?: string; items?: EvidenceItem[] };
    if (presented.type === "evidence_presentation" && presented.receipt_id && presented.items) {
      shown = true;
      const answers = await askItems(presented.items, ctx, deadline);
      // Cancelled, or every item left on Later: nothing to submit, nothing granted.
      if (answers !== null && !answers.every((answer) => answer.choice === "later"))
        await trusted({ operation: "evidence_confirmation", run_id: runId,
          payload: { receipt_id: presented.receipt_id, items: presented.items, answers } });
    }
  } catch { /* nothing is granted by a failure; the batch was shown only if `shown` is set */ }
  const failure: HostFailure | undefined = shown ? undefined : "presentation_failed";
  try { return { response: await reevaluate(failure), failure }; } catch { return { response, failure }; }
}
