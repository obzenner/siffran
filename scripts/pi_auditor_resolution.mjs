#!/usr/bin/env node
// Read-only probe: which file does pi-subagents actually resolve for the Empirica auditor?
//
// Why this exists: the Pi adapter blocks an audit when the resolved auditor file is not the
// package's own file ("empirica auditor package identity was shadowed"). pi-subagents merges
// package agents into a name-keyed map where the LAST discovered root wins, and user-level
// packages (for example a globally installed siffran) are discovered after project packages, so a
// dogfooded checkout can be shadowed by the installed copy. This probe reports the effective
// resolution per agent scope using the repository's pinned pi-subagents, so the condition is
// visible before a native qualification instead of at audit launch.
//
// It only reads agent/settings files (pi-subagents may run `npm root -g`). It never launches a
// child, writes artifacts, or edits settings.
//
// Usage: node scripts/pi_auditor_resolution.mjs [--project DIR] [--agent NAME] [--expected FILE]
//                                               [--require-candidate]
// Exit: 0 report (or candidate effective under --require-candidate); 1 candidate shadowed under
//       --require-candidate; 2 usage/environment error.
import { createJiti } from "jiti";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function parse(argv) {
  const opts = {
    project: process.cwd(),
    agent: "empirica.empirica-auditor",
    expected: path.join(repo, "plugins", "empirica", "agents", "pi", "empirica-auditor.md"),
    requireCandidate: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--require-candidate") { opts.requireCandidate = true; continue; }
    const value = argv[i + 1];
    if (!["--project", "--agent", "--expected"].includes(flag) || value === undefined)
      throw new Error(`unknown or incomplete argument: ${flag}`);
    opts[flag.slice(2)] = value;
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
  const pkgRoot = path.join(repo, "node_modules", "pi-subagents");
  const version = JSON.parse(readFileSync(path.join(pkgRoot, "package.json"), "utf8")).version;
  const agents = await createJiti(import.meta.url).import(path.join(pkgRoot, "src", "agents", "agents.ts"));
  const scopes = {};
  for (const scope of ["both", "user", "project"]) {
    const found = agents.discoverAgents(project, scope).agents
      .filter((agent) => agent.name === opts.agent || agent.localName === opts.agent);
    const effective = found[0];
    scopes[scope] = {
      matches: found.length,
      file: effective?.filePath ?? null,
      source: effective?.source ?? null,
      model: effective?.model ?? null,
      ...sameFile(effective?.filePath, opts.expected),
    };
  }
  const report = {
    project, agent: opts.agent, expected: canonical(opts.expected), pi_subagents: version, scopes,
    verdict: scopes.both.same ? `candidate effective (${scopes.both.basis})` : "candidate SHADOWED in default scope",
  };
  console.log(JSON.stringify(report, null, 2));
  return opts.requireCandidate && !scopes.both.same ? 1 : 0;
}

process.exitCode = await main();
