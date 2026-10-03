// Tests for scripts/pi_auditor_resolution.mjs against the devDependency pi-subagents, through its
// public `pi-subagents/preflight` export, fully isolated: the settings file is an explicit input
// (a temporary directory - the real home is never read), HOME points at a temporary directory, and
// PI_OFFLINE=1 so no global npm root is consulted.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { defaultSettingsPath } from "../pi_auditor_resolution.mjs";

const scripts = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const script = path.join(scripts, "pi_auditor_resolution.mjs");
const packageRoot = path.join(scripts, "..", "node_modules", "pi-subagents");
const AGENT = (model) => `---\nname: empirica-auditor\npackage: empirica\ndescription: test auditor\nmodel: ${model}\n---\n\nbody\n`;

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), "pi-auditor-resolution-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const home = path.join(root, "home");
  const agentDir = path.join(home, ".pi", "agent");
  mkdirSync(agentDir, { recursive: true });
  const settings = path.join(agentDir, "settings.json");
  writeFileSync(settings, "{}");
  const pkg = (dir, model) => {
    mkdirSync(path.join(dir, "agents"), { recursive: true });
    writeFileSync(path.join(dir, "package.json"),
      JSON.stringify({ name: path.basename(dir), pi: { subagents: { agents: ["./agents"] } } }));
    writeFileSync(path.join(dir, "agents", "empirica-auditor.md"), AGENT(model));
    return path.join(dir, "agents", "empirica-auditor.md");
  };
  const userPackages = (sources) => writeFileSync(settings, JSON.stringify({ packages: sources }));
  // The subprocess environment carries no PI_CODING_AGENT_DIR: the settings file is the only input.
  const { PI_CODING_AGENT_DIR: _unused, ...inherited } = process.env;
  const run = (args, extra = ["--package-root", packageRoot, "--settings", settings]) =>
    spawnSync(process.execPath, [script, ...args, ...extra], {
      encoding: "utf8", env: { ...inherited, HOME: home, PI_OFFLINE: "1" },
    });
  return { root, pkg, userPackages, run, settings };
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

// pi-subagents resolves a name collision by its own precedence (project wins at the devDependency
// version; 0.50 let the last-discovered user package win). The probe reports the runtime's answer, so
// the shadowing scenarios below are written for the version under test.
test("a project package with different bytes shadows the user-level candidate", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  f.pkg(project, "a/model-2");
  const installed = path.join(f.root, "installed");
  const expected = f.pkg(installed, "a/model-1");
  f.userPackages([installed]);
  const result = f.run(["--project", project, "--expected", expected, "--require-candidate"]);
  assert.equal(result.status, 1);
  const r = JSON.parse(result.stdout);
  assert.equal(r.scopes.both.same, false);
  assert.equal(r.scopes.both.basis, "different-bytes");
  assert.match(r.scopes.both.model, /^a\/model-2/);
  assert.equal(r.scopes.both.file, path.join(project, "agents", "empirica-auditor.md"));
  assert.match(r.verdict, /SHADOWED/);
});

test("the both-scope diagnostic accepts a byte-identical project copy of the candidate", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  f.pkg(project, "a/model-1");
  const installed = path.join(f.root, "installed");
  const expected = f.pkg(installed, "a/model-1");
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

test("a missing explicit settings file or package root is an explicit error, not an empty resolution", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  const expected = f.pkg(project, "a/model-1");
  const base = ["--project", project, "--expected", expected];
  const noSettings = f.run(base, ["--package-root", packageRoot, "--settings", path.join(f.root, "absent", "settings.json")]);
  assert.equal(noSettings.status, 2);
  assert.match(noSettings.stderr, /settings file does not exist/);
  const noPackage = f.run(base, ["--package-root", path.join(f.root, "no-pkg"), "--settings", f.settings]);
  assert.equal(noPackage.status, 2);
  const misnamed = f.run(base, ["--package-root", packageRoot, "--settings", path.join(f.root, "home", "other.json")]);
  assert.equal(misnamed.status, 2);
  assert.match(misnamed.stderr, /must name a settings\.json/);
});

test("the settings path is the only source of user packages: the report names it and discovery follows it", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  mkdirSync(project);
  const installed = path.join(f.root, "installed");
  const expected = f.pkg(installed, "a/model-1");
  f.userPackages([installed]);
  const listed = report(f.run(["--project", project, "--expected", expected, "--require-candidate"]));
  assert.equal(listed.settings, f.settings);
  assert.equal(listed.package_root, packageRoot);
  assert.equal(listed.scopes.both.file, expected);
  assert.equal(listed.scopes.both.same, true);
  // Control: the identical install under a settings file that lists no package is not found.
  const elsewhere = path.join(f.root, "elsewhere");
  mkdirSync(elsewhere);
  writeFileSync(path.join(elsewhere, "settings.json"), "{}");
  const result = f.run(["--project", project, "--expected", expected, "--require-candidate"],
    ["--package-root", packageRoot, "--settings", path.join(elsewhere, "settings.json")]);
  assert.equal(result.status, 1);
  const unlisted = JSON.parse(result.stdout);
  assert.equal(unlisted.settings, path.join(elsewhere, "settings.json"));
  assert.equal(unlisted.scopes.both.file, null);
  assert.equal(unlisted.scopes.both.same, false);
});

test("the default settings path is resolved in one place and never from a test's home", () => {
  assert.equal(defaultSettingsPath({ PI_CODING_AGENT_DIR: "/agents/x" }, "/home/u"), "/agents/x/settings.json");
  assert.equal(defaultSettingsPath({}, "/home/u"), "/home/u/.pi/agent/settings.json");
  assert.equal(defaultSettingsPath({ PI_CODING_AGENT_DIR: "" }, "/home/u"), "/home/u/.pi/agent/settings.json");
});

test("a settings file the package rejects is an explicit environment error, not a stack trace or a verdict", (t) => {
  const f = fixture(t);
  const project = path.join(f.root, "project");
  const expected = f.pkg(project, "a/model-1");
  // The devDependency refuses a builtin override that still uses the removed `fallbackModels` field -
  // the situation of an operator whose settings predate it (observed with the real ~/.pi settings).
  writeFileSync(f.settings, JSON.stringify({ subagents: { agentOverrides: { oracle: { fallbackModels: ["a/b"] } } } }));
  const result = f.run(["--project", project, "--expected", expected, "--require-candidate"]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /rejected .*settings\.json for scope both: .*fallbackModels/);
  assert.equal(result.stdout, "");
  assert.doesNotMatch(result.stderr, /\n\s+at /, "no stack trace");
});
