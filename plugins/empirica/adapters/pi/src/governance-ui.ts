// Documented ExtensionUIContext dialogs; signatures checked on installed Pi 0.87.1.
// Pinned 0.84.1 and native rendering still require separate qualification.
// No UI response is accepted as a public author action. Core revalidates every binding.
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as path from "node:path";
import type { ExtensionContext } from "./pi-types.ts";
import type { PrivateIngress } from "./private-transport.ts";
import type { Response } from "./contract.ts";
import { assertResponse } from "./guard.ts";

const PUBLIC_TOOLS_PATH = path.resolve(path.dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "..", "..", "contracts", "empirica", "v2", "public-tools.json");
interface DecisionControls {
  actions: Record<string, string>;
  budgets: Record<string, { label: string; maximum: number }>;
  modes: Record<string, string>; inventory: string; auditor: string; feedback: string;
}
const PUBLIC_TOOLS = JSON.parse(readFileSync(PUBLIC_TOOLS_PATH, "utf8")) as {
  recovery: Record<string, { message: string; sections: string[]; next_actions: string[] }>;
  governance_decisions: { controls: DecisionControls; confirmation: { title: string; actions: string[] } };
};
const RECOVERY = PUBLIC_TOOLS.recovery;
const DECISIONS = PUBLIC_TOOLS.governance_decisions;

interface Model { provider_id: string; model_id: string }
interface Proposal {
  budgets: Record<string, number>; modes: Record<string, boolean>;
  auditor: Model | null; allow_same_model: boolean;
}
interface Governance {
  state: string; control_mode: string; proposal_digest: string; plan_revision: number; revision_limit: number;
  proposal: Proposal; inventory_status: string; prompt_error: string | null; budgets: Record<string, number>;
  review_text: string; context: { inventory: { members: Model[]; source: string; complete: boolean; authorized: boolean };
             author: Model | null; ingress: string };
}

const USED_COUNTER: Record<string, string> = {
  max_passes: "passes_used", max_spawns: "spawns_used", max_audit_spawns: "audit_spawns_used",
};
const BUDGET_LABELS = Object.entries(DECISIONS.controls.budgets);
const MODE_LABELS = Object.entries(DECISIONS.controls.modes);
const DECISION_CHOICES = Object.values(DECISIONS.controls.actions);
const actionFor = (label: string) => Object.entries(DECISIONS.controls.actions).find(([, value]) => value === label)?.[0];
const MODE_CHOICES = ["Enabled", "Disabled"];

// Backslash is escaped so an actual ESC and the literal text "\x1b" stay distinguishable.
const HIDDEN = /[\\\u0000-\u001f\u007f-\u009f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]/gu;

export function safeGovernanceText(value: unknown): string {
  return String(value).replace(HIDDEN, ch => {
    if (ch === "\\") return "\\\\";
    const code = ch.codePointAt(0)!;
    return code <= 255 ? `\\x${code.toString(16).padStart(2, "0")}` : `\\u${code.toString(16).padStart(4, "0")}`;
  });
}

function modelName(model: Model): string {
  return `${model.provider_id}/${model.model_id}`;
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
  let action = "approve", proposal: Proposal = structuredClone(g.proposal), feedback = "";
  try {
    if (confirmation) {
      const confirmed = await ctx.ui.confirm(DECISIONS.confirmation.title,
        g.review_text + "\nConfirm = approve this exact revision. Cancel = keep edits pending.", options());
      if (cancelled(confirmed) || confirmed !== true) return dismiss();
    } else {
      const reviewed = await ctx.ui.confirm(
        `Empirica scope review — revision ${g.plan_revision} of at most ${g.revision_limit}`,
        g.review_text + "\nOK = continue to the decision. Cancel = dismiss without approving anything.", options());
      if (cancelled(reviewed) || reviewed !== true) return dismiss();
      const choice = await ctx.ui.select("Decision", DECISION_CHOICES, options());
      if (cancelled(choice) || !DECISION_CHOICES.includes(choice!)) return dismiss();
      action = actionFor(choice!) ?? "";
      if (action === "request_changes") {
        const text = await ctx.ui.input(DECISIONS.controls.feedback, "", options());
        if (cancelled(text) || !text!.trim()) return dismiss();
        feedback = text!;
      } else if (action === "edit") {
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
      if (["approve", "edit"].includes(action) && (!proposal.auditor || action === "edit")) {
        const names = g.context.inventory.members.map(m => safeGovernanceText(modelName(m)));
        const selected = await ctx.ui.select(DECISIONS.controls.auditor, names, options());
        if (cancelled(selected) || !names.includes(selected!)) return dismiss();
        proposal.auditor = g.context.inventory.members[names.indexOf(selected!)];
      }
    }
    const inventoryConfirmed = action === "approve" ? await ctx.ui.confirm(DECISIONS.controls.inventory,
      "Confirm that the displayed model inventory is complete and authorized for this run.", options()) : false;
    if (action === "approve" && (cancelled(inventoryConfirmed) || inventoryConfirmed !== true)) return dismiss();
    let sameModel = false;
    if (["approve", "edit"].includes(action) && g.inventory_status === "singleton") {
      const editing = action === "edit";
      const consent = await ctx.ui.confirm(editing ? "SAME MODEL — proposal only" : "SAME MODEL — LOWERED INDEPENDENCE",
        editing ? "Propose this exception for locked review? Nothing is approved." :
          "Explicitly consent to this verified authorized singleton exception? This is not independent-model review.", options());
      if (cancelled(consent) || (!editing && consent !== true)) return dismiss();
      if (!confirmation) proposal.allow_same_model = consent === true;
      sameModel = !editing && consent === true;
    }
    const submission: Record<string, unknown> = { action, configuration: proposal,
      inventory_confirmed: inventoryConfirmed, allow_same_model: sameModel };
    if (action === "request_changes") submission.feedback = feedback;
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
    if (action === "request_changes" && ["Allow", "Inert"].includes(admitted.result.type))
      return unavailable(admitted, "governance.changes_requested");
    if (action === "edit" && !revised) return unavailable(admitted);
    return admitted;
  } catch { return dismiss(); }
}
