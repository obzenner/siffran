// A throwaway on-disk `pi-subagents` package (manifest, entry file, preflight module) so owner
// resolution and owner-anchored imports run against the real file system without the registry.

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import type { InvocationProvenance } from "../src/contract.ts";
import { SUBAGENTS_COMPATIBILITY } from "../src/host-profile.ts";
import type { ToolInfo } from "../src/pi-types.ts";

export interface FixturePackage {
  /** Directory of the package (its ``package.json`` lives here). */
  readonly root: string;
  /** The extension entry file — what Pi reports as the tool's ``sourceInfo.path``. */
  readonly entry: string;
  /** The ``subagent`` ToolInfo as Pi's ``getAllTools()`` would report it. */
  tool(overrides?: Partial<ToolInfo["sourceInfo"]>): ToolInfo;
}

export interface FixtureOptions {
  readonly name?: string;
  /** ``null`` omits the field; a string is written verbatim (so invalid versions can be tested). */
  readonly version?: string | null;
  /** Preflight module source; default exports a sentinel-tagged ``resolveSubagentLaunchContract``. */
  readonly preflight?: string | null;
  readonly sentinel?: string;
}

/** Create a `pi-subagents`-shaped package under ``parent`` (defaults to a fresh temp dir). */
export function makeSubagentsPackage(parent: string, options: FixtureOptions = {}): FixturePackage {
  const root = path.join(parent, "pi-subagents");
  mkdirSync(path.join(root, "src", "api"), { recursive: true });
  const manifest: Record<string, unknown> = {
    name: options.name ?? "pi-subagents", type: "module",
    exports: { ".": "./index.ts", "./preflight": "./src/api/preflight.ts" },
  };
  if (options.version !== null) manifest.version = options.version ?? "0.74.0";
  writeFileSync(path.join(root, "package.json"), JSON.stringify(manifest));
  const entry = path.join(root, "index.ts");
  writeFileSync(entry, "export default function () {}\n");
  const sentinel = options.sentinel ?? "fixture";
  if (options.preflight !== null)
    writeFileSync(path.join(root, "src", "api", "preflight.ts"), options.preflight
      ?? `export const SENTINEL = ${JSON.stringify(sentinel)};\n`
        + "export async function resolveSubagentLaunchContract() {\n"
        + `  return { ok: false, message: ${JSON.stringify(`preflight:${sentinel}`)} };\n}\n`);
  return {
    root, entry,
    tool: (overrides = {}) => ({ name: "subagent",
      sourceInfo: { path: entry, source: "npm:pi-subagents", scope: "user", origin: "package", ...overrides } }),
  };
}

const created: string[] = [];
/** A fresh temp directory removed when the test process exits. */
export function tempParent(prefix = "empirica-owner-"): string {
  if (created.length === 0)
    process.on("exit", () => { for (const dir of created) rmSync(dir, { recursive: true, force: true }); });
  const dir = mkdtempSync(path.join(os.tmpdir(), prefix));
  created.push(dir);
  return dir;
}

let shared: FixturePackage | undefined;
/** The process-wide default owner used by FakePi unless a test replaces it. */
export function defaultSubagentsPackage(): FixturePackage {
  shared ??= makeSubagentsPackage(tempParent("empirica-owner-default-"));
  return shared;
}

/**
 * The StartRun invocation of a Pi session whose runtime the contract's policy accepts: the newest
 * reviewed version at lexical paths (the Python bridge checks containment, not existence).
 */
export function recordedInvocation(): InvocationProvenance & { host_runtime: NonNullable<InvocationProvenance["host_runtime"]> } {
  const root = "/opt/pi-subagents";
  const versions = SUBAGENTS_COMPATIBILITY.reviewed_versions;
  return { host: "pi", interactive: true, signal: "ctx.mode=tui", delegation: false,
    host_runtime: { policy_id: SUBAGENTS_COMPATIBILITY.policy_id, subagents: {
      package: SUBAGENTS_COMPATIBILITY.package, version: versions[versions.length - 1]!,
      owner_path: `${root}/src/extension/index.js`, package_root: root,
      preflight_path: `${root}/src/api/preflight.js`, source: `${root}/index.js` } } };
}
