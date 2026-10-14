// The Pi turn end (ADR-0068): what the adapter does at `agent_before_settle` for an active run.
//
// The decision is the registry's turn-end table (`turn_end` in `contracts/empirica/v2/host-boundaries.json`), the same on
// every host; this file maps it to Pi's surface and owns the one thing Pi does not give: a loop bound. Pi has no host cap
// on continuations ("guard continuation conditions"), so an auto run is asked to continue only while it makes progress.
// Progress means the run's `basis` changed since the adapter's previous continuation. The basis is the core's
// (`core.evaluation.progress_basis`, a field of the run view: the blockers and the run state an author's or a host's action
// can change); the adapter composes nothing and counts nothing, so a read of the run or of a file is never progress. That
// memory is in-process adapter state, never run state, and a restart allows at most one extra continuation.

import type { Response } from "./contract.ts";
import { budgetExhaustedWait, humanApprovalWait, BUDGET_EXHAUSTED_NOTICE, HUMAN_WAIT_NOTICE } from "./governance-ui.ts";
import { PUBLIC_TOOLS } from "./public-tools.ts";

type Result = Response["result"];

const NOTICES = PUBLIC_TOOLS.settlement_notices;

/** What was observed when the adapter last asked an auto run to continue: the core's `run.basis`. */
export type Basis = string;

type TurnEnd =
  /** Append nothing: the run converged, an audit is pending, the run is not active, or it cannot be read. */
  | { kind: "nothing" }
  /** Append one visible notice and request no continuation (a settlement, or a deliberative pause). */
  | { kind: "notice"; notice: string }
  /** Append the next obligation and request one more model request; `basis` is what to remember. */
  | { kind: "continue"; message: string; basis: Basis }
  /** An auto run that made no progress since the previous continuation: end with the way out and record `no-progress`. */
  | { kind: "no-progress"; notice: string };

export const fill = (template: string, values: Record<string, string>): string =>
  Object.entries(values).reduce((text, [key, value]) => text.replace(`{${key}}`, () => value), template);

/** The core's own text for every blocker, as one sentence (Claude's `_reason_text`). */
function reasonText(result: Result): string {
  const reasons = result.type === "Block" ? result.reasons : [];
  const text = reasons.map((reason) => reason.message ?? "").join(" ").split(/\s+/).filter(Boolean).join(" ")
    || "the run is not complete.";
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

function activeBlock(result: Result): result is Extract<Result, { type: "Block" }> & { run: NonNullable<Extract<Result, { type: "Block" }>["run"]> } {
  return result.type === "Block" && !!result.run && result.run.status === "active";
}

/** The turn-end question: may a report still ask a human about the sole evidence blocker? True only when the confirmation is
 * the sole blocker and the batch's `prompt_error` is in the core's `ASKABLE` (`null`: a dialog can be shown; `unobserved`: the
 * report looks for one). It is Claude's `_evidence_report_directive`, case for case (`fixtures/turn-end-predicates.json`).
 * It is not `evidenceConfirmationWait` (evidence-ui), which asks the different question "can the host present the batch now". */
export function evidenceAskable(result: Result): boolean {
  if (!activeBlock(result) || result.reasons.length !== 1 || result.reasons[0].code !== "evidence.confirmation_required")
    return false;
  const batch = result.run.confirmation_batch;
  return !!batch && (batch.prompt_error === null || batch.prompt_error === "unobserved");
}

/** True for the single pending async audit, which settles the turn (its result continues the run): Claude's `_async_audit_wait`,
 * case for case (`fixtures/turn-end-predicates.json`). Both require the child to have an id. */
export function auditPending(result: Result): boolean {
  if (!activeBlock(result) || result.reasons.length !== 1 || result.reasons[0].code !== "audit.pending") return false;
  const children = Array.isArray(result.run.children) ? result.run.children as Array<Record<string, unknown>> : [];
  return children.filter((child) => child.resource_class === "audit" && child.state === "pending"
    && typeof child.child_id === "string" && child.child_id !== "").length === 1;
}

function controlMode(result: Result): "deliberative" | "auto" | null {
  if (!activeBlock(result)) return null;
  const mode = (result.run.governance as { control_mode?: unknown } | null | undefined)?.control_mode;
  return mode === "deliberative" || mode === "auto" ? mode : null;
}

/** The turn-end table for one evaluation of the run and the previous continuation's basis. */
export function turnEnd(result: Result, previous: Basis | null): TurnEnd {
  if (!activeBlock(result)) return { kind: "nothing" };  // converged or terminal, inert or faulted: the gate at tool_call holds
  if (humanApprovalWait(result)) return { kind: "notice", notice: HUMAN_WAIT_NOTICE };
  if (budgetExhaustedWait(result)) {
    const { resource } = result.reasons[0].parameters as { resource: string };
    return { kind: "notice", notice: fill(BUDGET_EXHAUSTED_NOTICE, { resource }) };
  }
  if (auditPending(result)) return { kind: "nothing" };
  const mode = controlMode(result);
  if (mode === null) return { kind: "nothing" };  // a view without a mode is not acted on
  const reason = reasonText(result);
  const askable = evidenceAskable(result);
  if (mode === "deliberative")
    return { kind: "notice", notice: askable ? NOTICES.paused_for_evidence : fill(NOTICES.paused_for_human, { reason }) };
  const basis: Basis = String((result.run as { basis?: unknown }).basis ?? "");
  const wayOut = fill(NOTICES.unattended_continue, { reason });
  // The first continuation hands the agent the core's own reason; later ones, the way out (the evidence directive is the
  // one exception and is kept, ADR-0068).
  const message = askable ? NOTICES.evidence_report : previous === null ? reason : wayOut;
  if (previous !== null && previous === basis)
    return { kind: "no-progress", notice: wayOut };
  return { kind: "continue", message, basis };
}
