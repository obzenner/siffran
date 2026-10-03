// Real-preflight regression for the Pi auditor resolver (native qualification P1-D1). The gate tests
// drive defaultAuditContractResolver through captured responses; here the devDependency
// pi-subagents' PUBLIC preflight (`pi-subagents/preflight`, loaded from an explicit package root)
// resolves this checkout's packaged auditor, whose `thinking: high` it appends to the candidate.
// The operator's home is never read: every call runs in a scratch project (a package whose agents
// directory is the packaged auditor) with HOME, USERPROFILE and PI_CODING_AGENT_DIR in scratch, and
// PI_OFFLINE=1. A decoy home holding invalid agents is installed for the whole file to prove it.
// No private pi-subagents module is imported.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { makeLoader, readPackage, runPreflight } from "../lib/pi_subagents_package.mjs";
import { useDecoyHome } from "./decoy_home.mjs";
import { THINKING_LEVELS } from "../../plugins/empirica/adapters/pi/src/host-profile.ts";
import { withoutThinkingLevel } from "../../plugins/empirica/adapters/pi/src/preflight-seam.ts";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const packageRoot = path.join(repo, "node_modules", "pi-subagents");
const auditor = path.join(repo, "plugins", "empirica", "agents", "pi", "empirica-auditor.md");

const PROBE = `
import { createJiti } from "jiti";
import { importExport } from ${JSON.stringify(path.join(repo, "scripts/lib/pi_subagents_package.mjs"))};
const jiti = createJiti(${JSON.stringify(path.join(repo, "package.json"))});
const adapter = await jiti.import(${JSON.stringify(path.join(repo, "plugins/empirica/adapters/pi/src/index.ts"))});
const inventories = await jiti.import(${JSON.stringify(path.join(repo, "plugins/empirica/adapters/pi/src/subagent-inventory.ts"))});
const preflight = await importExport(${JSON.stringify(packageRoot)}, "./preflight");
const version = ${JSON.stringify(readPackage(packageRoot).version)};
const expectedAgent = ${JSON.stringify(auditor)};
const configured = process.env.PROBE_MODEL;
const ctx = { cwd: process.env.PROBE_PROJECT, model: { provider: "main", id: "author" },
  modelRegistry: { getAvailable: () => [{ provider: "audit", id: "reviewer" }] } };
const settings = { getAgentDir: () => process.env.PI_CODING_AGENT_DIR, SettingsManager: { create: () => ({
  getGlobalSettings: () => ({ subagents: { defaultModel: configured } }), getProjectSettings: () => ({}) }) } };
try {
  const resolved = await adapter.defaultAuditContractResolver(
    { agent: "empirica.empirica-auditor", task: "Audit the host-provided dossier.", expectedAgent },
    ctx, { runtime: { preflight, version, inventory: inventories.loadInventory(version) }, settings });
  console.log(JSON.stringify({ ok: true, ...resolved }));
} catch (error) { console.log(JSON.stringify({ ok: false, message: String(error.message) })); }
`;

/** A scratch root: `project` (a package whose agents directory is the packaged auditor), `home`, `agent`. */
function scratchRoot(t, prefix) {
  const root = mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const dirs = { root, project: path.join(root, "project"), home: path.join(root, "home"), agent: path.join(root, "home", ".pi", "agent") };
  for (const dir of [dirs.project, dirs.agent]) mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dirs.project, "package.json"),
    JSON.stringify({ name: "x", pi: { subagents: { agents: [path.dirname(auditor)] } } }));
  return dirs;
}

function probe(t, model) {
  const dirs = scratchRoot(t, "pi-auditor-preflight-");
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", PROBE], {
    cwd: repo, encoding: "utf8",
    env: { ...process.env, HOME: dirs.home, USERPROFILE: dirs.home, PI_CODING_AGENT_DIR: dirs.agent, PI_OFFLINE: "1",
           PROBE_MODEL: model, PROBE_PROJECT: dirs.project },
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout.trim().split("\n").at(-1));
}

test("the real preflight admits the packaged auditor for its configured reviewer model", (t) => {
  const resolved = probe(t, "audit/reviewer");
  assert.equal(resolved.ok, true, resolved.message);
  assert.equal(resolved.model, "audit/reviewer");
  assert.equal(resolved.agentFilePath, auditor);
});

test("the real preflight still refuses a configured model the registry cannot serve", (t) => {
  const resolved = probe(t, "missing/reviewer");
  assert.equal(resolved.ok, false);
  assert.match(resolved.message, /Unknown subagent model 'missing\/reviewer'/);
});

test("the real preflight accepts every contract thinking level as a model suffix, and the adapter strips exactly those", async (t) => {
  useDecoyHome(t); // the operator's home holds agents that would make any read of it fail the preflight
  const dirs = scratchRoot(t, "pi-auditor-levels-");
  const loader = makeLoader(path.join(packageRoot, "package.json"));
  const launch = (model) => runPreflight({ packageRoot, loader, agentDir: dirs.agent,
    env: { PI_OFFLINE: "1", HOME: dirs.home, USERPROFILE: dirs.home },
    input: { agent: "empirica.empirica-auditor", task: "x", context: "fresh", model, agentScope: "both", cwd: dirs.project,
      availableModels: [{ provider: "audit", id: "reviewer" }] } });
  assert.ok(THINKING_LEVELS.size > 0);
  for (const level of THINKING_LEVELS) {
    const response = await launch(`audit/reviewer:${level}`);
    assert.equal(response.ok, true, `${level}: ${response.message}`);
    assert.equal(response.contract.model, `audit/reviewer:${level}`, `the runtime keeps ${level} as the level`);
    assert.equal(withoutThinkingLevel(response.contract.model), "audit/reviewer", level);
  }
  // A non-level suffix is part of the model id to the runtime, and to the adapter.
  await assert.rejects(launch("audit/reviewer:ultra"), /Unknown subagent model/);
  assert.equal(withoutThinkingLevel("audit/reviewer:ultra"), "audit/reviewer:ultra");
  assert.equal(withoutThinkingLevel("audit/reviewer-v1:0"), "audit/reviewer-v1:0");
});
