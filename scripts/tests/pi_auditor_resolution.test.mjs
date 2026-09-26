// Tests for scripts/pi_auditor_resolution.mjs against the pinned pi-subagents, fully isolated:
// temporary HOME and PI_CODING_AGENT_DIR, and PI_OFFLINE=1 so no global npm root is consulted.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const script = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "pi_auditor_resolution.mjs");
const AGENT = (model) => `---\nname: empirica-auditor\npackage: empirica\ndescription: test auditor\nmodel: ${model}\n---\n\nbody\n`;

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), "pi-auditor-resolution-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const home = path.join(root, "home");
  const agentDir = path.join(home, ".pi", "agent");
  mkdirSync(agentDir, { recursive: true });
  const pkg = (dir, model) => {
    mkdirSync(path.join(dir, "agents"), { recursive: true });
    writeFileSync(path.join(dir, "package.json"),
      JSON.stringify({ name: path.basename(dir), pi: { subagents: { agents: ["./agents"] } } }));
    writeFileSync(path.join(dir, "agents", "empirica-auditor.md"), AGENT(model));
    return path.join(dir, "agents", "empirica-auditor.md");
  };
  const userPackages = (sources) =>
    writeFileSync(path.join(agentDir, "settings.json"), JSON.stringify({ packages: sources }));
  const run = (args) => spawnSync(process.execPath, [script, ...args], {
    encoding: "utf8",
    env: { ...process.env, HOME: home, PI_CODING_AGENT_DIR: agentDir, PI_OFFLINE: "1" },
  });
  return { root, pkg, userPackages, run };
}

function report(result) {
  assert.equal(result.status === 0 || result.status === 1, true, result.stderr);
  return JSON.parse(result.stdout);
}

test("project package alone is the effective auditor", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  const expected = f.pkg(project, "a/model-1");
  const r = report(f.run(["--project", project, "--expected", expected, "--require-candidate"]));
  assert.equal(r.scopes.both.same, true);
  assert.equal(r.scopes.both.basis, "path");
});

test("a user package with different bytes shadows the project candidate", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  const expected = f.pkg(project, "a/model-1");
  const installed = path.join(f.root, "installed");
  f.pkg(installed, "a/model-2");
  f.userPackages([installed]);
  const result = f.run(["--project", project, "--expected", expected, "--require-candidate"]);
  assert.equal(result.status, 1);
  const r = JSON.parse(result.stdout);
  assert.equal(r.scopes.both.same, false);
  assert.equal(r.scopes.both.basis, "different-bytes");
  assert.equal(r.scopes.both.model, "a/model-2");
  assert.equal(r.scopes.project.same, true);
  assert.match(r.verdict, /SHADOWED/);
});

test("a byte-identical installed copy is accepted by content, like the adapter guard", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  const expected = f.pkg(project, "a/model-1");
  const installed = path.join(f.root, "installed");
  f.pkg(installed, "a/model-1");
  f.userPackages([installed]);
  const r = report(f.run(["--project", project, "--expected", expected, "--require-candidate"]));
  assert.equal(r.scopes.both.same, true);
  assert.equal(r.scopes.both.basis, "bytes");
});

test("a missing project directory is an explicit error, not an empty resolution", (t) => {
  const f = fixture(t);
  const expected = f.pkg(path.join(f.root, "project"), "a/model-1");
  const result = f.run(["--project", path.join(f.root, "absent"), "--expected", expected]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /does not exist/);
});
