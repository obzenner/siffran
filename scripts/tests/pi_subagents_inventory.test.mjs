// The reviewed pi-subagents inventories (plugins/empirica/adapters/pi/compat/*.json) against the
// real packages, and the audit launch's fields against the package's own parameter schema.
//
// The devDependency copy (node_modules/pi-subagents) is a hard requirement: its inventory must equal
// a fresh generation and must not be skipped. Older reviewed versions are verified when their
// package roots are supplied as PI_SUBAGENTS_ROOTS='{"0.50.0":"/path","0.64.0":"/path"}'; the test
// log names which were verified.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  COMPAT_DIR, generateInventory, inventoryPath, serialiseInventory, subagentParamProperties,
} from "../gen_pi_subagents_inventory.mjs";
import { readPackage } from "../lib/pi_subagents_package.mjs";
import { auditLaunchInput } from "../../plugins/empirica/adapters/pi/src/audit-launch.ts";
import { AUDIT_LAUNCH_POLICY } from "../../plugins/empirica/adapters/pi/src/host-profile.ts";
import { loadInventory, LAUNCH_FORMS } from "../../plugins/empirica/adapters/pi/src/subagent-inventory.ts";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const devDependency = path.join(repo, "node_modules", "pi-subagents");
const declared = JSON.parse(readFileSync(path.join(repo, "package.json"), "utf8")).devDependencies["pi-subagents"];

test("the devDependency is pinned exactly and is the installed copy", () => {
  assert.match(declared, /^\d+\.\d+\.\d+$/, "an exact version, not a range");
  assert.equal(readPackage(devDependency).version, declared);
});

test("the devDependency's checked-in inventory equals a fresh generation (never skipped)", async () => {
  const fresh = serialiseInventory(await generateInventory(devDependency));
  assert.equal(readFileSync(inventoryPath(COMPAT_DIR, declared), "utf8"), fresh);
});

test("every other reviewed inventory equals a fresh generation where its package root is supplied", async (t) => {
  const roots = process.env.PI_SUBAGENTS_ROOTS ? JSON.parse(process.env.PI_SUBAGENTS_ROOTS) : {};
  const verified = [];
  for (const [version, root] of Object.entries(roots)) {
    const fresh = await generateInventory(root);
    assert.equal(fresh.version, version, `${root} is not pi-subagents ${version}`);
    assert.equal(readFileSync(inventoryPath(COMPAT_DIR, version), "utf8"), serialiseInventory(fresh), version);
    verified.push(version);
  }
  t.diagnostic(`verified against supplied package roots: ${verified.length ? verified.join(", ") : "(none supplied)"}`);
});

test("every checked-in inventory names its own version, is canonical JSON, and uses only known launch forms", () => {
  const files = readdirSync(COMPAT_DIR).filter((name) => name.endsWith(".json")).sort();
  assert.ok(files.length >= 4);
  for (const file of files) {
    const version = file.replace(/^pi-subagents-/, "").replace(/\.json$/, "");
    const text = readFileSync(path.join(COMPAT_DIR, file), "utf8");
    const inventory = JSON.parse(text);
    assert.equal(inventory.version, version, file);
    assert.equal(text, serialiseInventory(inventory), `${file} is not in canonical form`);
    assert.ok(inventory.launch_forms.every((form) => LAUNCH_FORMS.includes(form)), file);
    assert.ok(loadInventory(version), `${file} is not loadable by the adapter`);
  }
  assert.ok(files.includes(`pi-subagents-${declared}.json`));
});

test("the fixed inventories record what each version's schema declares (observed differences)", () => {
  const read = (version) => JSON.parse(readFileSync(inventoryPath(COMPAT_DIR, version), "utf8"));
  assert.deepEqual(read("0.50.0").launch_forms, ["agent", "workflowScript", "resume"]);
  assert.equal(read("0.50.0").supports.turnBudget, true);
  assert.equal(read("0.64.0").supports.turnBudget, false);
  assert.deepEqual(read("0.64.0").launch_forms, ["agent", "workflowScript", "workflowScriptPath", "workflow"]);
  assert.deepEqual(read(declared).launch_forms, ["agent", "workflow"]);
  for (const version of ["0.50.0", "0.64.0", declared]) assert.equal(read(version).supports.toolBudget, true, version);
});

test("every field the audit launch sets is declared by the devDependency's own parameter schema", async () => {
  const properties = await subagentParamProperties(devDependency);
  const launch = auditLaunchInput({ task: "t", model: "a/b", agentScope: "user", policy: AUDIT_LAUNCH_POLICY });
  for (const key of ["agent", ...Object.keys(launch)])
    assert.ok(properties.includes(key), `${key} is not a subagent tool parameter at ${declared}`);
  assert.equal(properties.includes("turnBudget"), false, "turnBudget is not a parameter: it would be ignored, not enforced");
  assert.equal("turnBudget" in launch, false);
});

test("a key the schema does not declare would fail the membership check (negative control)", async () => {
  const properties = await subagentParamProperties(devDependency);
  const launch = { ...auditLaunchInput({ task: "t", model: "a/b", agentScope: "user", policy: AUDIT_LAUNCH_POLICY }),
    turnBudget: { maxTurns: 8, graceTurns: 1 } };
  assert.ok(Object.keys(launch).some((key) => !properties.includes(key)));
});
