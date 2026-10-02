#!/usr/bin/env node
// Generate the reviewed launch/action inventory of one installed pi-subagents package.
//
// The Pi adapter classifies every `subagent` tool call against the inventory of the exact version
// that owns the tool (plugins/empirica/adapters/pi/compat/pi-subagents-<version>.json). Those files
// are generated, never hand-typed, so a package update that changes its action or launch surface
// cannot pass unnoticed: `--check` (and the adapter's drift test) fails until the inventory is
// regenerated and the diff is reviewed.
//
// Sources, and why two of them are not the public export map:
//   * launch contract version  — observed: a real `pi-subagents/preflight` call (public export) for a
//     built-in agent; its `contract.version` is the version the package emits.
//   * launch forms, supports   — the package's own TypeBox parameter schema
//     (`src/extension/schemas`: `createSubagentParamsSchema`). PRIVATE: pi-subagents exports no schema.
//   * actions                  — the package's own dispatch list (`src/shared/types`:
//     `SUBAGENT_ACTIONS`, which `subagent-executor` validates `action` against; since 0.74 the schema
//     declares `action` as a free string). PRIVATE for the same reason.
//   * validateOffline          — the package documents `validate` as offline (`docs/tool-reference.md`).
// Nothing is read from the home directory: PI_CODING_AGENT_DIR points at a throwaway directory.
//
// Usage: node scripts/gen_pi_subagents_inventory.mjs --package-root DIR [--out-dir DIR | --check]
//   --package-root DIR  an installed pi-subagents directory (dependencies resolvable)
//   --out-dir DIR       write pi-subagents-<version>.json there (default: the adapter's compat/)
//   --check             compare with the checked-in file instead of writing; exit 1 on any drift
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { importExport, makeLoader, readPackage } from "./lib/pi_subagents_package.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const COMPAT_DIR = path.join(repo, "plugins", "empirica", "adapters", "pi", "compat");
/** Top-level `subagent` parameters that start an execution; whichever the schema declares is a launch form. */
const LAUNCH_FORM_CANDIDATES = ["agent", "workflowScript", "workflowScriptPath", "workflow", "resume"];

export function inventoryPath(dir, version) {
  return path.join(dir, `pi-subagents-${version}.json`);
}

/** Classify the `workflow` parameter's accepted types from its schema: absent | string | boolean|string. */
function workflowShape(property) {
  if (property === undefined) return "absent";
  const types = new Set();
  const visit = (node) => {
    if (node && typeof node === "object") {
      if (typeof node.type === "string") types.add(node.type);
      for (const branch of node.anyOf ?? node.oneOf ?? []) visit(branch);
    }
  };
  visit(property);
  const shape = [...types].sort().join("|");
  if (shape !== "string" && shape !== "boolean|string")
    throw new Error(`unrecognised workflow parameter shape: ${shape || "(no type)"}`);
  return shape;
}

async function observedLaunchContractVersion(packageRoot) {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "pi-subagents-inventory-"));
  const previous = { dir: process.env.PI_CODING_AGENT_DIR, offline: process.env.PI_OFFLINE };
  try {
    mkdirSync(path.join(scratch, "agent")); mkdirSync(path.join(scratch, "project"));
    process.env.PI_CODING_AGENT_DIR = path.join(scratch, "agent");
    process.env.PI_OFFLINE = "1";
    const preflight = await importExport(packageRoot, "./preflight");
    const result = await preflight.resolveSubagentLaunchContract({
      agent: "scout", context: "fresh", agentScope: "both", cwd: path.join(scratch, "project") });
    if (!result.ok) throw new Error(`built-in preflight refused: ${result.message}`);
    if (!Number.isInteger(result.contract.version)) throw new Error("preflight contract carries no integer version");
    return result.contract.version;
  } finally {
    for (const [key, value] of [["PI_CODING_AGENT_DIR", previous.dir], ["PI_OFFLINE", previous.offline]]) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    rmSync(scratch, { recursive: true, force: true });
  }
}

/** Does disabling the `tool-budgets` feature remove `toolBudget` from the schema? (false: no such feature) */
async function disabledFeaturesPrune(loader, root, createSchema) {
  const file = ["js", "ts"].map((ext) => path.join(root, "src", "shared", `disabled-features.${ext}`)).find(existsSync);
  if (file === undefined) return false;
  const module = await loader.import(file);
  const surface = module.resolveDisabledFeatureSurface({ disabledFeatures: ["tool-budgets"] });
  return !("toolBudget" in createSchema(surface).properties);
}

/** The tool-call parameter names the package's own schema declares (`createSubagentParamsSchema`). */
export async function subagentParamProperties(packageRoot) {
  const pkg = readPackage(packageRoot);
  const loader = makeLoader(path.join(pkg.root, "package.json"));
  const schemas = await loader.import(path.join(pkg.root, "src", "extension", "schemas"));
  return Object.keys(schemas.createSubagentParamsSchema().properties);
}

/** The inventory of the installed pi-subagents at `packageRoot`. */
export async function generateInventory(packageRoot) {
  const pkg = readPackage(packageRoot);
  const loader = makeLoader(path.join(pkg.root, "package.json"));
  const schemas = await loader.import(path.join(pkg.root, "src", "extension", "schemas"));
  const properties = schemas.createSubagentParamsSchema().properties;
  const types = await loader.import(path.join(pkg.root, "src", "shared", "types"));
  const docs = readFileSync(path.join(pkg.root, "docs", "tool-reference.md"), "utf8");
  if (!Array.isArray(types.SUBAGENT_ACTIONS) || !types.SUBAGENT_ACTIONS.every((a) => typeof a === "string"))
    throw new Error("SUBAGENT_ACTIONS is not a list of strings");
  return {
    version: pkg.version,
    launch_contract_version: await observedLaunchContractVersion(pkg.root),
    launch_forms: LAUNCH_FORM_CANDIDATES.filter((form) => form in properties),
    actions: [...types.SUBAGENT_ACTIONS],
    supports: {
      turnBudget: "turnBudget" in properties,
      toolBudget: "toolBudget" in properties,
      timeoutMs: "timeoutMs" in properties,
      workflow: workflowShape(properties.workflow),
      disabledFeatures: await disabledFeaturesPrune(loader, pkg.root, schemas.createSubagentParamsSchema),
      validateOffline: types.SUBAGENT_ACTIONS.includes("validate") && /Offline workflow `validate`/.test(docs),
    },
  };
}

export function serialiseInventory(inventory) {
  return `${JSON.stringify(inventory, null, 2)}\n`;
}

async function main(argv) {
  const opts = { check: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--check") { opts.check = true; continue; }
    if (!["--package-root", "--out-dir"].includes(argv[i]) || argv[i + 1] === undefined)
      throw new Error(`unknown or incomplete argument: ${argv[i]}`);
    opts[argv[i].slice(2).replace("-", "_")] = argv[i + 1];
    i += 1;
  }
  if (opts.package_root === undefined) throw new Error("--package-root DIR is required");
  if (opts.check && opts.out_dir !== undefined) throw new Error("--check and --out-dir are exclusive");
  const inventory = await generateInventory(opts.package_root);
  const target = inventoryPath(opts.out_dir ?? COMPAT_DIR, inventory.version);
  const fresh = serialiseInventory(inventory);
  if (opts.check) {
    if (!existsSync(target)) { console.error(`${target} is missing; generate it`); return 1; }
    if (readFileSync(target, "utf8") !== fresh) { console.error(`${target} drifted from ${opts.package_root}; regenerate and review`); return 1; }
    console.log(`${target} matches pi-subagents ${inventory.version}`);
    return 0;
  }
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, fresh);
  console.log(`wrote ${target}`);
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (error) => {
    console.error(String(error.message ?? error)); process.exitCode = 2;
  });
}
