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
  | "spike_backend"
  | "spike_backend_unavailable"
  | "spike_backend_qualified"
  | "spike_backend_record"
  | "spike_backend_execution"
  | "proposal_rationale"
  | "host_unreviewed"
  | "host_unreviewed_note"
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
  | "inert"
  | "waiting"
  | "waiting_record"
  | "waiting_claim"
  | "waiting_terminal"
  | "waiting_for_claim"
  | "waiting_none_error"
  | "waiting_unavailable"
  | "waiting_already_presented"
  | "waiting_interaction_limit"
  | "waiting_surface_unrecorded"
  | "waiting_presentation_failed"
  | "waiting_unobserved"
  | "verified_by_service"
  | "confirmed_by_human"
  | "rejected_by_human"
  | "pending_human"
  | "pending_audit"
  | "human_note"
  | "decision_approve"
  | "decision_reject"
  | "human_answer";

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
  settlement_notices: { human_wait: string; evidence_wait: string; evidence_report: string; budget_exhausted: string;
    paused_for_human: string; paused_for_evidence: string; unattended_continue: string };
  author_view: { labels: AuthorViewLabels };
}

/**
 * The plugin-local contract copy (`make vendor-contracts`, byte-identical to the repository SSOT).
 * Three directories up from `src/` is the plugin root, so an installed `plugins/empirica/` alone
 * carries every document the adapter reads. Every other reader imports this one directory.
 */
export const VENDORED_CONTRACT_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..",
  "vendor", "contracts", "empirica", "v2",
);

/** Read one vendored contract document; the one loader every adapter module shares. */
export function readVendored(name: string): any {
  return JSON.parse(readFileSync(path.join(VENDORED_CONTRACT_DIR, name), "utf8"));
}

export const PUBLIC_TOOLS = readVendored("public-tools.json") as PublicToolsProjection;

type Schema = Record<string, any>;

/** The vendored response and request schema definitions every derived contract value is read from. */
export const RESPONSE_DEFS = readVendored("response.schema.json").$defs as Schema;
export const REQUEST_DEFS = readVendored("request.schema.json").$defs as Schema;

/** The closed string values an `enum`/`const` schema admits; any other shape refuses to load. */
export function schemaValues<T extends string = string>(schema: Schema | undefined): ReadonlySet<T> {
  const values: unknown[] = Array.isArray(schema?.enum) ? schema.enum
    : schema !== undefined && "const" in schema ? [schema.const] : [];
  if (values.length === 0 || !values.every((value) => typeof value === "string"))
    throw new Error(`contract schema is not a closed string value set: ${JSON.stringify(schema)}`);
  return new Set(values as T[]);
}

/** The one value a `const` schema admits; any other shape refuses to load. */
export function schemaConst(schema: Schema | undefined): string {
  if (typeof schema?.const !== "string")
    throw new Error(`contract schema is not a string const: ${JSON.stringify(schema)}`);
  return schema.const;
}

/** The one boolean a `const` schema admits; any other shape refuses to load. */
export function schemaBoolean(schema: Schema | undefined): boolean {
  if (typeof schema?.const !== "boolean")
    throw new Error(`contract schema is not a boolean const: ${JSON.stringify(schema)}`);
  return schema.const;
}

const PATTERN_KEYS = new Set(["type", "pattern", "description"]);

/**
 * The anchored pattern a string schema admits; any other admission keyword (`format`, `enum`, ...),
 * an unanchored pattern, or a top-level alternation (`|`, which `^...$` would not anchor) refuses to load.
 */
export function schemaPattern(schema: Schema | undefined): RegExp {
  const pattern = schema?.pattern;
  if (schema?.type !== "string" || !Object.keys(schema).every((key) => PATTERN_KEYS.has(key))
      || typeof pattern !== "string" || !pattern.startsWith("^") || !pattern.endsWith("$")
      || pattern.endsWith("\\$") || pattern.includes("|"))
    throw new Error(`contract schema is not a closed anchored string pattern: ${JSON.stringify(schema)}`);
  return new RegExp(pattern);
}

/** The branches of a nonempty `oneOf` schema; any other shape refuses to load. */
export function schemaBranches(schema: Schema | undefined): readonly Schema[] {
  if (!Array.isArray(schema?.oneOf) || schema.oneOf.length === 0)
    throw new Error(`contract schema is not a closed oneOf: ${JSON.stringify(schema)}`);
  return schema.oneOf;
}

/** The exact keys of a closed object schema (no extra properties, every property required). */
export function closedKeys(schema: Schema | undefined): readonly string[] {
  const keys = Object.keys(schema?.properties ?? {}).sort();
  if (schema?.type !== "object" || schema.additionalProperties !== false || keys.length === 0
      || !Array.isArray(schema.required) || [...schema.required].sort().join("\0") !== keys.join("\0"))
    throw new Error(`contract schema is not a closed object: ${JSON.stringify(schema)}`);
  return keys;
}
