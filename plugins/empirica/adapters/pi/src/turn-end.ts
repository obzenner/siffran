// The Pi turn end (ADR-0068): what the adapter does at `agent_before_settle` for an active run.
//
// The decision is the registry's turn-end table (`turn_end` in `contracts/empirica/v2/host-boundaries.json`), the same on
// every host; this file maps it to Pi's surface and owns the one thing Pi does not give: a loop bound. Pi has no host cap
// on continuations ("guard continuation conditions"), so an auto run is asked to continue only while it makes progress, and
// only so often: the K-th turn end on one basis, or the T-th in a chain, ends the turn with the residual notice (the registry's `turn_end.bounds`).
// Progress means the run's `basis` changed since the adapter's previous continuation. The basis is the core's
// (`core.evaluation.progress_basis`, a field of the run view: the blockers and the run state an author's or a host's action
// can change); the adapter composes nothing, so a read of the run or of a file is never progress. It counts continuations
// only. That memory (`{basis, count, total}`) is in-process adapter state, never run state, and a restart starts a chain over.

import type { Response } from "./contract.ts";
import { budgetExhaustedWait, humanApprovalWait, BUDGET_EXHAUSTED_NOTICE, HUMAN_WAIT_NOTICE } from "./governance-ui.ts";
import { PUBLIC_TOOLS } from "./public-tools.ts";

type Result = Response["result"];

const NOTICES = PUBLIC_TOOLS.settlement_notices;

/** What was observed when the adapter last asked an auto run to continue: the core's `run.basis`. */
export type Basis = string;

/** K: the K-th turn end of a chain on one basis ends it (K - 1 continuations). T: the T-th turn end in a chain ends it. Both are the
 * registry's `turn_end.bounds`, read against it by a test. */
export const CONTINUATION_K = 3;
export const CONTINUATION_T = 16;

/** What the adapter remembers of the chain it is in: the basis of the last continuation and the continuations so far. */
export type Memory = { basis: Basis; count: number; total: number };

type TurnEnd =
  /** Append nothing: the run converged, an audit is pending, the run is not active, or it cannot be read. */
  | { kind: "nothing" }
  /** Append one visible notice and request no continuation (a settlement, or a deliberative pause). */
  | { kind: "notice"; notice: string }
  /** Append the next obligation and request one more model request; `memory` is what to remember. */
  | { kind: "continue"; message: string; memory: Memory }
  /** An auto chain that reached K on one basis or T in total: end with the residual notice (the honest stop) and record `no-progress`. */
  | { kind: "no-progress"; notice: string };

/** The remembered chain as the adapter wrote it, else nothing: a memory from 7.0.x held a bare basis, and anything unreadable
 * (wrong shape, a count that is not a non-negative integer) is read the same way, as a changed basis with zeroed counts. */
function readMemory(value: unknown): Memory | null {
  if (typeof value !== "object" || value === null) return null;
  const { basis, count, total } = value as Record<string, unknown>;
  const counted = (n: unknown): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0;
  return typeof basis === "string" && counted(count) && counted(total) ? { basis, count, total } : null;
}

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
 * report looks for one); a deliberative run pauses with `paused_for_evidence` then. An auto run asks no one. It is Claude's `_evidence_report_directive`, case for case (`fixtures/turn-end-predicates.json`).
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

/** The turn-end table for one evaluation of the run and what the adapter remembers of the chain it is in (`null`: none). */
export function turnEnd(result: Result, remembered: unknown): TurnEnd {
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
  if (mode === "deliberative")
    return { kind: "notice", notice: evidenceAskable(result) ? NOTICES.paused_for_evidence : fill(NOTICES.paused_for_human, { reason }) };
  const basis: Basis = String((result.run as { basis?: unknown }).basis ?? "");
  // The count of continuations on one basis and the total in the chain, as this one would make them. Nothing remembered, or a
  // memory that is not readable, is a changed basis: the count starts at 1.
  const previous = readMemory(remembered);
  const count = previous !== null && previous.basis === basis ? previous.count + 1 : 1;
  const total = (previous?.total ?? 0) + 1;
  // The K-th turn end on one basis, or the T-th in the chain, is the honest end: the residual notice offers the stop. This
  // check stays ahead of anything the turn would hand the agent, the `none` directive included.
  if (count >= CONTINUATION_K || total >= CONTINUATION_T)
    return { kind: "no-progress", notice: fill(NOTICES.unattended_residual, { reason }) };
  // The core says nothing the author does approves the claims as they stand (the root claim is refuted or conflicted): the
  // agent is handed the residual notice as the directive, to end honestly or revise the graph. A settled turn would hand the
  // run to nobody. It goes on once per basis, and the continuation is counted like any other: a turn end on the basis the
  // previous continuation saw ends the turn with the way out. Any other value, or none (a view the schema would refuse), is
  // not read as that or as the author's step.
  if (result.run.turn_end_actor === "none") {
    if (count > 1) return { kind: "no-progress", notice: fill(NOTICES.unattended_continue, { reason }) };
    return { kind: "continue", message: fill(NOTICES.unattended_residual, { reason }), memory: { basis, count, total } };
  }
  // The first continuation hands the agent the core's own reason; later ones, the way out. An auto run asks no one, so no
  // directive to report is ever appended.
  const message = previous === null ? reason : fill(NOTICES.unattended_continue, { reason });
  return { kind: "continue", message, memory: { basis, count, total } };
}
