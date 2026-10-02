// Active pi-subagents owner resolution (Empirica 4.1, D2).
//
// Empirica does not bundle pi-subagents. The extension that registered the `subagent` tool in this
// Pi session is the only runtime whose preflight and result shapes Empirica may rely on, so the
// owner is observed from Pi's own tool inventory (`pi.getAllTools()[].sourceInfo`), never searched
// for: no `npm root`, no lockfile, no bare `import("pi-subagents/...")` that could bind a different
// installed copy than the one that registered the tool.
//
// Pi keeps only the first registrant of a tool name (`getAllTools()` cannot show a second
// `subagent`), so a second loaded copy of pi-subagents is invisible there. It is detected from
// `pi.getCommands()`, which Pi does not deduplicate: every loaded pi-subagents copy registers slash
// commands (`subagents`, `run`, `subagents-doctor`, ...) carrying its own extension `sourceInfo`.
//
// Everything here is a pure function over injected I/O. `resolveSubagentOwner` turns a tool
// inventory into a canonical owner identity or a typed refusal; `resolveOwnerPreflight` imports the
// preflight module anchored at that owner's package. Mapping refusals onto the public contract's
// reasons lives in owner-refusal.ts.

import { createRequire } from "node:module";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

import type { SlashCommandInfo, ToolInfo } from "./pi-types.ts";
import { SUBAGENT_TOOL } from "./translate.ts";

export const SUBAGENTS_PACKAGE = "pi-subagents";
/** Env flag pi-subagents sets in the Pi processes it spawns as children. */
export const SUBAGENT_CHILD_ENV = "PI_SUBAGENT_CHILD";
/** The specifier the owner's package exposes its launch preflight under. */
export const PREFLIGHT_SPECIFIER = `${SUBAGENTS_PACKAGE}/preflight`;

/** Semantic Version 2.0.0 (semver.org's recommended expression). */
const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$/;

export type OwnerRefusalCode =
  | "missing-tool"
  | "multiple-owners"
  | "owner-unobservable"
  | "package-not-pi-subagents"
  | "version-unobservable"
  | "child-process"
  | "preflight-unavailable"
  /** The session's cached owner no longer matches a fresh resolution (adapter-level invariant). */
  | "owner-changed";

export interface OwnerRefusal {
  readonly ok: false;
  readonly code: OwnerRefusalCode;
  /** Diagnostic detail for the operator; the author-facing guidance comes from the contract reason. */
  readonly message: string;
}

export interface SubagentOwner {
  /** Canonical (realpath) path of the file that registered the `subagent` tool. */
  readonly owner_path: string;
  /** Canonical directory of the owner's `pi-subagents` package. */
  readonly package_root: string;
  /** Exact `version` from the owner's package.json (semver-shaped). */
  readonly version: string;
  /** Pi's `sourceInfo.source` for the registering extension. */
  readonly source: string;
}

export type OwnerResolution = ({ readonly ok: true } & SubagentOwner) | OwnerRefusal;

/** File-system reads the resolver needs, injected so every failure is testable. */
export interface OwnerIo {
  /** Canonical path; throws when the path does not exist. */
  realpath(target: string): string;
  /** Parsed `<dir>/package.json`; `undefined` when the file does not exist; throws when unreadable or invalid JSON. */
  readPackageJson(dir: string): unknown;
}

export interface OwnerEnv {
  readonly PI_SUBAGENT_CHILD?: string;
}

const refuse = (code: OwnerRefusalCode, message: string): OwnerRefusal => ({ ok: false, code, message });

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** A real array whose every element is a record (deliberately not a type predicate: callers keep their declared type). */
function isRecordList(value: unknown): boolean {
  return Array.isArray(value) && value.every((item) => isRecord(item));
}

/**
 * The nearest `package.json` at or above directory `start`: its directory and parsed content, or
 * `undefined` when none exists up to the filesystem root. Throws what `io.readPackageJson` throws.
 */
function nearestManifest(start: string, io: Pick<OwnerIo, "readPackageJson">): { dir: string; manifest: unknown } | undefined {
  let dir = start;
  for (;;) {
    const manifest = io.readPackageJson(dir);
    if (manifest !== undefined) return { dir, manifest };
    const parent = path.dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

/**
 * Canonical roots of every `pi-subagents` package that contributed an extension slash command.
 * A command whose source is not an extension, is not file-backed (`<inline>` extensions), cannot be
 * canonicalised, or lies in no `pi-subagents` package is not evidence of a second runtime and is
 * ignored; only commands demonstrably inside a `pi-subagents` package count.
 */
function commandPackageRoots(commands: readonly SlashCommandInfo[], io: OwnerIo): Set<string> {
  const roots = new Set<string>();
  const byDirectory = new Map<string, string | null>();
  for (const command of commands) {
    if (command.source !== "extension") continue;
    const info: unknown = command.sourceInfo;
    if (!isRecord(info) || typeof info.path !== "string" || !path.isAbsolute(info.path)) continue;
    let root: string | null;
    try {
      const directory = path.dirname(io.realpath(info.path));
      if (!byDirectory.has(directory)) {
        const found = nearestManifest(directory, io);
        byDirectory.set(directory,
          found !== undefined && isRecord(found.manifest) && found.manifest.name === SUBAGENTS_PACKAGE ? found.dir : null);
      }
      root = byDirectory.get(directory) ?? null;
    } catch {
      continue;
    }
    if (root !== null) roots.add(root);
  }
  return roots;
}

/**
 * Resolve the single extension that registered the `subagent` tool.
 *
 * Refuses (never guesses) when: this is a pi-subagents child process; the tool or command inventory
 * is malformed; no, or more than one, `subagent` tool is registered (two registrations are refused
 * even from the same package — one active tool has one provenance owner); the registering file
 * cannot be canonicalised; the nearest enclosing package.json is unreadable, is not `pi-subagents`,
 * or has no semver-shaped `version`; or the slash commands show a `pi-subagents` package other than
 * the tool owner's (Pi hides a second `subagent` registration, not a second copy's commands).
 * `sourceInfo.baseDir` is deliberately not consulted: the package root is derived from the canonical
 * file path alone, so a hint can never widen what is accepted.
 */
export function resolveSubagentOwner(
  tools: readonly ToolInfo[], commands: readonly SlashCommandInfo[], io: OwnerIo, env: OwnerEnv,
): OwnerResolution {
  if (env.PI_SUBAGENT_CHILD === "1")
    return refuse("child-process", `${SUBAGENT_CHILD_ENV}=1: this is a pi-subagents child process, not a parent session`);
  if (!isRecordList(tools))
    return refuse("owner-unobservable", "the tool inventory is not a list of tool records");
  if (!isRecordList(commands))
    return refuse("owner-unobservable", "the slash-command inventory is not a list of command records");
  const owners = tools.filter((tool) => tool.name === SUBAGENT_TOOL);
  if (owners.length === 0) return refuse("missing-tool", `no \`${SUBAGENT_TOOL}\` tool is registered`);
  if (owners.length > 1)
    return refuse("multiple-owners", `${owners.length} \`${SUBAGENT_TOOL}\` tools are registered`);
  const info = owners[0].sourceInfo;
  if (!isRecord(info) || typeof info.path !== "string" || info.path === "" || !path.isAbsolute(info.path))
    return refuse("owner-unobservable", "the `subagent` tool has no absolute source path");
  if (typeof info.source !== "string" || info.source === "")
    return refuse("owner-unobservable", "the `subagent` tool has no source identity");

  let ownerPath: string;
  try {
    ownerPath = io.realpath(info.path);
  } catch (error) {
    return refuse("owner-unobservable", `cannot canonicalise ${info.path}: ${describeError(error)}`);
  }

  // The nearest enclosing package.json must be pi-subagents; a nested package of another name is a
  // different owner, not a reason to keep climbing.
  let found: { dir: string; manifest: unknown } | undefined;
  try {
    found = nearestManifest(path.dirname(ownerPath), io);
  } catch (error) {
    return refuse("owner-unobservable", `cannot read a package.json enclosing ${ownerPath}: ${describeError(error)}`);
  }
  if (found === undefined) return refuse("owner-unobservable", `no package.json encloses ${ownerPath}`);
  const { dir, manifest } = found;
  if (!isRecord(manifest) || typeof manifest.name !== "string")
    return refuse("package-not-pi-subagents", `${path.join(dir, "package.json")} has no package name`);
  if (manifest.name !== SUBAGENTS_PACKAGE)
    return refuse("package-not-pi-subagents",
      `the package owning ${ownerPath} is ${JSON.stringify(manifest.name)}, not ${SUBAGENTS_PACKAGE}`);
  if (typeof manifest.version !== "string" || !SEMVER.test(manifest.version))
    return refuse("version-unobservable",
      `${path.join(dir, "package.json")} has no semver-shaped version (${JSON.stringify(manifest.version)})`);

  const roots = commandPackageRoots(commands, io);
  roots.add(dir);
  if (roots.size > 1)
    return refuse("multiple-owners",
      `${roots.size} distinct ${SUBAGENTS_PACKAGE} packages are loaded: ${[...roots].sort().join(", ")}`);
  return { ok: true, owner_path: ownerPath, package_root: dir, version: manifest.version, source: info.source };
}

/** True when two successful resolutions name the same owner identity (every field, exactly). */
export function sameOwner(a: SubagentOwner, b: SubagentOwner): boolean {
  return a.owner_path === b.owner_path && a.package_root === b.package_root
    && a.version === b.version && a.source === b.source;
}

/** The slice of pi-subagents' `preflight` module Empirica consumes. */
export interface AuditorPreflightApi {
  resolveSubagentLaunchContract(input: Record<string, unknown>): Promise<
    { ok: true; contract: { agent: { filePath: string }; model?: string; modelCandidates: string[] } }
    | { ok: false; message: string }
  >;
}

/** Module resolution and loading, injected so the binding can be proven with sentinel packages. */
export interface PreflightImporter {
  /** Resolve `specifier` as if required from the file `fromFile`; throws when it does not resolve. */
  resolve(specifier: string, fromFile: string): string;
  /** Load the module at an absolute file path. */
  load(file: string): Promise<unknown>;
}

export interface BoundPreflight {
  readonly ok: true;
  /** Canonical path of the preflight module that was loaded (inside `package_root`). */
  readonly preflight_path: string;
  readonly api: AuditorPreflightApi;
}

/**
 * Load `pi-subagents/preflight` from the package that owns the registered tool — resolution is
 * anchored at the owner's file, never at Empirica's. Refuses `preflight-unavailable` when the owner
 * does not resolve the specifier, resolves it into any package other than its own (the nearest
 * `package.json` above the resolved file must be the owner's `package_root`, so a nested
 * `node_modules/pi-subagents` copy is refused), or the module lacks a callable
 * `resolveSubagentLaunchContract`.
 */
export async function resolveOwnerPreflight(
  owner: SubagentOwner, importer: PreflightImporter, io: Pick<OwnerIo, "realpath" | "readPackageJson">,
): Promise<BoundPreflight | OwnerRefusal> {
  let resolved: string;
  try {
    resolved = io.realpath(importer.resolve(PREFLIGHT_SPECIFIER, owner.owner_path));
  } catch (error) {
    return refuse("preflight-unavailable", `${PREFLIGHT_SPECIFIER} does not resolve from ${owner.owner_path}: ${describeError(error)}`);
  }
  const relative = path.relative(owner.package_root, resolved);
  if (relative === "" || relative.startsWith("..") || path.isAbsolute(relative))
    return refuse("preflight-unavailable", `${resolved} is outside the owner package ${owner.package_root}`);
  let enclosing: { dir: string; manifest: unknown } | undefined;
  try {
    enclosing = nearestManifest(path.dirname(resolved), io);
  } catch (error) {
    return refuse("preflight-unavailable", `cannot read the package.json enclosing ${resolved}: ${describeError(error)}`);
  }
  if (enclosing === undefined || enclosing.dir !== owner.package_root)
    return refuse("preflight-unavailable",
      `${resolved} belongs to ${enclosing?.dir ?? "no package"}, not the owner package ${owner.package_root}`);
  let loaded: unknown;
  try {
    loaded = await importer.load(resolved);
  } catch (error) {
    return refuse("preflight-unavailable", `${resolved} failed to load: ${describeError(error)}`);
  }
  if (!isRecord(loaded) || typeof loaded.resolveSubagentLaunchContract !== "function")
    return refuse("preflight-unavailable", `${resolved} does not export a callable resolveSubagentLaunchContract`);
  return { ok: true, preflight_path: resolved, api: loaded as unknown as AuditorPreflightApi };
}

/** The adapter-owned observed-runtime object recorded beside the host profile (closed; one field each). */
export interface SubagentsProvenance {
  readonly package: typeof SUBAGENTS_PACKAGE;
  readonly version: string;
  readonly owner_path: string;
  readonly package_root: string;
  readonly preflight_path: string;
  readonly source: string;
}

/** The provenance of a bound owner, as observed — nothing configured or assumed. */
export function subagentsProvenance(owner: SubagentOwner, preflight: Pick<BoundPreflight, "preflight_path">): SubagentsProvenance {
  return {
    package: SUBAGENTS_PACKAGE, version: owner.version, owner_path: owner.owner_path,
    package_root: owner.package_root, preflight_path: preflight.preflight_path, source: owner.source,
  };
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Production file-system reads for {@link resolveSubagentOwner}. */
export const nodeOwnerIo: OwnerIo = {
  realpath: (target) => realpathSync(target),
  readPackageJson(dir) {
    const file = path.join(dir, "package.json");
    try {
      lstatSync(file);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT" || (error as NodeJS.ErrnoException).code === "ENOTDIR")
        return undefined;
      throw error;
    }
    return JSON.parse(readFileSync(file, "utf8"));
  },
};

/** Production module resolution: `require.resolve` anchored at the owner file, then a dynamic import. */
export const nodePreflightImporter: PreflightImporter = {
  resolve: (specifier, fromFile) => createRequire(fromFile).resolve(specifier),
  load: (file) => import(pathToFileURL(file).href),
};
