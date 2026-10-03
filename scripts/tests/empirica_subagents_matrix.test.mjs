// The pure parts of the reviewed pi-subagents matrix (scripts/empirica_subagents_matrix.mjs): which
// versions are checked, how each check fails, how a timeout or a missing package is reported. The
// network and the per-version packages are exercised by `make empirica-subagents-matrix` itself.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  classifierVerdict, compareVersions, evaluateCell, inspectInChildren, maskMachineDigests, newerThanReviewed,
  parseVersion, planCells, processVerdict, reviewCommands, reviewedFrom, runMatrix,
} from "../empirica_subagents_matrix.mjs";
import { loadInventory } from "../../plugins/empirica/adapters/pi/src/subagent-inventory.ts";
import { classifySubagentCall, MANAGEMENT_ACTIONS } from "../../plugins/empirica/adapters/pi/src/translate.ts";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const profiles = JSON.parse(readFileSync(path.join(repo, "contracts", "empirica", "v2", "host-profiles.json"), "utf8"));
const compat = path.join(repo, "plugins", "empirica", "adapters", "pi", "compat");

test("versions are exact releases, compared numerically, never as strings", () => {
  assert.deepEqual(parseVersion("0.75.0"), [0, 75, 0]);
  for (const bad of [">=0.75.0", "0.75", "0.75.0-beta.1", "v0.75.0", ""]) assert.throws(() => parseVersion(bad), /not an exact release/);
  assert.ok(compareVersions("0.9.0", "0.10.0") < 0, "0.9.0 < 0.10.0 (a string comparison says otherwise)");
  assert.equal(compareVersions("1.2.3", "1.2.3"), 0);
});

test("the matrix checks exactly the contract's reviewed versions, which are exactly the checked-in inventories", () => {
  const reviewed = reviewedFrom(profiles);
  const inventories = readdirSync(compat).map((file) => /^pi-subagents-(.+)\.json$/.exec(file)?.[1]).filter(Boolean);
  assert.deepEqual(reviewed.versions, inventories.sort(compareVersions));
  assert.equal(reviewed.policy_id, "pi-subagents-foreground-audit-v1");
});

test("reviewedFrom refuses a document with no profile, two profiles, or another package", () => {
  const withPolicy = profiles.profiles.find((profile) => profile.subagents_compatibility);
  assert.throws(() => reviewedFrom({ profiles: profiles.profiles.filter((profile) => profile !== withPolicy) }), /found 0/);
  assert.throws(() => reviewedFrom({ profiles: [withPolicy, { ...withPolicy, profile_id: "x" }] }), /found 2/);
  assert.throws(() => reviewedFrom({ profiles: [{ ...withPolicy, subagents_compatibility: { ...withPolicy.subagents_compatibility, package: "other" } }] }), /not pi-subagents/);
});

test("the version the devDependency pins runs in place; every other reviewed version comes from the registry", () => {
  assert.deepEqual(planCells(["0.50.0", "0.75.0"], "0.75.0"), [
    { version: "0.50.0", source: "registry" }, { version: "0.75.0", source: "local" }]);
  assert.deepEqual(planCells(["0.50.0"], undefined), [{ version: "0.50.0", source: "registry" }]);
});

test("only exact releases newer than the newest reviewed are proposed, oldest first", () => {
  const registry = ["0.49.0", "0.75.0", "0.76.0-beta.1", "0.100.0", "0.76.1", "0.9.0", "next"];
  assert.deepEqual(newerThanReviewed(registry, ["0.50.0", "0.75.0"]), ["0.76.1", "0.100.0"]);
  assert.deepEqual(newerThanReviewed(["0.75.0"], ["0.75.0"]), []);
});

test("the review commands name the exact version and make targets that exist; none edits the contract", () => {
  const commands = reviewCommands("0.76.0", "/tmp/m");
  assert.ok(commands.some((command) => command.includes("pi-subagents@0.76.0")));
  const makefile = readFileSync(path.join(repo, "Makefile"), "utf8");
  for (const target of ["pi-subagents-inventory", "pi-preflight-fixture", "empirica-subagents-matrix"])
    assert.match(makefile, new RegExp(`^${target}:`, "m"), `${target} is a Makefile target`);
  assert.ok(commands.every((command) => !/sed |>> |--write/.test(command)));
});

// --- the four checks -------------------------------------------------------------------------------

const INVENTORY = loadInventory("0.75.0");
const GOOD = () => ({
  version: "0.75.0", request: { agent: "a", task: "t", toolBudget: {} }, properties: ["agent", "task", "toolBudget", "model"],
  inventory: INVENTORY, liveFixture: { v: 1, cases: { x: { response: { contract: { digest: "a".repeat(64) } } } } },
  checkedFixture: { v: 1, cases: { x: { response: { contract: { digest: "b".repeat(64) } } } } },
  classify: classifySubagentCall, managementActions: MANAGEMENT_ACTIONS,
});
const failing = (checks) => Object.entries(checks).filter(([, check]) => !check.ok).map(([name]) => name);

test("a good version passes every check", () => assert.deepEqual(failing(evaluateCell(GOOD())), []));

test("a parameter the package no longer declares fails the schema check alone", () => {
  const checks = evaluateCell({ ...GOOD(), properties: ["agent", "task", "model"] });
  assert.deepEqual(failing(checks), ["schema"]);
  assert.match(checks.schema.detail, /toolBudget/);
});

test("a changed preflight answer fails the preflight check alone; a different machine digest does not", () => {
  const drift = GOOD();
  drift.liveFixture.cases.x.response.contract.message = "changed";
  assert.deepEqual(failing(evaluateCell(drift)), ["preflight"]);
  const sameButDigest = GOOD();
  sameButDigest.checkedFixture.cases.x.response.contract.digest = "c".repeat(64);
  assert.deepEqual(failing(evaluateCell(sameButDigest)), []);
  const notAHash = GOOD();
  notAHash.liveFixture.cases.x.response.contract.digest = "not-a-hash";
  assert.deepEqual(failing(evaluateCell(notAHash)), ["preflight"], "only a sha256 digest is masked");
  const otherInstall = GOOD();
  otherInstall.liveFixture.cases.x.response.contract.launchContractDigest = "d".repeat(64);
  otherInstall.checkedFixture.cases.x.response.contract.launchContractDigest = "e".repeat(64);
  assert.deepEqual(failing(evaluateCell(otherInstall)), [], "the same package in another directory hashes differently");
});

test("maskMachineDigests masks the path-bearing digests only, recursively, without mutating its input", () => {
  const input = { digest: "a".repeat(64), launchContractDigest: "a".repeat(64), version: 3, deep: [{ digest: "b".repeat(64) }] };
  const masked = maskMachineDigests(input);
  assert.equal(masked.digest, "<path-bearing digest>");
  assert.equal(masked.launchContractDigest, "<path-bearing digest>");
  assert.equal(masked.version, 3, "every other member is compared as it is");
  assert.equal(masked.deep[0].digest, "<path-bearing digest>");
  assert.equal(input.digest, "a".repeat(64));
});

test("the classifier admits exactly the reviewed surface of every reviewed inventory", () => {
  for (const version of reviewedFrom(profiles).versions) {
    const verdict = classifierVerdict({ version, inventory: loadInventory(version), classify: classifySubagentCall, managementActions: MANAGEMENT_ACTIONS });
    assert.equal(verdict.ok, true, `${version}: ${verdict.detail}`);
  }
});

test("a classifier that lets a mutation action through fails the classifier check", () => {
  const permissive = (input, inventory) => (input.action === "cancel" || input.action === "delete") ? { kind: "management", detail: "" } : classifySubagentCall(input, inventory);
  const inventory = { ...INVENTORY, actions: [...INVENTORY.actions, "delete"] };
  const verdict = classifierVerdict({ version: "0.75.0", inventory, classify: permissive, managementActions: MANAGEMENT_ACTIONS });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /action delete outside the read-only allowlist: expected unsupported, got management/);
});

test("a new launch form that the classifier cannot tell apart from an action is caught", () => {
  const blind = () => ({ kind: "management", detail: "" });
  const verdict = classifierVerdict({ version: "0.75.0", inventory: INVENTORY, classify: blind, managementActions: MANAGEMENT_ACTIONS });
  assert.equal(verdict.ok, false);
});

// --- process outcomes ------------------------------------------------------------------------------

test("a timeout, a signal and a nonzero exit are each a failure; only exit 0 is ok", () => {
  assert.deepEqual(processVerdict({ status: 0 }, "x"), { ok: true });
  assert.match(processVerdict({ error: Object.assign(new Error("spawnSync node ETIMEDOUT"), { code: "ETIMEDOUT" }), status: null }, "cell").detail, /cell: timed out/);
  assert.match(processVerdict({ status: null, signal: "SIGKILL" }, "cell").detail, /killed by SIGKILL/);
  assert.match(processVerdict({ status: 1, stderr: "a\nb\nc" }, "gen").detail, /exit 1: b \| c/);
});

test("a cell whose child times out is a failed cell, and the inventory check still ran", () => {
  const calls = [];
  const run = (command, args) => {
    calls.push(args[0]);
    return args.includes("--check") ? { status: 0, stdout: "" } : { error: Object.assign(new Error("x"), { code: "ETIMEDOUT" }), status: null };
  };
  const checks = inspectInChildren({ version: "0.75.0" }, "/pkg", run);
  assert.equal(checks.inventory.ok, true);
  assert.equal(checks.cell.ok, false);
  assert.match(checks.cell.detail, /timed out/);
  assert.equal(calls.length, 2);
});

test("a cell whose inventory check fails still reports the child's checks", () => {
  const run = (command, args) => args.includes("--check")
    ? { status: 1, stderr: "drifted" } : { status: 0, stdout: JSON.stringify({ schema: { ok: true } }) };
  const checks = inspectInChildren({ version: "0.75.0" }, "/pkg", run);
  assert.equal(checks.inventory.ok, false);
  assert.equal(checks.schema.ok, true);
});

test("one failing or unfetchable version never masks the others, and the matrix is not ok", async () => {
  const lines = [];
  const results = await runMatrix({
    cells: [{ version: "1.0.0", source: "registry" }, { version: "2.0.0", source: "registry" }, { version: "3.0.0", source: "local" }],
    prepare: (cell) => { if (cell.version === "1.0.0") throw new Error("npm install exited 1"); return `/pkg/${cell.version}`; },
    inspect: (cell) => ({ a: { ok: cell.version !== "3.0.0" } }),
    emit: (line) => lines.push(line), now: (() => { let t = 0; return () => (t += 5); })(),
  });
  assert.deepEqual(results.map((r) => [r.version, r.ok]), [["1.0.0", false], ["2.0.0", true], ["3.0.0", false]]);
  assert.equal(lines.length, 3, "one line per version");
  assert.match(results[0].checks.prepare.detail, /npm install exited 1/);
  assert.equal(results[1].ms, 5);
});
