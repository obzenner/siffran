#!/usr/bin/env node
// Read-only probe: which file does pi-subagents actually resolve for the Empirica auditor?
//
// Why this exists: the Pi adapter blocks an audit when the resolved auditor file is not the
// package's own file ("empirica auditor package identity was shadowed"). pi-subagents merges
// package agents into a name-keyed map and user-level packages (for example a globally installed
// siffran) can shadow a dogfooded checkout. This probe reports the runtime's own effective
// resolution per agent scope, so the condition is visible before a native qualification instead of
// at audit launch.
//
// It asks the package's PUBLIC launch preflight (`pi-subagents/preflight`), loaded from an explicit
// package root, never a private discovery module. The Pi settings file is an explicit input too:
// the default is `$PI_CODING_AGENT_DIR/settings.json`, else `~/.pi/agent/settings.json`, resolved
// in one place (`defaultSettingsPath`) and only when no `--settings` is given; tests always pass it.
// It never launches a child, writes artifacts, or edits settings (pi-subagents may run `npm root -g`
// unless PI_OFFLINE=1).
//
// Usage: node scripts/pi_auditor_resolution.mjs [--project DIR] [--agent NAME] [--expected FILE]
//            [--package-root DIR] [--settings FILE] [--require-candidate]
//   --package-root  an installed pi-subagents directory (default: this repository's devDependency,
//                   a fixture - pass the operator's real install to probe what Pi actually runs)
//   --settings      the Pi settings.json to use (its directory becomes the Pi agent dir)
// Exit: 0 report (or candidate effective under --require-candidate); 1 candidate shadowed under
//       --require-candidate; 2 usage/environment error (including a settings file the package rejects).
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { makeLoader, readPackage, runPreflight } from "./lib/pi_subagents_package.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Pi's user settings file: `$PI_CODING_AGENT_DIR/settings.json`, else `<home>/.pi/agent/settings.json`. */
export function defaultSettingsPath(env, home) {
  const dir = env.PI_CODING_AGENT_DIR;
  return path.join(dir ? path.resolve(dir) : path.join(home, ".pi", "agent"), "settings.json");
}

function parse(argv) {
  const opts = {
    project: process.cwd(),
    agent: "empirica.empirica-auditor",
    expected: path.join(repo, "plugins", "empirica", "agents", "pi", "empirica-auditor.md"),
    package_root: path.join(repo, "node_modules", "pi-subagents"),
    settings: undefined,
    requireCandidate: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--require-candidate") { opts.requireCandidate = true; continue; }
    const value = argv[i + 1];
    if (!["--project", "--agent", "--expected", "--package-root", "--settings"].includes(flag) || value === undefined)
      throw new Error(`unknown or incomplete argument: ${flag}`);
    opts[flag.slice(2).replace("-", "_")] = value;
    i += 1;
  }
  return opts;
}

function canonical(file) {
  try { return realpathSync.native(file); } catch { return path.resolve(file); }
}

function sameFile(candidate, expected) {
  if (!candidate) return { same: false, basis: "none" };
  if (canonical(candidate) === canonical(expected)) return { same: true, basis: "path" };
  try {
    const equal = readFileSync(candidate).equals(readFileSync(expected));
    return { same: equal, basis: equal ? "bytes" : "different-bytes" };
  } catch { return { same: false, basis: "unreadable" }; }
}

async function main() {
  let opts;
  try { opts = parse(process.argv.slice(2)); } catch (error) {
    console.error(String(error.message)); return 2;
  }
  const project = path.resolve(opts.project);
  if (!existsSync(project) || !statSync(project).isDirectory()) {
    console.error(`project directory does not exist: ${project}`); return 2;
  }
  if (!existsSync(opts.expected)) {
    console.error(`expected auditor file does not exist: ${opts.expected}`); return 2;
  }
  const explicitSettings = opts.settings !== undefined;
  const settings = path.resolve(opts.settings ?? defaultSettingsPath(process.env, os.homedir()));
  if (path.basename(settings) !== "settings.json") {
    console.error(`--settings must name a settings.json file: ${settings}`); return 2;
  }
  if (explicitSettings && !existsSync(settings)) {
    console.error(`settings file does not exist: ${settings}`); return 2;
  }
  let pkg;
  try { pkg = readPackage(opts.package_root); } catch (error) {
    console.error(String(error.message)); return 2;
  }
  const loader = makeLoader(path.join(pkg.root, "package.json"));
  const scopes = {};
  for (const scope of ["both", "user", "project"]) {
    let response;
    try {
      response = await runPreflight({
        packageRoot: pkg.root, loader, agentDir: path.dirname(settings),
        input: { agent: opts.agent, context: "fresh", agentScope: scope, cwd: project },
      });
    } catch (error) {
      // The runtime rejected this configuration (for example a settings field it removed): an
      // environment error to report, not a resolution to guess at.
      console.error(`pi-subagents ${pkg.version} (${pkg.root}) rejected ${settings} for scope ${scope}: ${String(error.message ?? error)}`);
      return 2;
    }
    const agent = response.ok ? response.contract.agent : undefined;
    scopes[scope] = {
      matches: agent ? 1 + (agent.shadowedCandidates?.length ?? 0) : 0,
      file: agent?.filePath ?? null,
      source: agent?.source ?? null,
      model: response.ok ? (response.contract.model ?? null) : null,
      ...(response.ok ? {} : { refused: response.code }),
      ...sameFile(agent?.filePath, opts.expected),
    };
  }
  const report = {
    project, agent: opts.agent, expected: canonical(opts.expected), pi_subagents: pkg.version,
    package_root: pkg.root, settings: existsSync(settings) ? settings : null, scopes,
    verdict: scopes.both.same ? `candidate effective (${scopes.both.basis})` : "candidate SHADOWED in default scope",
  };
  console.log(JSON.stringify(report, null, 2));
  return opts.requireCandidate && !scopes.both.same ? 1 : 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  process.exitCode = await main();
