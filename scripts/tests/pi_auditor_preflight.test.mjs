// Real-preflight regression for the Pi auditor resolver (native qualification P1-D1). The gate
// tests drive defaultAuditContractResolver through fake seams whose candidates carry no thinking
// level; here the pinned pi-subagents preflight resolves this checkout's packaged auditor, whose
// `thinking: high` it appends to the candidate. Isolated HOME/PI_CODING_AGENT_DIR and PI_OFFLINE=1.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

const PROBE = `
import { createJiti } from "jiti";
const jiti = createJiti(${JSON.stringify(path.join(repo, "package.json"))});
const adapter = await jiti.import(${JSON.stringify(path.join(repo, "plugins/empirica/adapters/pi/src/index.ts"))});
const preflight = await jiti.import(${JSON.stringify(path.join(repo, "node_modules/pi-subagents/src/api/preflight.ts"))});
const expectedAgent = ${JSON.stringify(path.join(repo, "plugins/empirica/agents/pi/empirica-auditor.md"))};
const configured = process.env.PROBE_MODEL;
const ctx = { cwd: ${JSON.stringify(repo)}, model: { provider: "main", id: "author" },
  modelRegistry: { getAvailable: () => [{ provider: "audit", id: "reviewer" }] } };
const settings = { getAgentDir: () => process.env.PI_CODING_AGENT_DIR, SettingsManager: { create: () => ({
  getGlobalSettings: () => ({ subagents: { defaultModel: configured } }), getProjectSettings: () => ({}) }) } };
try {
  const resolved = await adapter.defaultAuditContractResolver(
    { agent: "empirica.empirica-auditor", task: "Audit the host-provided dossier.", expectedAgent },
    ctx, { preflight, settings });
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
  assert.equal(resolved.agentFilePath, path.join(repo, "plugins/empirica/agents/pi/empirica-auditor.md"));
});

test("the real preflight still refuses a configured model the registry cannot serve", (t) => {
  const resolved = probe(t, "missing/reviewer");
  assert.equal(resolved.ok, false);
  assert.match(resolved.message, /Unknown subagent model 'missing\/reviewer'/);
});

test("the adapter strips exactly the thinking levels the pinned pi-subagents appends", async () => {
  const { createJiti } = await import("jiti");
  const jiti = createJiti(path.join(repo, "package.json"));
  const { THINKING_LEVELS } = await jiti.import(path.join(repo, "node_modules/pi-subagents/src/shared/model-info.ts"));
  const { withoutThinkingLevel } = await jiti.import(path.join(repo, "plugins/empirica/adapters/pi/src/index.ts"));
  assert.ok(THINKING_LEVELS.length > 0);
  for (const level of THINKING_LEVELS) assert.equal(withoutThinkingLevel(`audit/reviewer:${level}`), "audit/reviewer", level);
  assert.equal(withoutThinkingLevel("audit/reviewer-v1:0"), "audit/reviewer-v1:0");
});
