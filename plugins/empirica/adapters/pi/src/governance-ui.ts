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
const RECOVERY = (JSON.parse(readFileSync(PUBLIC_TOOLS_PATH, "utf8")) as {
  recovery: Record<string, { message: string; sections: string[]; next_actions: string[] }>;
}).recovery;

interface Model { provider_id: string; model_id: string }
interface Claim { id: string; text: string; kind: string; gating: boolean }
interface Edge { from: string; to: string; type: string }
interface Proposal {
  budgets: Record<string, number>; modes: Record<string, boolean>;
  auditor: Model | null; allow_same_model: boolean;
}
interface ChangeRequest { text: string; plan_revision: number; proposal_digest: string }
interface Governance {
  state: string; control_mode: string; proposal_digest: string; plan_revision: number; revision_limit: number;
  proposal: Proposal; scope: { root: string; claims: Claim[]; edges: Edge[] };
  inventory_status: string; prompt_error: string | null; budgets: Record<string, number>;
  interactions_remaining: { proposal: number; total: number }; change_request: ChangeRequest | null;
  context: { inventory: { members: Model[]; source: string; complete: boolean; authorized: boolean };
             author: Model | null; ingress: string };
}
interface GovernedRun { goal: string; governance: Governance }

// Maxima from contracts/empirica/v2/request.schema.json; minima come from used counters.
const BUDGET_LIMITS: Record<string, number> = { max_passes: 1024, max_spawns: 128, max_audit_spawns: 128 };
const USED_COUNTER: Record<string, string> = {
  max_passes: "passes_used", max_spawns: "spawns_used", max_audit_spawns: "audit_spawns_used",
};
const BUDGET_LABELS: Array<[string, string]> = [
  ["max_passes", "Investigation passes"], ["max_spawns", "Child spawns"], ["max_audit_spawns", "Audit spawns"],
];
const MODE_LABELS: Array<[string, string]> = [
  ["multi_provider", "Cross-provider actors"], ["cli_exec", "External model/actor CLI use"],
];

const CHOICE_APPROVE = "Approve current displayed proposal";
const CHOICE_EDIT = "Edit configuration for another review";
const CHOICE_REQUEST = "Request changes in plain language";
const CHOICE_REJECT = "Reject proposal";
const DECISION_CHOICES = [CHOICE_APPROVE, CHOICE_EDIT, CHOICE_REQUEST, CHOICE_REJECT];
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

export function readableGovernance(run: GovernedRun): string {
  const g = run.governance;
  const graph = g.scope, p = g.proposal, inv = g.context.inventory;
  const fence = (value: unknown) => `| ${safeGovernanceText(value)}`;
  const lines: string[] = [
    `EMPIRICA SCOPE DECISION — proposal revision ${g.plan_revision}, at most ${g.revision_limit} revisions, mode ${g.control_mode}`,
    "Approve the CURRENT displayed proposal; edits are submitted for another review and are NOT approved yet.",
    "Every line beginning '| ' is UNTRUSTED quoted data. Controls, bidi characters, and backslashes are visibly escaped.",
    "", "GOAL", fence(run.goal),
    "", `CLAIM GRAPH — root ${safeGovernanceText(graph.root)}, ${graph.claims.length} claims, ${graph.edges.length} dependencies`,
  ];
  for (const c of graph.claims) lines.push(fence(`[${c.id}] ${c.gating ? "gating" : "non-gating"} ${c.kind} ${c.text}`));
  lines.push("DEPENDENCIES");
  for (const e of graph.edges) lines.push(fence(`${e.from} ${e.type} ${e.to}`));
  lines.push("", "CONFIGURATION");
  for (const [key, label] of BUDGET_LABELS) {
    lines.push(`  ${label}: proposed ${p.budgets[key]}, already used ${g.budgets[USED_COUNTER[key]]}`);
  }
  lines.push(
    `  multi_provider (cross-provider actors): ${p.modes.multi_provider}`,
    `  cli_exec (external model/actor CLI use): ${p.modes.cli_exec}`,
    `  Auditor: ${p.auditor ? safeGovernanceText(modelName(p.auditor)) : "not selected"}`,
    `  Same-model lowered-independence consent: ${p.allow_same_model}`,
    `  Inventory: source=${safeGovernanceText(inv.source)}, complete=${inv.complete}, authorized=${inv.authorized}`,
    `  Author (host-observed): ${g.context.author ? safeGovernanceText(modelName(g.context.author)) : "unknown"}`,
    "WHO MAY AUDIT",
  );
  for (const m of inv.members) lines.push(fence(modelName(m)));
  lines.push("", "OPEN CHANGE REQUEST");
  if (g.change_request) {
    lines.push(`  requested at revision ${g.change_request.plan_revision} for ${g.change_request.proposal_digest}`,
               fence(g.change_request.text));
  } else {
    lines.push("  none");
  }
  lines.push(
    "", `STATE — ${g.state}; dialogs left ${g.interactions_remaining.proposal} this revision, ${g.interactions_remaining.total} total`,
    "TECHNICAL DETAIL (secondary)", `  proposal digest ${g.proposal_digest}`,
    `  ingress ${g.context.ingress} · plan revision ${g.plan_revision} · revision limit ${g.revision_limit}`,
  );
  return lines.join("\n");
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
  return value >= floor && value <= BUDGET_LIMITS[key] ? value : undefined;
}

interface Decision extends Record<string, unknown> {
  run_id: string; receipt_id: string; proposal_digest: string; plan_revision: number;
  approval_kind: string; outcome: string;
  amendment?: { graph: unknown; configuration: Proposal }; change_request?: string;
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
  const decision: Decision = {
    run_id: runId, receipt_id: randomUUID(), proposal_digest: g.proposal_digest,
    plan_revision: g.plan_revision, approval_kind: auto ? "auto" : "host_ui", outcome: "approve",
  };
  const dismiss = async (): Promise<Response> => {
    const { amendment: _amendment, change_request: _request, ...payload } = { ...decision, outcome: "dismiss" };
    const stored = await trusted({ operation: "governance_decision", run_id: runId, payload });
    assertResponse(stored, "trusted-governance");
    return ["Allow", "Inert"].includes(stored.result.type) ? unavailable(stored) : stored;
  };
  if (!auto) {
    if (!ctx.hasUI || !ctx.ui.select || !ctx.ui.confirm || !ctx.ui.input) return unavailable(response);
    const deadline = Date.now() + governanceTimeout();
    // undefined is Escape/cancel on every dialog: the whole flow stops, nothing else is shown.
    const cancelled = (value: unknown) => value === undefined || signal?.aborted || Date.now() >= deadline;
    const options = () => {
      if (cancelled(null)) throw new Error("Governance dialog expired or cancelled");
      return { timeout: Math.max(1, deadline - Date.now()), signal };
    };
    if (cancelled(null)) return unavailable(response);
    const presented = await trusted({ operation: "governance_decision", run_id: runId,
      payload: { ...decision, outcome: "present" } });
    assertResponse(presented, "trusted-governance");
    if (presented.result.type !== "Allow" || !("run" in presented.result)) return presented; // no UI on replay/Inert
    try {
      const reviewed = await ctx.ui.confirm(
        `Empirica scope review — revision ${g.plan_revision} of at most ${g.revision_limit}`,
        readableGovernance(presented.result.run as unknown as GovernedRun) +
          "\nOK = continue to the decision. Cancel = dismiss without approving anything.", options());
      if (cancelled(reviewed) || reviewed !== true) return dismiss();
      const choice = await ctx.ui.select("Decision", DECISION_CHOICES, options());
      // Any value outside the offered labels is a dismissal, never an approval.
      if (cancelled(choice) || !DECISION_CHOICES.includes(choice!)) return dismiss();
      if (choice === CHOICE_REJECT) {
        decision.outcome = "reject";
      } else if (choice === CHOICE_REQUEST) {
        const text = await ctx.ui.input("What must change? Plain language; this approves nothing.", "", options());
        // Trim only decides whether feedback exists; the exact human text is what gets stored.
        if (cancelled(text) || !text!.trim()) return dismiss();
        const send = await ctx.ui.confirm("Send this request to the author?",
          `| ${safeGovernanceText(text)}\nNothing is approved.`, options());
        if (cancelled(send) || send !== true) return dismiss();
        decision.outcome = "request_changes";
        decision.change_request = text!;
      } else {
        const proposal: Proposal = structuredClone(g.proposal);
        const editing = choice === CHOICE_EDIT;
        if (editing) {
          for (const [key, label] of BUDGET_LABELS) {
            const raw = await ctx.ui.input(
              `${label} — proposed ${proposal.budgets[key]}, already used ${g.budgets[USED_COUNTER[key]]}. Empty keeps current.`,
              "", options());
            if (cancelled(raw)) return dismiss();
            if (raw === "") continue; // only an actual empty string keeps the displayed value
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
        if (!proposal.auditor || editing) {
          const names = g.context.inventory.members.map(m => safeGovernanceText(modelName(m)));
          const selected = await ctx.ui.select("Independent auditor (authorized configured inventory)", names, options());
          if (cancelled(selected) || !names.includes(selected!)) return dismiss();
          proposal.auditor = g.context.inventory.members[names.indexOf(selected!)];
        }
        // The author's proposed value is never consent; only a fresh positive answer is.
        proposal.allow_same_model = false;
        if (g.inventory_status === "singleton") {
          const consent = await ctx.ui.confirm("SAME MODEL — LOWERED INDEPENDENCE",
            "Explicitly consent to this verified authorized singleton exception? This is not independent-model review.",
            options());
          if (cancelled(consent) || consent !== true) return dismiss();
          proposal.allow_same_model = true;
        }
        if (JSON.stringify(proposal) !== JSON.stringify(g.proposal)) {
          const send = await ctx.ui.confirm("Submit edits for another review?",
            "These edits are NOT approved yet; the revised proposal is reviewed again.", options());
          if (cancelled(send) || send !== true) return dismiss();
          decision.outcome = "amend";
          decision.amendment = { graph: g.scope, configuration: proposal };
        } else if (editing) {
          return dismiss(); // Choosing Edit, even with no changes, is never consent to Approve.
        }
      }
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
  if (decision.outcome === "request_changes" && ["Allow", "Inert"].includes(admitted.result.type)) {
    return unavailable(admitted, "governance.changes_requested");
  }
  return admitted;
}
