import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

export interface DecisionControls {
  actions: Record<string, string>;
  budgets: Record<string, { label: string; maximum: number }>;
}

export type AuthorViewLabel =
  | "no_governance"
  | "governance"
  | "budget_usage"
  | "converged"
  | "run_id"
  | "proposal_rationale"
  | "audit"
  | "finding"
  | "reasons"
  | "open_obligations"
  | "satisfied_obligations"
  | "residuals"
  | "children"
  | "freshness"
  | "next"
  | "affected"
  | "params"
  | "next_inline"
  | "missing"
  | "recovery"
  | "via_claim"
  | "argument"
  | "goal"
  | "root_claim_id"
  | "claims"
  | "edges"
  | "citations"
  | "audit_status"
  | "claim_kind"
  | "claim_gating"
  | "claim_state"
  | "claim_evidence"
  | "evidence_present"
  | "evidence_none"
  | "contract"
  | "block"
  | "fault"
  | "inert";

/** Contract-owned author-view headings and line labels (contract `author_view.labels`). */
export type AuthorViewLabels = Readonly<Record<AuthorViewLabel, string>>;

export interface PublicToolsProjection {
  definitions: Record<string, { title: string; description: string }>;
  schemas: { host_handle: Record<string, Record<string, unknown>> };
  host_profiles: Record<string, { delegation_env: string }>;
  start_refusal_codes: string[];
  recovery: Record<string, { message: string; sections: string[]; next_actions: string[] }>;
  governance_decisions: {
    controls: DecisionControls;
    confirmation: { title: string; actions: string[] };
  };
  settlement_notices: { human_wait: string; budget_exhausted: string };
  author_view: { labels: AuthorViewLabels };
}

const PUBLIC_TOOLS_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..",
  "contracts", "empirica", "v2", "public-tools.json",
);

export const PUBLIC_TOOLS = JSON.parse(
  readFileSync(PUBLIC_TOOLS_PATH, "utf8"),
) as PublicToolsProjection;
