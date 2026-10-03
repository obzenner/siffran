#!/usr/bin/env node
// Capture real `pi-subagents/preflight` responses for the Empirica auditor as a test fixture.
//
// The Pi adapter's preflight seam (`admitAuditPreflight`) is proven against responses that real
// pi-subagents packages produced, never against hand-written shapes. This records, per package
// version, the public preflight's answer to three launches: the canonical auditor, an agent that does
// not exist, and a model the registry cannot serve. It uses a throwaway Pi agent dir and project, so
// no user configuration is read; absolute paths are replaced by placeholders so the file is stable.
//
// Usage: node scripts/capture_pi_preflight_fixture.mjs --package-root DIR [--out-dir DIR]
//   default out dir: plugins/empirica/adapters/pi/test/fixtures  (file: preflight-<version>.json)
import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { makeLoader, readPackage, runPreflight } from "./lib/pi_subagents_package.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const AUDITOR_DIR = path.join(repo, "plugins", "empirica", "agents", "pi");
export const FIXTURE_DIR = path.join(repo, "plugins", "empirica", "adapters", "pi", "test", "fixtures");

export function fixturePath(dir, version) {
  return path.join(dir, `preflight-${version}.json`);
}

/**
 * Placeholders stand in for machine-specific roots; longest root first so nested paths resolve. A root
 * is masked under both its given and its real path (macOS: /tmp is /private/tmp), so the same package
 * captured from a symlinked or a real location yields the same file.
 */
function maskPaths(value, roots) {
  const spellings = Object.entries(roots).flatMap(([name, root]) =>
    [...new Set([root, existsSync(root) ? realpathSync(root) : root])].map((spelling) => [name, spelling]));
  const ordered = spellings.sort((a, b) => b[1].length - a[1].length);
  const walk = (node) => {
    if (typeof node === "string") return ordered.reduce((text, [name, root]) => text.split(root).join(`{{${name}}}`), node);
    if (Array.isArray(node)) return node.map(walk);
    if (node && typeof node === "object") return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, walk(v)]));
    return node;
  };
  return walk(value);
}

export async function captureFixture(packageRoot) {
  const pkg = readPackage(packageRoot);
  const loader = makeLoader(path.join(pkg.root, "package.json"));
  const scratch = realpathSync(mkdtempSync(path.join(os.tmpdir(), "preflight-capture-")));
  try {
    const agentDir = path.join(scratch, "agent");
    const packaged = path.join(scratch, "project");
    const bare = path.join(scratch, "bare");
    for (const dir of [agentDir, packaged, bare]) mkdirSync(dir);
    // The project is a package whose agents directory is the repository's packaged auditor.
    writeFileSync(path.join(packaged, "package.json"),
      JSON.stringify({ name: "siffran-capture", pi: { subagents: { agents: [AUDITOR_DIR] } } }));
    writeFileSync(path.join(agentDir, "settings.json"), "{}");
    const availableModels = [{ provider: "audit", id: "reviewer" }];
    const launch = (cwd, model) => ({ agent: "empirica.empirica-auditor", task: "Audit the host-provided dossier.",
      context: "fresh", model, agentScope: "project", cwd, availableModels });
    const call = async (cwd, model) => {
      const input = launch(cwd, model);
      try {
        const response = await runPreflight({ packageRoot: pkg.root, loader, agentDir, env: { PI_OFFLINE: "1" }, input });
        return { input, response };
      } catch (error) {
        return { input, threw: String(error.message) };
      }
    };
    const roots = { AUDITOR_FILE: path.join(AUDITOR_DIR, "empirica-auditor.md"), AUDITOR_DIR, REPO: repo,
      PACKAGE_ROOT: pkg.root, PROJECT: packaged, BARE: bare, SCRATCH: scratch, TMPDIR: os.tmpdir() };
    return maskPaths({
      version: pkg.version,
      note: "Captured by scripts/capture_pi_preflight_fixture.mjs from the package's public pi-subagents/preflight export; {{NAME}} marks a machine-specific root.",
      cases: {
        canonical_auditor: await call(packaged, "audit/reviewer"),
        unknown_agent: await call(bare, "audit/reviewer"),
        unservable_model: await call(packaged, "missing/reviewer"),
      },
    }, roots);
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

async function main(argv) {
  const opts = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!["--package-root", "--out-dir"].includes(argv[i]) || argv[i + 1] === undefined)
      throw new Error(`unknown or incomplete argument: ${argv[i]}`);
    opts[argv[i].slice(2).replace("-", "_")] = argv[i + 1];
  }
  if (opts.package_root === undefined) throw new Error("--package-root DIR is required");
  const fixture = await captureFixture(opts.package_root);
  const target = fixturePath(opts.out_dir ?? FIXTURE_DIR, fixture.version);
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, `${JSON.stringify(fixture, null, 2)}\n`);
  console.log(`wrote ${target}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main(process.argv.slice(2)).catch((error) => { console.error(String(error.message ?? error)); process.exitCode = 2; });
