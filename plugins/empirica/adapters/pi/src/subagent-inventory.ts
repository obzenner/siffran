// The launch/action inventory of one pi-subagents version (Empirica 4.1, D4; ADR-0069).
//
// A reviewed inventory is generated — `scripts/gen_pi_subagents_inventory.mjs` reads a package's own
// parameter schema and action dispatch list — and checked in under `compat/`, one file per exact
// version. A version inside the profile's range with no such file is admitted by observation (`observeInventory`);
// the admission rule is owned by ADR-0069 and HX-ADM-02 and is not restated here. Its action list is the union of
// the reviewed versions' (`reviewedActionNames`) and its launch-contract version comes from the live
// preflight. This module validates, loads and observes; it never classifies a call.

import { readdirSync, readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { EXACT_VERSION } from "./host-profile.ts";

/** The top-level `subagent` parameters that start an execution, across every reviewed version. */
export const LAUNCH_FORMS = ["agent", "workflowScript", "workflowScriptPath", "workflow", "resume"] as const;
export type LaunchForm = typeof LAUNCH_FORMS[number];

/** What the adapter consumes of any inventory, reviewed or observed. */
interface InventoryFacts {
  readonly version: string;
  readonly launch_forms: readonly LaunchForm[];
  readonly actions: readonly string[];
  readonly supports: {
    readonly toolBudget: boolean;
    readonly timeoutMs: boolean;
    /** `validate` is documented as offline (statically checks a workflow without launching children). */
    readonly validateOffline: boolean;
  };
}

/** A checked-in compat record: everything the review recorded. */
export interface ReviewedInventory extends InventoryFacts {
  readonly reviewed: true;
  /** The package's declared `peerDependencies["@earendil-works/pi-ai"]`: the Pi generation it needs. */
  readonly peer_pi_ai: string;
  /** The `contract.version` this package's launch preflight emits (observed, not assumed). */
  readonly launch_contract_version: number;
  readonly supports: InventoryFacts["supports"] & {
    readonly turnBudget: boolean;
    readonly workflow: "absent" | "string" | "boolean|string";
    readonly disabledFeatures: boolean;
  };
}

/** What an in-range version with no record gives: the facts the adapter consumes. The two bound flags are declared by
 * its schema (not observed to be enforced) and `validateOffline` is false because nothing documents it as offline. */
export interface ObservedInventory extends InventoryFacts {
  readonly reviewed: false;
  /** Unknown until the live preflight answers; the seam accepts any version it knows. */
  readonly launch_contract_version: null;
}

export type SubagentInventory = ReviewedInventory | ObservedInventory;

/** Directory of the checked-in inventories (inside the plugin, so an installed copy carries them). */
export const COMPAT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "compat");


function record(value: unknown, where: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new Error(`pi-subagents inventory: ${where} must be an object`);
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], where: string): void {
  for (const key of Object.keys(value))
    if (!keys.includes(key)) throw new Error(`pi-subagents inventory: ${where} has unknown field ${JSON.stringify(key)}`);
  for (const key of keys)
    if (!(key in value)) throw new Error(`pi-subagents inventory: ${where} is missing ${JSON.stringify(key)}`);
}

function stringList(value: unknown, where: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string" && item !== "")
      || new Set(value).size !== value.length)
    throw new Error(`pi-subagents inventory: ${where} must be a list of unique nonempty strings`);
  return value as string[];
}

/** Validate a parsed inventory document (closed shape); throws on the first deviation. */
export function parseInventory(document: unknown): Omit<ReviewedInventory, "reviewed"> {
  const doc = record(document, "document");
  exactKeys(doc, ["version", "peer_pi_ai", "launch_contract_version", "launch_forms", "actions", "supports"], "document");
  if (typeof doc.version !== "string" || !EXACT_VERSION.test(doc.version))
    throw new Error("pi-subagents inventory: version must be an exact MAJOR.MINOR.PATCH release");
  if (typeof doc.peer_pi_ai !== "string" || doc.peer_pi_ai === "")
    throw new Error("pi-subagents inventory: peer_pi_ai must be the package's nonempty declared pi-ai peer range");
  if (typeof doc.launch_contract_version !== "number" || !Number.isInteger(doc.launch_contract_version)
      || doc.launch_contract_version < 1)
    throw new Error("pi-subagents inventory: launch_contract_version must be a positive integer");
  const forms = stringList(doc.launch_forms, "launch_forms");
  const unknownForm = forms.find((form) => !(LAUNCH_FORMS as readonly string[]).includes(form));
  if (unknownForm !== undefined || !forms.includes("agent"))
    throw new Error(`pi-subagents inventory: launch_forms must be drawn from ${LAUNCH_FORMS.join(", ")} and include agent`
      + (unknownForm === undefined ? "" : ` (got ${JSON.stringify(unknownForm)})`));
  const actions = stringList(doc.actions, "actions");
  const supports = record(doc.supports, "supports");
  exactKeys(supports, ["turnBudget", "toolBudget", "timeoutMs", "workflow", "disabledFeatures", "validateOffline"], "supports");
  for (const flag of ["turnBudget", "toolBudget", "timeoutMs", "disabledFeatures", "validateOffline"])
    if (typeof supports[flag] !== "boolean") throw new Error(`pi-subagents inventory: supports.${flag} must be a boolean`);
  if (!["absent", "string", "boolean|string"].includes(supports.workflow as string))
    throw new Error("pi-subagents inventory: supports.workflow must be absent, string, or boolean|string");
  if (supports.validateOffline === true && !actions.includes("validate"))
    throw new Error("pi-subagents inventory: validateOffline requires the validate action");
  return doc as unknown as Omit<ReviewedInventory, "reviewed">;
}

/**
 * The `subagent` parameters the audit launch sends (audit-launch.ts). A
 * package whose own schema lacks one of them cannot carry the launch, so it is not admitted.
 */
export const AUDIT_LAUNCH_PARAMETERS = [
  "agent", "task", "model", "agentScope", "async", "context", "acceptance", "timeoutMs", "toolBudget",
] as const;

/**
 * Admit a version that has no compat record from what the owner's tool declares. Returns the observed
 * inventory (its actions are `knownActions`, the names some reviewed version has), or the name of the first
 * audit-launch parameter the tool's schema does not declare.
 */
export function observeInventory(version: string, parameters: unknown, knownActions: readonly string[]):
  { readonly ok: true; readonly inventory: ObservedInventory } | { readonly ok: false; readonly detail: string } {
  const properties = parameters !== null && typeof parameters === "object"
    ? (parameters as { properties?: unknown }).properties : undefined;
  if (properties === null || typeof properties !== "object" || Array.isArray(properties))
    return { ok: false, detail: "the subagent tool declares no parameter schema" };
  const declared = properties as Record<string, unknown>;
  const missing = AUDIT_LAUNCH_PARAMETERS.find((name) => !(name in declared));
  if (missing !== undefined) return { ok: false, detail: `the subagent tool's schema does not declare \`${missing}\`` };
  return { ok: true, inventory: {
    reviewed: false, version, launch_contract_version: null,
    launch_forms: LAUNCH_FORMS.filter((form) => form in declared), actions: [...knownActions],
    // The schema declares both bound fields (AUDIT_LAUNCH_PARAMETERS); `validate` is never taken as offline.
    supports: { toolBudget: true, timeoutMs: true, validateOffline: false },
  } };
}

/** File reads for inventory loading, injected so absence and corruption are testable. */
export interface InventoryIo {
  /** File content, or `undefined` when the file does not exist; throws on any other failure. */
  readText(file: string): string | undefined;
}

export const nodeInventoryIo: InventoryIo = {
  readText(file) {
    try {
      return readFileSync(file, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
      throw error;
    }
  },
};

/**
 * The inventory reviewed for exactly `version`, or `undefined` when none is checked in (including
 * for any version that is not a plain `MAJOR.MINOR.PATCH` release). A present but malformed file,
 * or one that names a different version, is a defect and throws.
 */
export function loadInventory(version: string, dir: string = COMPAT_DIR, io: InventoryIo = nodeInventoryIo): ReviewedInventory | undefined {
  if (!EXACT_VERSION.test(version)) return undefined;
  const file = path.join(dir, `pi-subagents-${version}.json`);
  const text = io.readText(file);
  if (text === undefined) return undefined;
  const inventory = parseInventory(JSON.parse(text));
  if (inventory.version !== version)
    throw new Error(`pi-subagents inventory: ${file} describes ${inventory.version}, not ${version}`);
  return { reviewed: true as const, ...inventory };
}

/** The union of the actions of every checked-in inventory, sorted: the action names some reviewed version has. */
export function reviewedActionNames(dir: string = COMPAT_DIR): string[] {
  const names = new Set<string>();
  for (const file of readdirSync(dir).filter((name) => /^pi-subagents-\d+\.\d+\.\d+\.json$/.test(name)))
    for (const action of loadInventory(file.slice("pi-subagents-".length, -".json".length), dir)?.actions ?? [])
      names.add(action);
  return [...names].sort();
}
