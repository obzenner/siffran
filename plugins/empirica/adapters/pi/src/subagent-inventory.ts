// The reviewed launch/action inventory of one pi-subagents version (Empirica 4.1, D4).
//
// Inventories are generated — `scripts/gen_pi_subagents_inventory.mjs` reads a package's own
// parameter schema and action dispatch list — and checked in under `compat/`, one file per exact
// version. The adapter selects the inventory of the owner's exact version and refuses an owner with
// none, so a pi-subagents release whose surface nobody has reviewed cannot be classified by
// analogy with another. This module only validates and loads those files.

import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

/** The top-level `subagent` parameters that start an execution, across every reviewed version. */
export const LAUNCH_FORMS = ["agent", "workflowScript", "workflowScriptPath", "workflow", "resume"] as const;
export type LaunchForm = typeof LAUNCH_FORMS[number];

export interface SubagentInventory {
  readonly version: string;
  /** The `contract.version` this package's launch preflight emits (observed, not assumed). */
  readonly launch_contract_version: number;
  readonly launch_forms: readonly LaunchForm[];
  readonly actions: readonly string[];
  readonly supports: {
    readonly turnBudget: boolean;
    readonly toolBudget: boolean;
    readonly timeoutMs: boolean;
    readonly workflow: "absent" | "string" | "boolean|string";
    readonly disabledFeatures: boolean;
    /** `validate` is documented as offline (statically checks a workflow without launching children). */
    readonly validateOffline: boolean;
  };
}

/** Directory of the checked-in inventories (inside the plugin, so an installed copy carries them). */
export const COMPAT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "compat");

const EXACT_VERSION = /^\d+\.\d+\.\d+$/;

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
export function parseInventory(document: unknown): SubagentInventory {
  const doc = record(document, "document");
  exactKeys(doc, ["version", "launch_contract_version", "launch_forms", "actions", "supports"], "document");
  if (typeof doc.version !== "string" || !EXACT_VERSION.test(doc.version))
    throw new Error("pi-subagents inventory: version must be an exact MAJOR.MINOR.PATCH release");
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
  return doc as unknown as SubagentInventory;
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
export function loadInventory(version: string, dir: string = COMPAT_DIR, io: InventoryIo = nodeInventoryIo): SubagentInventory | undefined {
  if (!EXACT_VERSION.test(version)) return undefined;
  const file = path.join(dir, `pi-subagents-${version}.json`);
  const text = io.readText(file);
  if (text === undefined) return undefined;
  const inventory = parseInventory(JSON.parse(text));
  if (inventory.version !== version)
    throw new Error(`pi-subagents inventory: ${file} describes ${inventory.version}, not ${version}`);
  return inventory;
}
