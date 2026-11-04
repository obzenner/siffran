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
//   * launch_parameter_placement — only for a package that publishes the compact schema (0.77.0 and later: the
//     top level lists `agent, task, workflow, args, async, model, cwd, worktree, output, action, id, message` and
//     every other field goes in one `options` object): where each audit-launch parameter is declared, read from the
//     package's own `SUBAGENT_OPTION_KEYS` (same module, validated by `SubagentOptionParams`) and the top-level
//     properties. A flat package has no such export and its record carries no such field, so its output is unchanged.
//   * actions                  — the package's own dispatch list (`src/shared/types`:
//     `SUBAGENT_ACTIONS`, which `subagent-executor` validates `action` against; since 0.74 the schema
//     declares `action` as a free string). PRIVATE for the same reason.
//   * validateOffline          — the package documents `validate` as offline (`docs/tool-reference.md`).
//   * peer_pi_ai               — the package's own `peerDependencies["@earendil-works/pi-ai"]`: the Pi
//     generation it declares it needs. doc/hosts.md's "Requires Pi" column is derived from it (checked by
//     scripts/validate_empirica_activation.py), so the pairing claim is data, not prose.
// The operator's home directory is never read: the preflight runs in a throwaway project with
// PI_CODING_AGENT_DIR, HOME and USERPROFILE all pointing into the same scratch directory (the
// package resolves `~/.agents` and `~/.pi` from the home directory, and walks the project's ancestors).
//
// Usage: node scripts/gen_pi_subagents_inventory.mjs --package-root DIR [--out-dir DIR | --check]
//   --package-root DIR  an installed pi-subagents directory (dependencies resolvable)
//   --out-dir DIR       write pi-subagents-<version>.json there (default: the adapter's compat/)
//   --check             compare with the checked-in file instead of writing; exit 1 on any drift
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { importExport, importInternal, makeLoader, readPackage } from "./lib/pi_subagents_package.mjs";
import { AUDIT_LAUNCH_PARAMETERS, CORE_AUDIT_PARAMETERS, LAUNCH_FORMS } from "../plugins/empirica/adapters/pi/src/subagent-inventory.ts";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const COMPAT_DIR = path.join(repo, "plugins", "empirica", "adapters", "pi", "compat");
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

const PI_AI_PEER = "@earendil-works/pi-ai";

/** Environment variables through which the package (or `os.homedir()`) could reach the operator's home. */
const ISOLATED_ENV = ["PI_CODING_AGENT_DIR", "PI_OFFLINE", "HOME", "USERPROFILE"];

async function observedLaunchContractVersion(packageRoot) {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "pi-subagents-inventory-"));
  const previous = Object.fromEntries(ISOLATED_ENV.map((key) => [key, process.env[key]]));
  try {
    for (const dir of ["agent", "project", "home"]) mkdirSync(path.join(scratch, dir));
    process.env.PI_CODING_AGENT_DIR = path.join(scratch, "agent");
    process.env.PI_OFFLINE = "1";
    process.env.HOME = process.env.USERPROFILE = path.join(scratch, "home");
    const preflight = await importExport(packageRoot, "./preflight");
    const result = await preflight.resolveSubagentLaunchContract({
      agent: "scout", context: "fresh", agentScope: "both", cwd: path.join(scratch, "project") });
    if (!result.ok) throw new Error(`built-in preflight refused: ${result.message}`);
    if (!Number.isInteger(result.contract.version)) throw new Error("preflight contract carries no integer version");
    return result.contract.version;
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    rmSync(scratch, { recursive: true, force: true });
  }
}

/**
 * Does disabling the `tool-budgets` feature take `toolBudget` off the schema's enabled surface? A flat package drops it from
 * the top-level properties; a compact one (`optionKeys` given) refuses it as an option, which its feature surface records.
 */
async function disabledFeaturesPrune(loader, pkg, createSchema, optionKeys) {
  const module = await importInternal(loader, pkg, "src/shared/disabled-features", ["resolveDisabledFeatureSurface"]);
  if (module === undefined) return false;
  const surface = module.resolveDisabledFeatureSurface({ disabledFeatures: ["tool-budgets"] });
  if (optionKeys !== undefined) return optionKeys.includes("toolBudget") && surface.params.has("toolBudget");
  return !("toolBudget" in createSchema(surface).properties);
}

/** The tool-call parameter names the package's own schema declares (`createSubagentParamsSchema`). */
export async function subagentParamProperties(packageRoot) {
  const { schemas } = await loadSchemas(packageRoot);
  return Object.keys(schemas.createSubagentParamsSchema().properties);
}

/** The package's schema module as the package runs it (`src/extension/schemas`, or the bundle chunk that exports it). */
async function loadSchemas(packageRoot) {
  const pkg = readPackage(packageRoot);
  const loader = makeLoader(path.join(pkg.root, "package.json"));
  const schemas = await importInternal(loader, pkg, "src/extension/schemas", ["createSubagentParamsSchema", "SUBAGENT_OPTION_KEYS"]);
  if (schemas === undefined) throw new Error(`${pkg.root} has no src/extension/schemas`);
  return { pkg, loader, schemas };
}

/**
 * The keys the package accepts inside the `options` object of a `subagent` call (its own `SUBAGENT_OPTION_KEYS`), or
 * `undefined` for a flat package, which has no such export.
 */
export async function subagentOptionKeys(packageRoot) {
  const { schemas } = await loadSchemas(packageRoot);
  if (schemas.SUBAGENT_OPTION_KEYS === undefined) return undefined;
  if (!Array.isArray(schemas.SUBAGENT_OPTION_KEYS) || !schemas.SUBAGENT_OPTION_KEYS.every((key) => typeof key === "string"))
    throw new Error("SUBAGENT_OPTION_KEYS is not a list of strings");
  return [...schemas.SUBAGENT_OPTION_KEYS];
}

/**
 * Where each audit-launch parameter is declared: `top-level` when the schema's top-level properties list it, otherwise
 * `options` when the package's option keys do. `undefined` for a flat package (no option keys). A parameter declared in
 * neither place, a core one (`agent, task, model, async`) that is not top-level, or option keys without a top-level
 * `options` object, throws: the package cannot carry the launch.
 */
export function auditLaunchPlacement(topLevel, optionKeys) {
  if (optionKeys === undefined) return undefined;
  const placement = {};
  for (const name of AUDIT_LAUNCH_PARAMETERS) {
    if (topLevel.includes(name)) placement[name] = "top-level";
    else if (CORE_AUDIT_PARAMETERS.includes(name)) throw new Error(`the core audit-launch parameter ${name} is not a top-level property`);
    else if (optionKeys.includes(name)) placement[name] = "options";
    else throw new Error(`the audit-launch parameter ${name} is declared neither at the top level nor in the package's option keys`);
  }
  if (Object.values(placement).includes("options") && !topLevel.includes("options"))
    throw new Error("the package carries audit-launch parameters in options but declares no top-level options object");
  return placement;
}

/** The package's declared peer range on the Pi model layer; a package that declares none cannot be reviewed as data. */
export function peerPiAi(manifest) {
  const range = manifest.peerDependencies?.[PI_AI_PEER];
  if (typeof range !== "string" || range === "")
    throw new Error(`pi-subagents ${manifest.version} declares no peerDependencies["${PI_AI_PEER}"]; record its Pi requirement by hand first`);
  return range;
}

/** The inventory of the installed pi-subagents at `packageRoot`. */
export async function generateInventory(packageRoot) {
  const { pkg, loader, schemas } = await loadSchemas(packageRoot);
  const schema = schemas.createSubagentParamsSchema();
  const properties = schema.properties;
  const optionKeys = await subagentOptionKeys(pkg.root);
  const placement = auditLaunchPlacement(Object.keys(properties), optionKeys);
  if (placement !== undefined && properties.options?.type !== "object")
    throw new Error("the package's top-level options parameter is not an object schema");
  // What the schema declares for a call: the top-level properties, and for a compact package the keys of `options` too.
  const declared = new Set([...Object.keys(properties), ...(optionKeys ?? [])]);
  const types = await importInternal(loader, pkg, "src/shared/types", ["SUBAGENT_ACTIONS"]);
  if (types === undefined) throw new Error(`${pkg.root} has no src/shared/types`);
  const docs = readFileSync(path.join(pkg.root, "docs", "tool-reference.md"), "utf8");
  if (!Array.isArray(types.SUBAGENT_ACTIONS) || !types.SUBAGENT_ACTIONS.every((a) => typeof a === "string"))
    throw new Error("SUBAGENT_ACTIONS is not a list of strings");
  return {
    version: pkg.version,
    peer_pi_ai: peerPiAi(pkg.manifest),
    launch_contract_version: await observedLaunchContractVersion(pkg.root),
    // Top-level parameters that start an execution; whichever the schema declares is a launch form.
    launch_forms: LAUNCH_FORMS.filter((form) => form in properties),
    ...(placement === undefined ? {} : { launch_parameter_placement: placement }),
    actions: [...types.SUBAGENT_ACTIONS],
    supports: {
      turnBudget: declared.has("turnBudget"),
      toolBudget: declared.has("toolBudget"),
      timeoutMs: declared.has("timeoutMs"),
      workflow: workflowShape(properties.workflow),
      disabledFeatures: await disabledFeaturesPrune(loader, pkg, schemas.createSubagentParamsSchema, optionKeys),
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
