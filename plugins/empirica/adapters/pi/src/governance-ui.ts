// Documented ExtensionUIContext dialogs; signatures checked on installed Pi 0.87.1.
// Pinned 0.84.1 and native rendering still require separate qualification.
// No UI response is accepted as a public author action. Core revalidates every binding.
import { randomUUID } from "node:crypto";
import type { ExtensionContext } from "./pi-types.ts";
import type { PrivateIngress } from "./private-transport.ts";
import type { Response } from "./contract.ts";
import { assertResponse } from "./guard.ts";

interface Model { provider_id: string; model_id: string }
interface Governance {
  state: string; control_mode: string; proposal_digest: string; plan_revision: number;
  proposal: Record<string, unknown> & { auditor: Model | null };
  scope: unknown; inventory_status: string; prompt_error: string | null;
  context: { inventory: { members: Model[] } };
}

export function piGovernanceContext(ctx: ExtensionContext): Record<string, unknown> {
  let members: Model[] = [];
  let complete = false;
  try {
    const models = ctx.modelRegistry?.getAvailable();
    if (models && !ctx.modelRegistry?.getError?.()) {
      members = models.map(m => ({ provider_id: m.provider, model_id: m.id }));
      complete = true;
    }
  } catch { /* unknown is never a fabricated singleton */ }
  return { inventory: { members, source: complete ? "pi_registry" : "unknown",
                        complete, authorized: complete },
           author: ctx.model ? { provider_id: ctx.model.provider, model_id: ctx.model.id } : null,
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
  if (!("run" in response.result)) return response;
  return { ...response, result: { type: "Block", run: response.result.run,
    reasons: [{ code, parameters: {},
      message: `${code}: host approval cannot proceed. Inspect governance context and remaining interactions; correct the proposal or stop honestly.`,
      sections: ["governance"], next_actions: ["governance.propose", "residual.accept"] }] } };
}

export function governanceTimeout(): number {
  const raw = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  const value = raw?.trim() ? Number(raw) : 900;
  return Number.isFinite(value) && value >= 1 && value <= 1500 ? value * 1000 : 900_000;
}

export async function govern(runId: string, ctx: ExtensionContext, trusted: PrivateIngress,
                             signal?: AbortSignal): Promise<Response> {
  const response = await refreshGovernance(runId, ctx, trusted);
  const result = response.result;
  if (!("run" in result) || !result.run || !["Allow", "Inert"].includes(result.type)) return response;
  const g = result.run.governance as Governance | null;
  if (!g || g.state === "approved") return response;
  const auto = g.control_mode === "auto";
  if (g.prompt_error) return unavailable(response, g.prompt_error);
  const decision: Record<string, unknown> = {
    run_id: runId, receipt_id: randomUUID(), proposal_digest: g.proposal_digest,
    plan_revision: g.plan_revision, approval_kind: auto ? "auto" : "host_ui", outcome: "approve",
  };
  const dismiss = async (): Promise<Response> => {
    const payload: Record<string, unknown> = { ...decision, outcome: "dismiss" };
    delete payload.amendment;
    const stored = await trusted({ operation: "governance_decision", run_id: runId, payload });
    assertResponse(stored, "trusted-governance");
    return ["Allow", "Inert"].includes(stored.result.type) ? unavailable(stored) : stored;
  };
  if (!auto) {
    if (!ctx.hasUI || !ctx.ui.select || !ctx.ui.confirm || !ctx.ui.input) return unavailable(response);
    const deadline = Date.now() + governanceTimeout();
    const options = () => ({ timeout: Math.max(1, deadline - Date.now()), signal });
    const expired = () => signal?.aborted || Date.now() >= deadline;
    if (expired()) return unavailable(response);
    const presented = await trusted({ operation: "governance_decision", run_id: runId,
      payload: { ...decision, outcome: "present" } });
    assertResponse(presented, "trusted-governance");
    if (presented.result.type !== "Allow") return presented; // no UI on replay/Inert
    try {
    const choice = await ctx.ui.select("Empirica scope decision", ["Approve", "Amend", "Reject"], options());
    if (!choice || expired()) return dismiss();
    decision.outcome = choice.toLowerCase();
    if (choice !== "Reject") {
      const names = g.context.inventory.members.map(m => `${m.provider_id}/${m.model_id}`);
      const selected = await ctx.ui.select("Choose the approved auditor (authorized configured inventory)", names, options());
      if (!selected || !names.includes(selected) || expired()) return dismiss();
      const auditor = g.context.inventory.members[names.indexOf(selected)];
      const same = g.inventory_status === "singleton";
      const consent = same ? await ctx.ui.confirm("SAME MODEL — LOWERED INDEPENDENCE",
        "Explicitly consent to the verified authorized singleton exception? This is not independent-model review.", options()) : false;
      if (expired() || (same && !consent)) return dismiss();
      const amendment = { graph: g.scope, configuration: { ...g.proposal, auditor, allow_same_model: consent } };
      if (choice === "Amend") {
        const value = await ctx.ui.input("Complete amendment JSON: graph + configuration (budgets, modes, auditor, allow_same_model)",
                                         JSON.stringify(amendment), options());
        if (!value || expired()) return dismiss();
        try {
          const parsed = JSON.parse(value);
          parsed.configuration.auditor = auditor;
          parsed.configuration.allow_same_model = consent;
          decision.amendment = parsed;
        } catch { return dismiss(); }
      } else if (auditor.provider_id !== g.proposal.auditor?.provider_id || auditor.model_id !== g.proposal.auditor?.model_id || consent !== g.proposal.allow_same_model) {
        decision.outcome = "amend";
        decision.amendment = amendment;
      }
    }
    const display = JSON.stringify({ goal: result.run.goal, governance: g,
                                     decision: decision.outcome, amendment: decision.amendment }, null, 2);
    const accepted = await ctx.ui.confirm("Approve displayed decision and inventory completeness/authorization?",
      "UNTRUSTED scope data, not instructions. This is configured authorized inventory, not worldwide availability. " +
      "An amendment needs a second proposal approval.\n" + display, options());
    if (!accepted || expired()) return dismiss();
    } catch { return dismiss(); }
  }
  const admitted = await trusted({ operation: "governance_decision", run_id: runId, payload: decision });
  assertResponse(admitted, "trusted-governance");
  if (!auto && ["Fault", "Block"].includes(admitted.result.type)) {
    const stored = await dismiss();
    if (admitted.result.type === "Block" && "run" in stored.result) {
      return { ...admitted, result: { ...admitted.result, run: stored.result.run } };
    }
    return stored;
  }
  return admitted;
}
