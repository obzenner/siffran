import { randomUUID } from "node:crypto";
import type { ExtensionContext } from "./pi-types.ts";
import type { PrivateIngress } from "./private-transport.ts";
import type { Response } from "./contract.ts";
import { PROTOCOL } from "./contract.ts";
import { assertResponse } from "./guard.ts";
import { PUBLIC_TOOLS } from "./public-tools.ts";
import { initialState, reduce, renderLines } from "./dialog-view.ts";
import type { Dialog, DialogDecision, DialogState, DialogTheme } from "./dialog-view.ts";

const RECOVERY = PUBLIC_TOOLS.recovery;
interface Proposal { budgets: Record<string, number> }
interface Governance {
  state: string; control_mode: string; proposal_digest: string; plan_revision: number;
  proposal: Proposal; prompt_error: string | null;
  context: { author: { provider_id: string; model_id: string } | null; ingress: string };
}
interface Presentation { dialog: Dialog; scope: unknown }

function presentationOf(response: Response): Presentation | undefined {
  return (response.result as { presentation?: Presentation }).presentation;
}

function stripPresentation(response: Response): Response {
  if (response && typeof response === "object" && "presentation" in (response.result as object)) {
    const { presentation: _dropped, ...rest } = response.result as Record<string, unknown>;
    return { ...response, result: rest } as Response;
  }
  return response;
}

export function piGovernanceContext(ctx: ExtensionContext): Record<string, unknown> {
  return { author: ctx.model ? { provider_id: ctx.model.provider, model_id: ctx.model.id,
                                 source: "pi-context" } : null,
           ingress: ctx.hasUI ? "pi_ui" : "unavailable" };
}

export async function refreshGovernance(runId: string, ctx: ExtensionContext,
                                        trusted: PrivateIngress): Promise<Response> {
  const result = await trusted({ operation: "governance_context", run_id: runId,
                                payload: piGovernanceContext(ctx) });
  assertResponse(result, "trusted-governance");
  return result;
}

function unavailable(response: Response, code = "governance.approval_unavailable"): Response {
  if (!(code in RECOVERY)) throw new Error(`Missing canonical governance recovery metadata: ${code}`);
  if (!("run" in response.result)) return response;
  return { ...response, result: { type: "Block", run: response.result.run,
    reasons: [{ code, parameters: {}, ...RECOVERY[code] }] } };
}

export function governanceTimeout(): number {
  const raw = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  const value = raw?.trim() ? Number(raw) : 900;
  return Number.isFinite(value) && value >= 1 && value <= 1500 ? value * 1000 : 900_000;
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

/** Run one native custom component with shared deadline and abort handling. */
async function showDialog(ctx: ExtensionContext, dialog: Dialog, deadline: number,
                          confirmation: boolean, signal?: AbortSignal,
                          before?: Dialog): Promise<DialogDecision> {
  if (!ctx.ui.custom) return { type: "dismiss" };
  return ctx.ui.custom<DialogDecision>((tui, hostTheme, _keybindings, done) => {
    let state: DialogState = initialState(dialog, confirmation);
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
      render: width => renderLines(dialog, state, width, theme,
        { confirmation, before, timeoutMs: Math.max(0, deadline - Date.now()) }),
      invalidate: () => {},
      handleInput: data => {
        const outcome = reduce(state, data, dialog, confirmation);
        if ("done" in outcome) finish(outcome.done);
        else { state = outcome.state; tui.requestRender(); }
      },
    };
    return component;
  });
}

export async function govern(runId: string, ctx: ExtensionContext, trusted: PrivateIngress,
                             signal?: AbortSignal, confirmation?: { revision: number; digest: string; before: Dialog },
                             deadline = Date.now() + governanceTimeout(), fallback?: Response): Promise<Response> {
  let response: Response;
  try { response = await refreshGovernance(runId, ctx, trusted); }
  catch { return fallback && "run" in fallback.result ? stripPresentation(unavailable(fallback)) : closedFault(); }
  try { return stripPresentation(await mediateGovernance(response, runId, ctx, trusted, signal, confirmation, deadline)); }
  catch { return stripPresentation("run" in response.result ? unavailable(response) : response); }
}

async function mediateGovernance(response: Response, runId: string, ctx: ExtensionContext,
                                 trusted: PrivateIngress, signal?: AbortSignal,
                                 confirmation?: { revision: number; digest: string; before: Dialog },
                                 deadline = Date.now() + governanceTimeout()): Promise<Response> {
  const result = response.result;
  if (!("run" in result) || !result.run || !["Allow", "Inert"].includes(result.type)) return response;
  const g = result.run.governance as Governance | null;
  if (!g) return response;
  if (confirmation && (g.plan_revision !== confirmation.revision || g.proposal_digest !== confirmation.digest))
    return unavailable(response, "governance.stale_proposal");
  if (g.state === "approved") return response;
  const auto = g.control_mode === "auto";
  if (g.prompt_error) return unavailable(response, g.prompt_error);
  const envelope = { run_id: runId, receipt_id: randomUUID(), proposal_digest: g.proposal_digest,
    plan_revision: g.plan_revision, approval_kind: auto ? "auto" : "host_ui" };
  const dismiss = async (): Promise<Response> => {
    const stored = await trusted({ operation: "governance_decision", run_id: runId,
      payload: { ...envelope, outcome: "dismiss" } });
    assertResponse(stored, "trusted-governance");
    return ["Allow", "Inert"].includes(stored.result.type) ? unavailable(stored) : stored;
  };
  if (auto) {
    const admitted = await trusted({ operation: "governance_decision", run_id: runId,
      payload: { ...envelope, outcome: "approve" } });
    assertResponse(admitted, "trusted-governance");
    return admitted;
  }
  if (!ctx.hasUI || !ctx.ui.custom || signal?.aborted || Date.now() >= deadline) return unavailable(response);
  const presented = await trusted({ operation: "governance_decision", run_id: runId,
    payload: { ...envelope, outcome: "present" } });
  assertResponse(presented, "trusted-governance");
  if (presented.result.type !== "Allow" || !("run" in presented.result)) return presented;
  const dialog = presentationOf(presented)?.dialog;
  if (!dialog) return dismiss();
  const selected = await showDialog(ctx, dialog, deadline, Boolean(confirmation), signal,
    confirmation?.before);
  if (signal?.aborted || Date.now() >= deadline || selected.type === "dismiss") return dismiss();
  const action = selected.type === "reject" ? "reject" : "approve";
  const configuration = selected.type === "approve" ? selected.configuration : g.proposal;
  const admitted = await trusted({ operation: "governance_decision", run_id: runId,
    payload: { ...envelope, submission: { action, configuration } } });
  assertResponse(admitted, "trusted-governance");
  if (["Fault", "Block"].includes(admitted.result.type)) {
    const stored = await dismiss();
    return admitted.result.type === "Block" && "run" in stored.result
      ? { ...admitted, result: { ...admitted.result, run: stored.result.run } } : stored;
  }
  const next = "run" in admitted.result ? admitted.result.run?.governance as Governance | undefined : undefined;
  const revised = next && next.plan_revision !== g.plan_revision;
  if (!confirmation && action === "approve" && admitted.result.type === "Allow" && revised) {
    const fresh = await refreshGovernance(runId, ctx, trusted);
    return mediateGovernance(fresh, runId, ctx, trusted, signal,
      { revision: next.plan_revision, digest: next.proposal_digest, before: dialog }, deadline);
  }
  return admitted;
}
