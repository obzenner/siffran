// Schema-guarded, byte-identical TypeScript port of adapters/author_view.py.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

import { PROTOCOL, type Response } from "./contract.ts";
import { assertResponse } from "./guard.ts";

type Trusted = string & { readonly __trusted: unique symbol };
type Untrusted = string & { readonly __untrusted: unique symbol };
type Json = Record<string, any>;

interface TextSafety { readonly open: string; readonly close: string }
interface Header {
  readonly resultType: Trusted;
  readonly status: Trusted;
  readonly governance: Trusted;
  readonly runId: Trusted;
}
interface Reason {
  readonly code: Trusted;
  readonly message: Trusted;
  readonly affected: Trusted | null;
  readonly params: readonly Trusted[];
  readonly nextActions: readonly Trusted[];
}
interface Obligation {
  readonly obligationId: Trusted;
  readonly required: Trusted | Untrusted | null;
  readonly missing: Trusted | null;
  readonly nextActions: readonly Trusted[];
}
interface Child {
  readonly resourceClass: Trusted;
  readonly purpose: Trusted | Untrusted;
  readonly state: Trusted;
  readonly recovery: Trusted | null;
}
interface Freshness {
  readonly path: Untrusted;
  readonly state: Trusted;
}
interface AuditSummary {
  readonly state: Trusted;
  readonly independence: Trusted;
}
interface AuthorView {
  readonly header: Header;
  readonly audit: AuditSummary;
  readonly reasons: readonly Reason[];
  readonly obligations: readonly Obligation[];
  readonly satisfied: readonly Trusted[];
  readonly residuals: readonly Reason[];
  readonly children: readonly Child[];
  readonly freshness: readonly Freshness[];
  readonly nextActions: readonly Trusted[];
}

const CONTRACT_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..",
  "contracts", "empirica", "v2", "public-contract.json",
);
const PUBLIC_CONTRACT = JSON.parse(readFileSync(CONTRACT_PATH, "utf8")) as Json;
const RESPONSE_DEFS = (JSON.parse(readFileSync(
  path.join(path.dirname(CONTRACT_PATH), "response.schema.json"), "utf8",
)) as Json).$defs as Json;
const DIGEST_DEFS = new Set(["digest256", "nullableDigest256"]);
const CLAIM_ID_DEF = "claimId";
// The one claim-id pattern, read from the contract. ECMAScript `$` (no `m` flag) matches only at
// the end, so this is the same full match the Python renderer applies.
const CLAIM_ID = new RegExp(String((RESPONSE_DEFS[CLAIM_ID_DEF] as Json).pattern));
const SURFACES = Object.fromEntries(
  Object.entries(PUBLIC_CONTRACT.next_actions as Json).map(([id, row]) => [id, row.surface]),
) as Json;
const CEILINGS = Object.keys(PUBLIC_CONTRACT.governance_decisions.controls.budgets as Json)
  .map((ceiling) => [ceiling, `${ceiling.slice("max_".length)}_used`] as const);

/** Mark contract-owned text. */
function trusted(value: unknown): Trusted { return String(value) as Trusted; }

/** Escape and delimit one author-controlled value. */
function untrusted(value: unknown, safety: TextSafety): Untrusted {
  const escaped = String(value)
    .replace(/[\\\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]/gu,
      (char) => {
        const code = char.codePointAt(0) ?? 0;
        if (code === 92) return "\\\\";
        return code <= 255
          ? `\\x${code.toString(16).padStart(2, "0")}`
          : `\\u${code.toString(16).padStart(4, "0")}`;
      })
    .replaceAll("<", "\\x3c")
    .replaceAll(">", "\\x3e");
  return `${safety.open}${escaped}${safety.close}` as Untrusted;
}

/** Render a claim id raw; it is safe by construction only if it matches the pattern. */
function claimId(value: unknown, safety: TextSafety): Trusted | Untrusted {
  return typeof value === "string" && CLAIM_ID.test(value) ? trusted(value) : untrusted(value, safety);
}

/** Return recursively sorted compact JSON without throwing. */
function fallback(result: unknown): string {
  try {
    const seen = new WeakSet<object>();
    const serialize = (value: unknown): string => {
      if (value === null || value === undefined) return "null";
      if (typeof value === "string") return JSON.stringify(value);
      if (typeof value === "boolean") return value ? "true" : "false";
      if (typeof value === "number") {
        if (Number.isNaN(value)) return "NaN";
        if (value === Infinity) return "Infinity";
        if (value === -Infinity) return "-Infinity";
        return String(value);
      }
      if (typeof value !== "object") return "null";
      if (seen.has(value)) return "null";
      seen.add(value);
      try {
        if (Array.isArray(value)) return `[${value.map(serialize).join(",")}]`;
        const object = value as Record<string, unknown>;
        return `{${Object.keys(object).sort().map(
          (key) => `${JSON.stringify(key)}:${serialize(object[key])}`,
        ).join(",")}}`;
      } finally {
        seen.delete(value);
      }
    };
    return serialize(result);
  } catch {
    return "null";
  }
}

function objectWithKeys(value: unknown, keys: readonly string[]): value is Json {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

const text = (value: unknown, nonempty = false): value is string =>
  typeof value === "string" && (!nonempty || value.length > 0);
const unique = (value: unknown[]): boolean =>
  new Set(value.map((item) => JSON.stringify(item))).size === value.length;
const rows = (value: unknown, keys: readonly string[], fields: readonly string[]): boolean =>
  Array.isArray(value) && unique(value) && value.every((item) =>
    objectWithKeys(item, keys) && fields.every((field) => text(item[field])));

function validContractResult(value: unknown): boolean {
  if (!objectWithKeys(value, (value as Json | null)?.target === "index"
    ? ["target", "digest", "index"] : ["target", "digest", "section_id", "section"])) return false;
  if (!text(value.digest) || !/^sha256:[0-9a-f]{64}$/.test(value.digest)) return false;
  if (value.target === "index") {
    const index = value.index;
    return objectWithKeys(index, ["id", "version", "sections", "reasons", "next_actions"])
      && index.id === "empirica/public" && index.version === "3.0.0"
      && rows(index.sections, ["id", "title"], ["id", "title"])
      && Array.isArray(index.reasons) && unique(index.reasons) && index.reasons.every((reason) =>
        objectWithKeys(reason, ["code", "sections"]) && text(reason.code)
        && Array.isArray(reason.sections) && reason.sections.every((section) => text(section)))
      && rows(index.next_actions, ["id", "description"], ["id", "description"]);
  }
  if (value.target !== "section" || !text(value.section_id, true)) return false;
  const section = value.section;
  return objectWithKeys(section, ["id", "title", "summary", "clauses"])
    && text(section.id, true) && section.id === value.section_id && text(section.title, true)
    && text(section.summary) && rows(section.clauses, ["id", "text"], ["id", "text"])
    && section.clauses.every((clause: Json) => text(clause.id, true) && text(clause.text, true));
}

/** Validate with the same closed result shapes used by the public response schema. */
function validResult(result: unknown): boolean {
  if (objectWithKeys(result, ["type", "contract_result"]) && result.type === "Allow")
    return validContractResult(result.contract_result);
  try {
    assertResponse({ protocol: PROTOCOL, request_id: "author-view", result } as Response,
      "author-view");
    return true;
  } catch {
    return false;
  }
}

/** Render one contract-owned action surface. */
function surface(actionId: string): Trusted {
  const row = SURFACES[actionId] as Json;
  if (row.tool === "empirica_observe") return trusted(`empirica_observe kind=${row.action}`);
  if (row.tool === "empirica_read") return trusted(`empirica_read ${row.operation}`);
  if (row.tool === "report_convergence") return trusted(`report_convergence intent=${row.intent}`);
  return trusted(`${row.owner}: ${row.operation}`);
}

/** Render a contract id; an embedded claim id is raw only when its suffix is a claim id. */
function obligationId(value: string, safety: TextSafety): Trusted {
  return value.startsWith("claim:")
    ? trusted(`claim:${claimId(value.slice("claim:".length), safety)}`)
    : trusted(value);
}

type ValueRenderer = (value: any, safety: TextSafety) => string;

/**
 * Derive a value renderer from its response-schema shape; `null` drops digests.
 *
 * Ownership follows the schema: enums/consts are contract-owned (trusted), claim ids are safe by
 * construction (raw), free strings are author-supplied (untrusted), a nullable `anyOf` renders
 * its one non-null branch, and arrays and objects compose their item/field renderers.
 */
function valueRenderer(schema: Json): ValueRenderer | null {
  if (schema.$ref !== undefined) {
    const name = String(schema.$ref).split("/").pop() as string;
    if (name === CLAIM_ID_DEF) return (value, safety) => claimId(value, safety);
    return DIGEST_DEFS.has(name) ? null : valueRenderer(RESPONSE_DEFS[name]);
  }
  if (schema.anyOf !== undefined) {
    const branches = (schema.anyOf as Json[]).filter((row) => !isNull(row));
    if (branches.length !== 1)
      throw new Error(`author view cannot render parameter schema: ${JSON.stringify(schema)}`);
    const inner = valueRenderer(branches[0] as Json);
    if (inner === null) return null;
    return (value, safety) => (value === null ? "null" : inner(value, safety));
  }
  if (schema.enum !== undefined || schema.const !== undefined) return (value) => String(value);
  if (schema.type === "string") return (value, safety) => untrusted(value, safety);
  if (schema.type === "array") {
    const item = valueRenderer(schema.items) as ValueRenderer;
    const separator = isString(schema.items) ? ", " : "; ";
    return (value, safety) => (value as unknown[]).map((row) => item(row, safety)).join(separator);
  }
  if (schema.type === "object") {
    const fields = Object.entries(schema.properties as Json)
      .map(([name, sub]) => [name, valueRenderer(sub)] as const);
    return (value, safety) => fields
      .filter(([name, render]) => render !== null && name in value)
      .map(([name, render]) => (render as ValueRenderer)(value[name], safety)).join(": ");
  }
  throw new Error(`author view cannot render parameter schema: ${JSON.stringify(schema)}`);
}

/** Whether a schema is exactly the `{"type": "null"}` branch of a nullable `anyOf`. */
function isNull(schema: Json): boolean {
  return objectWithKeys(schema, ["type"]) && schema.type === "null";
}

/** Whether a schema (following refs) is a string, which joins with `", "` in arrays. */
function isString(schema: Json): boolean {
  if (schema.$ref !== undefined) return isString(RESPONSE_DEFS[String(schema.$ref).split("/").pop() as string]);
  return schema.type === "string";
}

/**
 * One renderer per reason/residual parameter key, derived from every `*Params` def.
 *
 * A key declared by two parameter shapes must have one schema, so its ownership is
 * unambiguous; a new contract parameter is rendered without a code change.
 */
function parameterRenderers(): ReadonlyMap<string, ValueRenderer | null> {
  const shapes = new Map<string, Json>();
  for (const name of Object.keys(RESPONSE_DEFS).sort()) {
    if (!name.endsWith("Params")) continue;
    for (const [key, schema] of Object.entries((RESPONSE_DEFS[name].properties ?? {}) as Json)) {
      const known = shapes.get(key);
      if (known !== undefined && JSON.stringify(known) !== JSON.stringify(schema))
        throw new Error(`parameter ${key} has conflicting schemas`);
      shapes.set(key, schema);
    }
  }
  return new Map([...shapes].map(([key, schema]) => [key, valueRenderer(schema)]));
}

const PARAMETERS = parameterRenderers();

/** Render reason/residual parameters with schema-derived ownership (digests dropped). */
function parameters(values: Json, safety: TextSafety): readonly Trusted[] {
  return Object.keys(values).sort().flatMap((key): Trusted[] => {
    if (!PARAMETERS.has(key)) throw new Error(`unknown author-view parameter: ${key}`);
    const render = PARAMETERS.get(key);
    return render ? [trusted(`${key}=${render(values[key], safety)}`)] : [];
  });
}

/** Render a reason's affected obligation (the schema's only author-relevant field). */
function affected(value: Json | null | undefined, safety: TextSafety): Trusted | null {
  if (!value || value.obligation_id === undefined) return null;
  return obligationId(value.obligation_id, safety);
}

/** Render the claim a residual blocks and, when redirected, the claim to discharge. */
function residualClaims(row: Json, safety: TextSafety): Trusted | null {
  if (row.claim_id === undefined) return null;
  const claim = `claim:${claimId(row.claim_id, safety)}`;
  const target = row.target_claim_id ?? row.claim_id;
  return trusted(target === row.claim_id ? claim : `${claim} via claim:${claimId(target, safety)}`);
}

/** Parse one schema-valid reason row. */
function reason(row: Json, safety: TextSafety): Reason {
  return {
    code: trusted(row.code), message: trusted(row.message), affected: affected(row.affected, safety),
    params: parameters(row.parameters, safety),
    nextActions: (row.next_actions as string[]).map(surface),
  };
}

/** Render the compact governance header summary. */
function governance(run: Json): Trusted {
  const value = run.governance as Json | null;
  if (value === null) return trusted("no governance");
  const parts = [`governance: ${value.state}`];
  if (value.proposal !== null && value.proposal !== undefined && value.budgets) {
    parts.push(`passes/spawns/audits: ${CEILINGS.map(
      ([ceiling, used]) => `${value.budgets[used]}/${value.proposal.budgets[ceiling]}`,
    ).join(" ")}`);
  }
  return trusted(parts.join("; "));
}

/** The host audit child's label is the contract literal `audit`; any other purpose is fenced. */
function childLabel(row: Json, safety: TextSafety): Trusted | Untrusted {
  return row.resource_class === "audit" && row.purpose === "audit"
    ? trusted(row.purpose) : untrusted(row.purpose, safety);
}

/** Parse one guarded run-bearing Allow or Block result. */
function parse(result: Json): AuthorView {
  const run = result.run as Json;
  const safety = run.untrusted_delimiters as TextSafety;
  let resultType = String(result.type);
  if (result.type === "Allow") resultType += ` (converged=${result.converged ? "true" : "false"})`;
  const header: Header = {
    resultType: trusted(resultType), status: trusted(run.status), governance: governance(run),
    runId: trusted(run.id),
  };
  const terminalNext = (run.next_actions as string[]).map(surface);
  const terminal = run.status !== "active";
  const active = run.obligations.active as Json[];
  const obligations = active.filter((row) => row.status !== "satisfied").map((row): Obligation => ({
    obligationId: obligationId(row.id, safety),
    required: row.required
      ? (String(row.id).startsWith("claim:") ? untrusted(row.required, safety) : trusted(row.required))
      : null,
    missing: row.missing === null ? null : trusted(row.missing.code),
    nextActions: (row.next as string[]).map(surface).concat(
      (row.next as string[]).length || !terminal ? [] : terminalNext,
    ),
  }));
  const satisfied = active.filter((row) => row.status === "satisfied")
    .map((row) => obligationId(row.id, safety));
  const residuals = (run.residuals as Json[]).map((row): Reason => ({
    code: trusted(row.code), message: trusted(""), affected: residualClaims(row, safety),
    params: parameters(row.parameters, safety),
    nextActions: (row.next_actions as string[]).map(surface),
  }));
  const children = (run.children as Json[]).map((row): Child => ({
    resourceClass: trusted(row.resource_class), purpose: childLabel(row, safety),
    state: trusted(row.state),
    recovery: row.recovery_action ? surface(row.recovery_action) : null,
  }));
  const freshness = (run.freshness.changes as Json[]).map((row): Freshness => ({
    path: untrusted(row.path, safety), state: trusted(row.state),
  }));
  return {
    header,
    audit: { state: trusted(run.audit.state), independence: trusted(run.audit.independence) },
    reasons: (result.reasons ?? []).map((row: Json) => reason(row, safety)),
    obligations, satisfied, residuals, children, freshness,
    nextActions: terminal ? [] : terminalNext,
  };
}

/** Render reasons or residuals without section framing. */
function reasonLines(rows: readonly Reason[]): readonly string[] {
  return rows.flatMap((row) => [
    `  ${row.code}${row.message ? `: ${row.message}` : ""}${row.affected ? ` — affected: ${row.affected}` : ""}`,
    ...(row.params.length ? [`    params: ${row.params.join("; ")}`] : []),
    ...(row.nextActions.length ? [`    next: ${row.nextActions.join("; ")}`] : []),
  ]);
}

/** Render open obligations without section framing. */
function obligationLines(rows: readonly Obligation[]): readonly string[] {
  return rows.flatMap((row) => [
    `  ${row.obligationId}${row.required ? `: ${row.required}` : ""}`,
    ...(row.missing ? [`    missing: ${row.missing}`] : []),
    ...(row.nextActions.length ? [`    next: ${row.nextActions.join("; ")}`] : []),
  ]);
}

/** Render child summaries without section framing. */
function childLines(rows: readonly Child[]): readonly string[] {
  return rows.map((row) =>
    `  ${row.purpose}: ${row.state}${row.recovery ? ` — recovery: ${row.recovery}` : ""}`);
}

/** Render freshness summaries without section framing. */
function freshnessLines(rows: readonly Freshness[]): readonly string[] {
  return rows.map((row) => `  ${row.path}: ${row.state}`);
}

/** Render independence only once an audit verdict exists. */
function auditLine(audit: AuditSummary): string {
  const suffix = ["passed", "failed"].includes(audit.state) ? ` (${audit.independence})` : "";
  return `Audit: ${audit.state}${suffix}`;
}

/** Render a parsed view through one ordered, empty-dropping section table. */
function render(view: AuthorView): string {
  const base = [
    `${view.header.resultType} ${view.header.status} — ${view.header.governance}`,
    `run_id: ${view.header.runId}`,
    auditLine(view.audit),
  ].join("\n");
  const sections: readonly [string, readonly string[]][] = [
    ["Reasons:", reasonLines(view.reasons)],
    ["Open obligations:", obligationLines(view.obligations)],
    ["Satisfied obligations:", view.satisfied.map((item) => `  ${item}`)],
    ["Residuals:", reasonLines(view.residuals)],
    ["Children:", childLines(view.children)],
    ["Freshness:", freshnessLines(view.freshness)],
    ["Next:", view.nextActions.map((item) => `  ${item}`)],
  ];
  return [base, ...sections.filter(([, lines]) => lines.length)
    .map(([title, lines]) => [title, ...lines].join("\n"))].join("\n\n");
}

/** Render the graph projection without private identity or artifact fields. */
function argumentLines(argument: Json, safety: TextSafety): readonly string[] {
  const claims = (argument.claims as Json[]).map((item) =>
    `  ${claimId(item.claim_id, safety)} kind=${item.kind} gating=${item.gating ? "true" : "false"} ` +
    `state=${item.state} evidence=${item.active_evidence_ids.length ? "present" : "none"}: ` +
    `${untrusted(item.text, safety)}`);
  const edges = (argument.edges as Json[]).map((item) =>
    `  ${claimId(item.from, safety)} -${item.type}-> ${claimId(item.to, safety)}`);
  const citations = (argument.artifacts as Json[]).filter((item) => item.citation !== undefined)
    .map((item) => `  ${untrusted(item.citation, safety)}`);
  return [
    `goal: ${untrusted(argument.goal, safety)}`,
    `root_claim_id: ${claimId(argument.root_claim_id, safety)}`,
    "Claims:", ...claims,
    ...(edges.length ? ["Edges:", ...edges] : []),
    ...(citations.length ? ["Citations:", ...citations] : []),
    `Audit status: ${argument.audit.state} (${argument.audit.independence})`,
  ];
}

/** Render trusted mappings and lists recursively as deterministic text. */
function trustedContractValue(value: unknown, indent = ""): readonly string[] {
  if (Array.isArray(value)) return value.flatMap((item) =>
    item !== null && typeof item === "object"
      ? [`${indent}-`, ...trustedContractValue(item, `${indent}  `)]
      : [`${indent}- ${String(item)}`]);
  if (value !== null && typeof value === "object")
    return Object.entries(value as Json).flatMap(([key, item]) =>
      item !== null && typeof item === "object"
        ? [`${indent}${key}:`, ...trustedContractValue(item, `${indent}  `)]
        : [`${indent}${key}: ${String(item)}`]);
  return [`${indent}${String(value)}`];
}

/** Render an index or section trusted contract projection. */
function contractLines(contract: Json): readonly string[] {
  if (contract.target === "index")
    return (contract.index.sections as Json[]).map((item) => `${item.id} — ${item.title}`);
  const section = contract.section as Json;
  return [`${section.id} — ${section.title}`,
    ...trustedContractValue({ summary: section.summary, clauses: section.clauses }, "  ")];
}

/** Render one guarded public result as text. */
function renderValid(row: Json): string {
  if (row.argument) {
    const argument = row.argument as Json;
    return `Argument\n${argumentLines(
      argument, argument.untrusted_delimiters as TextSafety,
    ).join("\n")}`;
  }
  if (row.contract_result)
    return `Contract\n${contractLines(row.contract_result as Json).join("\n")}`;
  if (["Allow", "Block"].includes(row.type) && row.run) return render(parse(row));
  if (row.type === "Block") {
    const safety = PUBLIC_CONTRACT.untrusted_delimiters as TextSafety;
    return `Block\n${reasonLines((row.reasons as Json[]).map((item) => reason(item, safety))).join("\n")}`;
  }
  if (row.type === "Fault")
    return `Fault ${row.code} (${row.fail_direction})${row.message === undefined ? "" : `: ${row.message}`}`;
  return "Inert";
}

/** Validate once, then render every valid public result as plain text. */
export function renderAuthorView(result: unknown, options: { strict?: boolean } = {}): string {
  if (!validResult(result)) return fallback(result);
  const row = result as Json;
  try {
    return renderValid(row);
  } catch (error) {
    if (options.strict) throw error;
    return fallback(result);
  }
}
