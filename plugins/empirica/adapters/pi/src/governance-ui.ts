import { randomUUID } from "node:crypto";
import type { ExtensionContext } from "./pi-types.ts";
import type { PrivateIngress } from "./private-transport.ts";
import { assertPrivateResponse } from "./private-transport.ts";
import type { Block, Response } from "./contract.ts";
import { PROTOCOL } from "./contract.ts";
import { assertResponse } from "./guard.ts";
import { PUBLIC_TOOLS } from "./public-tools.ts";
import { APPROVAL_INGRESS_UNAVAILABLE, DIALOG_INGRESS, routesToDialog } from "./host-profile.ts";
import { EVIDENCE_WAIT_NOTICE, evidenceConfirmationWait } from "./evidence-ui.ts";
import { ENVIRONMENT_CONTROLS, initialState, reduce, renderEnvironmentLines, renderLines } from "./dialog-view.ts";
import type { BoundDocument } from "./dialog-view.ts";
import type { Dialog, DialogDecision, DialogState, DialogTheme } from "./dialog-view.ts";

const RECOVERY = PUBLIC_TOOLS.recovery;
interface Proposal { budgets: Record<string, number>; rationale: string | null }
interface Governance {
  state: string; control_mode: string; proposal_digest: string; plan_revision: number;
  first_approval: boolean;
  proposal: Proposal; prompt_error: string | null;
  /** The ceilings the run has approved (the view's own); read only to show a raise beside them. */
  budgets?: Record<string, number>;
  /** Whether the configuration waits on its human: the core's answer (`core.governance.human_wait`). */
  human_wait: boolean;
  /** Whether the standing proposal asks for more than the run approved: the core's answer (`core.governance.raises`),
   * required by the response schema. */
  raises: boolean;
  context: { author: { provider_id: string; model_id: string } | null; ingress: string;
    interactive: boolean | null; delegation: boolean };
}
/** The private presentation carries the bound document the Python bridge wrote (PR 09) and, when
 * the pair could not be proven or published, the marker naming that failure instead. The
 * document never crosses with its text: the human opens the absolute path. */
interface Presentation {
  dialog: Dialog;
  scope: unknown;
  document?: BoundDocument;
  document_unavailable?: true;
}

function presentationOf(response: Response): Presentation | undefined {
  return (response.result as { presentation?: Presentation }).presentation;
}

/** Validate the bound document exactly as the private response validator does: a full digest
 * and a nonempty absolute path, with core-owned priors the form must never recompute, and the
 * parsed document's core-derived ``question`` — ``approve`` or ``confirm_amendment`` —
 * which alone chooses which dialog is shown (M1, review 2). PR 16 (A10/A11) threads the
 * presentation-only link fields through: `url` (the observer's deep link, `null` without a
 * live observer of this home) and `run12` (the run's compact id the fallback names), both
 * computed by the Python bridge once per presentation; `null` is the honest "no link", never
 * a missing member. Anything else is treated as absent, so a malformed pair refuses exactly
 * like a missing one. */
function boundDocument(presentation: Presentation | undefined): BoundDocument | undefined {
  const document = !presentation || presentation.document_unavailable === true
    ? undefined : presentation.document;
  const digest = document?.digest;
  const path = document?.path;
  if (typeof digest !== "string" || !/^sha256:[0-9a-f]{64}$/.test(digest)) return undefined;
  if (typeof path !== "string" || !path.startsWith("/")) return undefined;
  const url = document?.url;
  if (url !== null && typeof url !== "string") return undefined;
  if (typeof url === "string" && !/^http:\/\/127\.0\.0\.1:[0-9]{1,5}(\/.*)?$/.test(url)) return undefined;
  const run12 = document?.run12;
  if (run12 !== null && typeof run12 !== "string") return undefined;
  if (typeof run12 === "string" && !/^[0-9a-f]{12}$/.test(run12)) return undefined;
  const priors = document?.priors;
  if (priors !== null && priors !== undefined
    && (typeof priors !== "object"
      || Object.values(priors).some(prior => typeof prior !== "number"))) return undefined;
  const question = document?.question;
  if (question !== "approve" && question !== "confirm_amendment") return undefined;
  return { digest, path, priors: priors ?? null, question, url: url ?? null, run12: run12 ?? null };
}

function stripPresentation(response: Response): Response {
  if (response && typeof response === "object" && "presentation" in (response.result as object)) {
    const { presentation: _dropped, ...rest } = response.result as Record<string, unknown>;
    return { ...response, result: rest } as Response;
  }
  return response;
}

/** The ingress the host can show now: the profile's dialog ingress where a UI is present, else the unavailable word. */
export function piGovernanceContext(ctx: ExtensionContext): Record<string, unknown> {
  return { author: ctx.model ? { provider_id: ctx.model.provider, model_id: ctx.model.id,
                                 source: "pi-context" } : null,
           ingress: ctx.hasUI && DIALOG_INGRESS !== undefined ? DIALOG_INGRESS : APPROVAL_INGRESS_UNAVAILABLE };
}

export async function refreshGovernance(runId: string, ctx: ExtensionContext,
                                        trusted: PrivateIngress,
                                        bind = false): Promise<Response> {
  // (m2, review 2) Only the mediator's own refresh binds the HITL document: the raw-level
  // ``bind_document`` flag makes the Python bridge write the decision document an actual
  // present will name. A plain refresh — the one in front of every tool_call, model_select
  // and session event — carries no document and writes none.
  const result = await trusted({ operation: "governance_context", run_id: runId,
    bind_document: bind || undefined, payload: piGovernanceContext(ctx) });
  assertResponse(result, "trusted-governance");
  return result;
}

/**
 * The one contract code for a mediation that ended without a decision, chosen from the run's own governance facts
 * (never from the adapter's guess): the row it names owns the actions. Python: `closed_code`. The run's allowance
 * spent, nobody who can be asked with no automatic way on, or nothing left to show or change (the view's `next_action` is the
 * stop) → the stop alone; a raise nobody can approve (no human, or no interaction left) while a proposal within the approved ceilings is admitted → `raise_unavailable`; this configuration's presentations spent → `configuration_shown`;
 * a human who closed the dialog → `dialog_closed`.
 */
function closedCode(response: Response): string {
  const run = "run" in response.result ? response.result.run : null;
  const g = (run?.governance ?? {}) as { prompt_error?: string | null; human_wait?: boolean; next_action?: string; raises?: boolean;
    interactions_remaining?: { total?: number }; context?: { approval_capability?: string } };
  // the view's own next action is the stop: nobody to ask with no automatic way on, or nothing left to show or change
  if (g.next_action === "residual.accept") return "governance.approval_unavailable";
  // nobody can approve a pending raise, but a proposal within the approved ceilings is still admitted: propose it, or stop
  // (or none can be asked for want of an interaction: the run's total allowance is spent)
  if (g.context?.approval_capability === "unavailable" || (g.prompt_error && g.interactions_remaining?.total === 0))
    return g.raises === true ? "governance.raise_unavailable" : "governance.approval_unavailable";
  if (g.prompt_error)
    return g.interactions_remaining?.total === 0 ? "governance.approval_unavailable" : "governance.configuration_shown";
  return g.human_wait === true ? "governance.dialog_closed" : "governance.approval_unavailable";
}

function unavailable(response: Response, code?: string): Response {
  code ??= closedCode(response);
  if (!(code in RECOVERY)) throw new Error(`Missing canonical governance recovery metadata: ${code}`);
  if (!("run" in response.result)) return response;
  return { ...response, result: { type: "Block", run: response.result.run,
    reasons: [{ code, parameters: {}, ...RECOVERY[code] }] } };
}

/**
 * Host decision timeout in milliseconds. Unset or blank `EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS` means the
 * 900-second default; a set value must be ASCII decimal digits only (after trimming ASCII whitespace) from
 * 1 to 1500 inclusive, leading zeros allowed (significant digits are bounded before conversion so no input
 * length changes the outcome), or this throws instead of silently becoming the default. The Python mirror
 * (`governance_timeout`) accepts the identical syntax and formats the identical message; the shared case
 * table pins both.
 */
export function governanceTimeout(): number {
  const raw = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  const text = raw?.replace(/^[ \t\n\r\f\v]+|[ \t\n\r\f\v]+$/g, "");
  if (raw === undefined || !text) return 900_000;
  const match = /^0*([1-9][0-9]{0,3})$/.exec(text);
  if (match) {
    const seconds = Number(match[1]);
    if (seconds <= 1500) return seconds * 1000;
  }
  throw new Error(`EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS must be a whole number of seconds from 1 to 1500, got ${JSON.stringify(raw)}`);
}

function closedFault(code: "unavailable" | "corrupt_run" = "unavailable"): Response {
  return { protocol: PROTOCOL, request_id: randomUUID(),
    result: { type: "Fault", code, fail_direction: "closed" } };
}

interface DialogComponent {
  render(width: number): string[];
  invalidate(): void;
  handleInput(data: string): void;
}

/** One screen of the shared dialog frame for the current state and the time left. */
type Screen = (state: DialogState, width: number, theme: DialogTheme, timeoutMs: number) => string[];

/** Run one native custom component with shared deadline and abort handling: `controls` are the editable rows the
 * keys move over, `screen` draws the frame. */
async function showDialog(ctx: ExtensionContext, controls: Pick<Dialog, "budgets">, deadline: number,
                          confirmation: boolean, screen: Screen, signal?: AbortSignal): Promise<DialogDecision> {
  if (!ctx.ui.custom) return { type: "dismiss" };
  // Pi's RPC mode reports hasUI=true yet its `custom()` is `async () => undefined` (no component
  // can be shown; 1.0.0 and 1.1.0 alike): "nothing was presented" is a dismissal, never an approval.
  const decision = await ctx.ui.custom<DialogDecision | undefined>((tui, hostTheme, _keybindings, done) => {
    let state: DialogState = initialState(controls, confirmation);
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;
    const abort = () => finish({ type: "dismiss" });
    const finish = (value: DialogDecision) => {
      if (!closed) {
        closed = true;
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        done(value);
      }
    };
    const theme: DialogTheme = {
      accent: text => hostTheme.fg("accent", text),
      muted: text => hostTheme.fg("dim", text),
      warning: text => hostTheme.fg("warning", text),
    };
    timer = setTimeout(() => finish({ type: "dismiss" }), Math.max(1, deadline - Date.now()));
    timer.unref?.();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort(); // an abort that landed before the listener existed
    const component: DialogComponent = {
      render: width => screen(state, width, theme, Math.max(0, deadline - Date.now())),
      invalidate: () => {},
      handleInput: data => {
        const outcome = reduce(state, data, controls, confirmation);
        if ("done" in outcome) finish(outcome.done);
        else { state = outcome.state; tui.requestRender(); }
      },
    };
    return component;
  });
  return decision ?? { type: "dismiss" };
}

export function expectedApprovalKind(governance: Governance): "host_ui" | "auto" {
  if (governance.control_mode === "deliberative") return "host_ui";
  // After the first approval an interactive auto run's human is asked about a raise only: the core's fact, read from the view
  // (`governance.raises`, derived from the proposal and the approved ceilings), never derived here. Fail closed: absent is not a raise.
  if (governance.context.interactive === true)
    return !governance.first_approval || governance.raises === true ? "host_ui" : "auto";
  return "auto";
}

/** The reasons an unapproved configuration blocks with (`governance.approval_required` before the first approval or
 * after a rejection, `governance.revision_required` for a revision): the blocker the host may pause on. */
const CONFIGURATION_REASONS: ReadonlySet<string> = new Set(["governance.approval_required", "governance.revision_required"]);

/** True only for the sole legitimate human-approval blocker of an active run (ADR-0063). Whether the configuration
 * waits on its human is the core's answer in the run view (`governance.human_wait`, the predicate the observer reads
 * too); the host adds only its own ingress (Pi's dialog) and that the configuration's blocker is the run's sole one. The same
 * rule as Python `completion._human_approval_wait`, case for case (`fixtures/turn-end-predicates.json`). */
export function humanApprovalWait(result: Response["result"]): boolean {
  if (result.type !== "Block" || !("run" in result) || !result.run || result.run.status !== "active")
    return false;
  const g = result.run.governance as Governance | null;
  if (g === null) return false;
  const reasons = result.reasons;
  return g.human_wait === true && routesToDialog(g.context.ingress)
    && reasons.length === 1 && CONFIGURATION_REASONS.has(reasons[0].code);
}

/** True only for the sole `budget.exhausted` blocker of an active run (ADR-0063 exhaustion rule) that has no recovery left
 * for the agent to take; mirrors Python `completion._budget_exhausted_wait`. Denying the report again cannot help a run whose
 * recovery the core refused (a delegated auto run's raise) or leaves to the human (a deliberative run's). An auto run the
 * core still offers `budget.raise` (its human approves a raise) is not waiting: the agent proposes it. */
export function budgetExhaustedWait(result: Response["result"]): boolean {
  if (!(result.type === "Block" && "run" in result && !!result.run && result.run.status === "active"
    && result.reasons.length === 1 && result.reasons[0].code === "budget.exhausted")) return false;
  const offered = (result.reasons[0].next_actions as string[] | undefined)?.includes("budget.raise") === true;
  const governance = result.run.governance as { control_mode?: unknown } | null | undefined;
  return !(offered && governance?.control_mode === "auto");
}

export const HUMAN_WAIT_NOTICE = PUBLIC_TOOLS.settlement_notices.human_wait;
/** Contract-owned template; `{resource}` is its only placeholder. */
export const BUDGET_EXHAUSTED_NOTICE = PUBLIC_TOOLS.settlement_notices.budget_exhausted;

/** The nonterminal settlement notice for a Block the host must not repeat, or null when the
 * generic gate applies. */
export function settlementNotice(result: Response["result"]): string | null {
  if (humanApprovalWait(result)) return HUMAN_WAIT_NOTICE;
  if (evidenceConfirmationWait(result)) return EVIDENCE_WAIT_NOTICE;
  if (!budgetExhaustedWait(result)) return null;
  const { resource } = (result as Block).reasons[0].parameters as { resource: string };
  return BUDGET_EXHAUSTED_NOTICE.replace("{resource}", () => resource);
}

export async function govern(runId: string, ctx: ExtensionContext, trusted: PrivateIngress,
                             signal?: AbortSignal, confirmation?: { revision: number; digest: string },
                             deadline = Date.now() + governanceTimeout(), fallback?: Response): Promise<Response> {
  let response: Response;
  try { response = await refreshGovernance(runId, ctx, trusted, true); }
  catch { return fallback && "run" in fallback.result ? stripPresentation(unavailable(fallback)) : closedFault(); }
  try { return stripPresentation(await mediateGovernance(response, runId, ctx, trusted, signal, confirmation, deadline)); }
  catch { return stripPresentation("run" in response.result ? unavailable(response) : response); }
}

async function mediateGovernance(response: Response, runId: string, ctx: ExtensionContext,
                                 trusted: PrivateIngress, signal?: AbortSignal,
                                 confirmation?: { revision: number; digest: string },
                                 deadline = Date.now() + governanceTimeout()): Promise<Response> {
  const result = response.result;
  if (!("run" in result) || !result.run || !["Allow", "Inert"].includes(result.type)) return response;
  const g = result.run.governance as Governance | null;
  if (!g) return response;
  if (confirmation && (g.plan_revision !== confirmation.revision || g.proposal_digest !== confirmation.digest))
    return unavailable(response, "governance.stale_proposal");
  if (g.state === "approved") return response;
  const approvalKind = expectedApprovalKind(g);
  if (g.prompt_error) return unavailable(response);
  const envelope = { run_id: runId, receipt_id: randomUUID(), proposal_digest: g.proposal_digest,
    plan_revision: g.plan_revision, approval_kind: approvalKind };
  // PR 09 (ADR-0072 write-first): a host_ui mediation must hold the bridge-written bound
  // document before anything is presented — no pair, or the bridge's own unavailable marker,
  // means governance.approval_unavailable, nothing spent. States that wait on no human carry
  // no document and never reach here by the state guard above. The auto lane binds nothing.
  const bound = approvalKind === "host_ui" ? boundDocument(presentationOf(response)) : undefined;
  if (approvalKind === "host_ui" && !bound) return unavailable(response);
  const mediationEnvelope = bound ? { ...envelope, document_digest: bound.digest } : envelope;
  const dismiss = async (): Promise<Response> => {
    const stored = await trusted({ operation: "governance_decision", run_id: runId,
      payload: { ...mediationEnvelope, outcome: "dismiss" } });
    assertResponse(stored, "trusted-governance");
    return ["Allow", "Inert"].includes(stored.result.type) ? unavailable(stored) : stored;
  };
  if (approvalKind === "auto") {
    const admitted = await trusted({ operation: "governance_decision", run_id: runId,
      payload: { ...mediationEnvelope, outcome: "approve" } });
    assertResponse(admitted, "trusted-governance");
    return admitted;
  }
  if (!ctx.hasUI || !ctx.ui.custom || !routesToDialog(g.context.ingress)
      || signal?.aborted || Date.now() >= deadline) return unavailable(response);
  const presented = await trusted({ operation: "governance_decision", run_id: runId,
    payload: { ...mediationEnvelope, outcome: "present" } });
  assertResponse(presented, "trusted-governance");
  if (presented.result.type !== "Allow" || !("run" in presented.result)) return presented;
  const dialog = presentationOf(presented)?.dialog;
  if (!dialog) return dismiss();
  // M1 (review 2), mirroring round 08's Claude fix: the bound document's core-derived
  // ``question`` chooses which dialog the human answers — `confirm_amendment` shows the
  // buttons-only confirm view with the document's (was …) priors, `approve` the editable
  // review view — never the adapter's own recursion flag. The flag survives only as the
  // re-entry guard (the stale-proposal check above and the recursion below).
  const confirm = bound !== undefined && bound.question === "confirm_amendment";
  // a raise is shown as one, beside the ceilings the run approved (the view's own, never recomputed here)
  const raise = !confirm && g.raises === true ? g.budgets : undefined;
  const selected = await showDialog(ctx, dialog, deadline, confirm, (state, width, theme, timeoutMs) =>
    renderLines(dialog, state, width, theme, { confirmation: confirm, document: bound, raise, timeoutMs }), signal);
  if (signal?.aborted || Date.now() >= deadline || selected.type === "dismiss") return dismiss();
  const action = selected.type === "reject" ? "reject" : "approve";
  const configuration = selected.type === "approve" ? selected.configuration
    : { budgets: g.proposal.budgets };
  const admitted = await trusted({ operation: "governance_decision", run_id: runId,
    payload: { ...mediationEnvelope, submission: { action, configuration } } });
  assertResponse(admitted, "trusted-governance");
  if (["Fault", "Block"].includes(admitted.result.type)) {
    const stored = await dismiss();
    return admitted.result.type === "Block" && "run" in stored.result
      ? { ...admitted, result: { ...admitted.result, run: stored.result.run } } : stored;
  }
  const next = "run" in admitted.result ? admitted.result.run?.governance as Governance | undefined : undefined;
  const revised = next && next.plan_revision !== g.plan_revision;
  if (!confirmation && action === "approve" && admitted.result.type === "Allow" && revised) {
    const fresh = await refreshGovernance(runId, ctx, trusted, true);
    return mediateGovernance(fresh, runId, ctx, trusted, signal,
      { revision: next.plan_revision, digest: next.proposal_digest }, deadline);
  }
  return admitted;
}

/** The private admission of one public `environment` action: the run and the revision its transaction admitted. */
export interface EnvironmentAdmission { run_id: string; revision: number }

/**
 * Mediate the human approval of the environment revision this very action admitted (Empirica 7.2 §5/§6,
 * ADR-0072 write-first): the private bridge writes the revision's document and records one present receipt, the
 * shared dialog frame shows the document's own facts, and the decision receipt names the written document's digest
 * and the admitted revision. Approve and reject decide; Esc, a timeout, an abort or a surface that shows nothing
 * leave the revision pending with only the presentation row. Every refusal of a private leg returns unchanged; a
 * response that is not an admission, an admission of another run, or a failing private leg returns the admitted
 * response as it came.
 */
export async function governEnvironment(response: Response, admission: EnvironmentAdmission | null,
                                        ctx: ExtensionContext, trusted: PrivateIngress, signal?: AbortSignal,
                                        deadline = Date.now() + governanceTimeout()): Promise<Response> {
  try { return await mediateEnvironment(response, admission, ctx, trusted, signal, deadline); }
  catch { return response; }
}

async function mediateEnvironment(response: Response, admission: EnvironmentAdmission | null,
                                  ctx: ExtensionContext, trusted: PrivateIngress, signal: AbortSignal | undefined,
                                  deadline: number): Promise<Response> {
  const result = response.result;
  if (result.type !== "Allow" || !("run" in result) || !result.run || admission?.run_id !== result.run.id)
    return response;
  if (!ctx.hasUI || !ctx.ui.custom || signal?.aborted || Date.now() >= deadline) return response;
  const runId = result.run.id;
  const shown = assertPrivateResponse("environment_present", await trusted({ operation: "environment_present",
    run_id: runId, payload: { revision: admission.revision } }));
  if (shown.type === "environment_refused") return checked(shown.response ?? response);
  const presented = checked(shown.response);
  // the receipt names the admitted revision only: a document of any other revision is never decided on
  if (shown.environment.revision !== admission.revision) return presented;
  const selected = await showDialog(ctx, ENVIRONMENT_CONTROLS, deadline, false, (state, width, theme, timeoutMs) =>
    renderEnvironmentLines(shown.environment, shown.document, state, width, theme, timeoutMs), signal);
  if (signal?.aborted || Date.now() >= deadline || selected.type === "dismiss") return presented;
  const decided = await trusted({ operation: "environment_decision", run_id: runId, payload: {
    run_id: runId, receipt_id: shown.receipt_id, revision: shown.environment.revision,
    proposal_digest: shown.environment.proposal_digest, outcome: selected.type,
    document_digest: shown.document.digest } });
  return checked(decided as Response);
}

function checked(response: Response): Response {
  assertResponse(response, "trusted-environment");
  return response;
}
