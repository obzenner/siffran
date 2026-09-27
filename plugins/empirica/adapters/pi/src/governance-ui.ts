// Documented ExtensionUIContext dialogs; signatures checked on installed Pi 0.87.1.
// Pinned 0.84.1 and native rendering still require separate qualification.
// No UI response is accepted as a public author action. Core revalidates every binding.
import { randomUUID } from "node:crypto";
import type { ExtensionContext } from "./pi-types.ts";
import type { PrivateIngress } from "./private-transport.ts";
import type { Response } from "./contract.ts";
import { assertResponse } from "./guard.ts";
import { PUBLIC_TOOLS } from "./public-tools.ts";

const RECOVERY = PUBLIC_TOOLS.recovery;
const DECISIONS = PUBLIC_TOOLS.governance_decisions;

interface Proposal {
  budgets: Record<string, number>; modes: Record<string, boolean>;
}
interface Governance {
  state: string; control_mode: string; proposal_digest: string; plan_revision: number;
  proposal: Proposal; prompt_error: string | null; budgets: Record<string, number>;
  review_text: string; context: { author: { provider_id: string; model_id: string } | null; ingress: string };
}

const USED_COUNTER: Record<string, string> = {
  max_passes: "passes_used", max_spawns: "spawns_used", max_audit_spawns: "audit_spawns_used",
};
const BUDGET_LABELS = Object.entries(DECISIONS.controls.budgets);
const MODE_LABELS = Object.entries(DECISIONS.controls.modes);
const DECISION_CHOICES = Object.values(DECISIONS.controls.actions);
const actionFor = (label: string) => Object.entries(DECISIONS.controls.actions).find(([, value]) => value === label)?.[0];
const MODE_CHOICES = ["Enabled", "Disabled"];

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

// Bounded 1..4 digits, then range: never floats, signs, exponents or unknown keys.
function parseBudget(key: string, raw: string, g: Governance): number | undefined {
  if (!/^\d{1,4}$/.test(raw)) return undefined;
  const value = Number(raw);
  const floor = Math.max(key === "max_passes" ? 1 : 0, g.budgets[USED_COUNTER[key]]);
  return value >= floor && value <= DECISIONS.controls.budgets[key].maximum ? value : undefined;
}

export async function govern(runId: string, ctx: ExtensionContext, trusted: PrivateIngress,
                             signal?: AbortSignal, confirmation?: { revision: number; digest: string },
                             deadline = Date.now() + governanceTimeout()): Promise<Response> {
  const response = await refreshGovernance(runId, ctx, trusted);
  const result = response.result;
  if (!("run" in result) || !result.run || !["Allow", "Inert"].includes(result.type)) return response;
  const g = result.run.governance as Governance | null;
  if (!g) return response;
  if (confirmation && (g.plan_revision !== confirmation.revision || g.proposal_digest !== confirmation.digest))
    return unavailable(response, "governance.stale_proposal");
  if (g.state === "approved") return response;
  const auto = g.control_mode === "auto";
  if (g.prompt_error) return unavailable(response, g.prompt_error);
  const revision = g.plan_revision;
  const envelope = { run_id: runId, receipt_id: randomUUID(), proposal_digest: g.proposal_digest,
    plan_revision: revision, approval_kind: auto ? "auto" : "host_ui" };
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
  if (!ctx.hasUI || !ctx.ui.select || !ctx.ui.confirm || !ctx.ui.input) return unavailable(response);
  const cancelled = (value: unknown) => value === undefined || signal?.aborted || Date.now() >= deadline;
  const options = () => {
    if (cancelled(null)) throw new Error("Governance dialog expired or cancelled");
    return { timeout: Math.max(1, deadline - Date.now()), signal };
  };
  if (cancelled(null)) return unavailable(response);
  const presented = await trusted({ operation: "governance_decision", run_id: runId,
    payload: { ...envelope, outcome: "present" } });
  assertResponse(presented, "trusted-governance");
  if (presented.result.type !== "Allow" || !("run" in presented.result)) return presented;
  let action = "approve", proposal: Proposal = structuredClone(g.proposal);
  try {
    if (confirmation) {
      const confirmed = await ctx.ui.confirm(DECISIONS.confirmation.title,
        g.review_text + "\nConfirm = approve this exact revision. Cancel = keep edits pending.", options());
      if (cancelled(confirmed) || confirmed !== true) return dismiss();
    } else {
      const reviewed = await ctx.ui.confirm(
        `Empirica run configuration — epoch ${g.plan_revision}`,
        g.review_text + `\nHost decision timeout (read-only): ${Math.ceil(Math.max(0, deadline - Date.now()) / 1000)} seconds.` +
          "\nOK = continue to the decision. Cancel = dismiss without approving anything.", options());
      if (cancelled(reviewed) || reviewed !== true) return dismiss();
      const choice = await ctx.ui.select("Decision", DECISION_CHOICES, options());
      if (cancelled(choice) || !DECISION_CHOICES.includes(choice!)) return dismiss();
      action = actionFor(choice!) ?? "";
      if (action === "edit") {
        for (const [key, row] of BUDGET_LABELS) {
          const raw = await ctx.ui.input(
            `${row.label} — proposed ${proposal.budgets[key]}, already used ${g.budgets[USED_COUNTER[key]]}. Empty keeps current.`, "", options());
          if (cancelled(raw)) return dismiss();
          if (raw === "") continue;
          const value = parseBudget(key, raw!, g);
          if (value === undefined) return dismiss();
          proposal.budgets[key] = value;
        }
        for (const [key, label] of MODE_LABELS) {
          const current = proposal.modes[key] ? "Enabled" : "Disabled";
          const value = await ctx.ui.select(`${label} — currently ${current}`, MODE_CHOICES, options());
          if (cancelled(value) || !MODE_CHOICES.includes(value!)) return dismiss();
          proposal.modes[key] = value === "Enabled";
        }
      }
    }
    const submission: Record<string, unknown> = { action, configuration: proposal };
    const admitted = await trusted({ operation: "governance_decision", run_id: runId,
      payload: { ...envelope, submission } });
    assertResponse(admitted, "trusted-governance");
    if (["Fault", "Block"].includes(admitted.result.type)) {
      const stored = await dismiss();
      return admitted.result.type === "Block" && "run" in stored.result
        ? { ...admitted, result: { ...admitted.result, run: stored.result.run } } : stored;
    }
    const next = "run" in admitted.result ? admitted.result.run?.governance as Governance | undefined : undefined;
    const revised = next && next.plan_revision !== revision;
    if (!confirmation && ["approve", "edit"].includes(action) && admitted.result.type === "Allow" && revised)
      return govern(runId, ctx, trusted, signal, { revision: next.plan_revision, digest: next.proposal_digest }, deadline);
    if (action === "edit" && !revised) return unavailable(admitted);
    return admitted;
  } catch { return dismiss(); }
}
