// Real-preflight regression for the Pi auditor resolver (native qualification P1-D1). The gate tests
// drive defaultAuditContractResolver through captured responses; here the devDependency
// pi-subagents' PUBLIC preflight (`pi-subagents/preflight`, loaded from an explicit package root)
// resolves this checkout's packaged auditor, whose `thinking: high` it appends to the candidate.
// Isolated HOME/PI_CODING_AGENT_DIR and PI_OFFLINE=1; no private pi-subagents module is imported.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { makeLoader, readPackage, runPreflight } from "../lib/pi_subagents_package.mjs";
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
const ctx = { cwd: ${JSON.stringify(repo)}, model: { provider: "main", id: "author" },
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

function probe(t, model) {
  const root = mkdtempSync(path.join(os.tmpdir(), "pi-auditor-preflight-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const agentDir = path.join(root, "home", ".pi", "agent");
  mkdirSync(agentDir, { recursive: true });
  const result = spawnSync(process.execPath, ["--input-type=module", "-e", PROBE], {
    cwd: repo, encoding: "utf8",
    env: { ...process.env, HOME: path.join(root, "home"), PI_CODING_AGENT_DIR: agentDir, PI_OFFLINE: "1",
           PROBE_MODEL: model },
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
  const root = mkdtempSync(path.join(os.tmpdir(), "pi-auditor-levels-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const agentDir = path.join(root, "agent");
  mkdirSync(agentDir);
  const loader = makeLoader(path.join(packageRoot, "package.json"));
  const launch = (model) => runPreflight({ packageRoot, loader, agentDir, env: { PI_OFFLINE: "1" },
    input: { agent: "empirica.empirica-auditor", task: "x", context: "fresh", model, agentScope: "both", cwd: repo,
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
