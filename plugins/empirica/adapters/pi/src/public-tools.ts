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
  | "mode"
  | "mode_delegated"
  | "budget_usage"
  | "converged"
  | "run_id"
  | "spike_backend"
  | "spike_backend_unavailable"
  | "spike_backend_qualified"
  | "spike_backend_record"
  | "spike_backend_execution"
  | "environment"
  | "environment_images"
  | "qualification"
  | "qualification_bound"
  | "qualification_bound_note"
  | "qualification_unbound"
  | "qualification_failure"
  | "qualification_probe"
  | "argument_environment"
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
  | "budget_raise_note"
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
    paused_for_human: string; paused_for_evidence: string; unattended_continue: string; unattended_residual: string };
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

/** The semantic keys a string-pattern schema may carry; `x-`-prefixed extension members (ADR-0075
 * registration annotations, vendor documentation) carry no validation meaning and are nonsemantic. */
const PATTERN_KEYS = new Set(["type", "pattern", "description"]);

/** The absolute-end lookahead the re-anchored contract word uses — its `\s`/`\S` are part of the
 * one atom the page may serve, so the atom itself is allowlisted, not its classes. */
const ABSOLUTE_END = "(?![\\s\\S])";

/** The positive bounded grammar this page's one pattern caller (the digest guard) may serve,
 * after `^` is stripped and the absolute end taken off: a literal prefix of `[a-z0-9:]` characters,
 * then exactly one `[0-9a-f]` class with a fixed positive count — no escape class, no dot, no
 * group, no alternation, no other quantifier. A blacklist of
 * disagreeing atoms cannot hold that word: the class-escape list admitted `^\q`, an identity
 * escape JavaScript compiles and Python refuses at compile time, and the seed resting a `(?!.)`
 * on the Unicode line separator U+2028, which JavaScript's dot never matches (the `u` flag changes
 * neither that nor dot semantics) while Python's does — the engines' `\d`/`\w`/`\s` families
 * apart, their whitespace sets are simply *different*, not a Unicode-versus-ASCII split, so no
 * restatement of “which escapes disagree” is the boundary. The boundary is the grammar itself:
 * the digest word matches it, and both engines read every admitted pattern identically.
 *
 * The one closed end is the absolute lookahead; a `$` anchor never loads. JSON Schema's `pattern` is Python `re.search`, whose `$` matches before a final
 * line feed, while JavaScript's unescaped `$` (no `m` flag) refuses that value — the same
 * split that made the core re-anchor its digest word, so the page admits no `$`-anchored word
 * at all, not even the anchored digest itself: only `(?![\s\S])` closes the grammar. */
const BOUNDED_WORD = /^[a-z0-9:]*\[0-9a-f\]\{[1-9][0-9]*\}$/;

/** The anchored pattern a string schema serves: exactly the bounded digest-guard word — `^`,
 * that literal prefix, the one fixed-count class, and the one closed end, the absolute
 * `(?![\s\S])` lookahead. Any other admission keyword (`format`, `enum`, ...), an unanchored,
 * escaped or dollar end, a top-level alternation (`|`, which `^...` would not anchor) refuses to
 * load.
 */
export function schemaPattern(schema: Schema | undefined): RegExp {
  const pattern = schema?.pattern;
  const body = typeof pattern !== "string" ? undefined
    : pattern.endsWith(ABSOLUTE_END) ? pattern.slice(0, -ABSOLUTE_END.length)
    : undefined;
  if (schema?.type !== "string"
      || !Object.keys(schema).every((key) => PATTERN_KEYS.has(key) || key.startsWith("x-"))
      || typeof pattern !== "string" || typeof body !== "string" || !pattern.startsWith("^")
      || !BOUNDED_WORD.test(body.slice(1)))
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
