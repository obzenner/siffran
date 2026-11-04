// The reviewed pi-subagents inventories (plugins/empirica/adapters/pi/compat/*.json) against the
// real packages, and the audit launch's fields against the package's own parameter schema.
//
// The devDependency copy (node_modules/pi-subagents) is a hard requirement: its inventory must equal
// a fresh generation and must not be skipped. Older reviewed versions are verified when their
// package roots are supplied as PI_SUBAGENTS_ROOTS='{"0.74.0":"/path","0.75.0":"/path"}'; the test
// log names which were verified.
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  auditLaunchPlacement, COMPAT_DIR, generateInventory, inventoryPath, peerPiAi, serialiseInventory, subagentOptionKeys,
  subagentParamProperties,
} from "../gen_pi_subagents_inventory.mjs";
import { importInternal, isBundled, makeLoader, readPackage } from "../lib/pi_subagents_package.mjs";
import { useDecoyHome } from "./decoy_home.mjs";
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

test("the inventory records the package's own declared pi-ai peer, and a package that declares none cannot be recorded", async () => {
  const manifest = readPackage(devDependency).manifest;
  const inventory = await generateInventory(devDependency);
  assert.equal(inventory.peer_pi_ai, manifest.peerDependencies["@earendil-works/pi-ai"]);
  assert.match(inventory.peer_pi_ai, /^>=\d+\.\d+\.\d+$/);
  assert.equal(peerPiAi({ version: "9.9.9", peerDependencies: { "@earendil-works/pi-ai": ">=0.90.0" } }), ">=0.90.0");
  for (const peerDependencies of [undefined, {}, { "@earendil-works/pi-ai": "" }, { "@earendil-works/pi-ai": 90 }])
    assert.throws(() => peerPiAi({ version: "9.9.9", peerDependencies }), /declares no peerDependencies/);
});

test("generation never reads the operator's home: a decoy home with invalid agents changes nothing", async (t) => {
  useDecoyHome(t);
  const fresh = serialiseInventory(await generateInventory(devDependency));
  assert.equal(readFileSync(inventoryPath(COMPAT_DIR, declared), "utf8"), fresh);
  assert.ok(process.env.HOME?.includes("decoy-home-"), "the decoy was in force during the generation");
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
  for (const version of ["0.74.0", declared]) {
    assert.deepEqual(read(version).launch_forms, ["agent", "workflow"], version);
    assert.equal(read(version).supports.turnBudget, false, version);
    assert.equal(read(version).supports.toolBudget, true, version);
    assert.equal(read(version).launch_contract_version, 3, version);
  }
  assert.equal(readdirSync(COMPAT_DIR).some((name) => /pi-subagents-0\.(50|64)\.0\.json/.test(name)), false,
    "launch contract v2 (before 0.74.0) is out of the supported range");
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

// --- the compact (0.77+) schema: where each audit-launch parameter is declared ------------------------------

const AUDIT = ["agent", "task", "model", "agentScope", "async", "context", "acceptance", "timeoutMs", "toolBudget"];

test("a flat package (the devDependency, 0.76.1) has no option keys and so no placement field", async () => {
  assert.equal(await subagentOptionKeys(devDependency), undefined);
  assert.equal("launch_parameter_placement" in (await generateInventory(devDependency)), false);
  assert.equal(auditLaunchPlacement(AUDIT, undefined), undefined);
});

test("placement is read from the schema: top-level wins, otherwise the package's option keys, otherwise it throws", () => {
  const top = ["agent", "task", "model", "async", "options"];
  assert.deepEqual(auditLaunchPlacement(top, ["agentScope", "context", "acceptance", "timeoutMs", "toolBudget"]), {
    agent: "top-level", task: "top-level", model: "top-level", agentScope: "options", async: "top-level",
    context: "options", acceptance: "options", timeoutMs: "options", toolBudget: "options" });
  assert.deepEqual(auditLaunchPlacement([...top, "context"], ["agentScope", "acceptance", "timeoutMs", "toolBudget"]).context, "top-level");
  assert.throws(() => auditLaunchPlacement(top, ["agentScope", "context", "acceptance", "timeoutMs"]), /toolBudget/);
  assert.throws(() => auditLaunchPlacement(["task", "model", "async", "options"], ["agentScope", "context", "acceptance", "timeoutMs", "toolBudget"]), /agent/);
  assert.throws(() => auditLaunchPlacement(top.filter((name) => name !== "options"), ["agentScope", "context", "acceptance", "timeoutMs", "toolBudget"]), /options/);
});

test("a compact package's inventory records the union of top-level and option keys, in the field's canonical key order", async (t) => {
  const roots = process.env.PI_SUBAGENTS_ROOTS ? JSON.parse(process.env.PI_SUBAGENTS_ROOTS) : {};
  const compact = Object.entries(roots).filter(([version]) => Number(version.split(".")[1]) >= 77);
  for (const [version, root] of compact) {
    const inventory = await generateInventory(root);
    assert.deepEqual(Object.keys(inventory.launch_parameter_placement), AUDIT, version);
    assert.equal(inventory.supports.toolBudget && inventory.supports.timeoutMs, true, version);
    assert.equal(inventory.supports.disabledFeatures, true, version);
    assert.deepEqual(inventory.launch_forms, ["agent", "workflow"], version);
  }
  t.diagnostic(`compact packages checked: ${compact.map(([version]) => version).join(", ") || "(none supplied)"}`);
});

// --- the package layout: source before 0.81.0, a bundle of hashed chunks from 0.81.0 ---------------------------

function scratchPackage(t, files, manifest) {
  const root = mkdtempSync(path.join(os.tmpdir(), "layout-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  writeFileSync(path.join(root, "package.json"), JSON.stringify({ name: "pi-subagents", version: "9.9.9", type: "module", ...manifest }));
  for (const [file, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
    writeFileSync(path.join(root, file), text);
  }
  const pkg = readPackage(root);
  return { pkg, loader: makeLoader(path.join(root, "package.json")) };
}

test("a source layout is read from src/, a bundle from the chunk whose export block lists the names", async (t) => {
  const flat = scratchPackage(t, { "src/extension/schemas.js": "export const foo = 1;\n" }, { exports: { ".": "./index.js" } });
  assert.equal(isBundled(flat.pkg), false);
  assert.equal((await importInternal(flat.loader, flat.pkg, "src/extension/schemas", ["foo"])).foo, 1);
  assert.equal(await importInternal(flat.loader, flat.pkg, "src/extension/absent", ["foo"]), undefined);

  const bundled = scratchPackage(t, {
    // chunk-0 only imports the names (an import block is not an export block); chunk-a exports them; src/ must not be read.
    "bundle/chunk-0.js": 'import {\n  bar,\n  baz\n} from "./chunk-a.js";\nconst q = 0;\nexport {\n  q\n};\n',
    "bundle/chunk-a.js": "const bar = 1;\nconst baz = 2;\nexport {\n  bar,\n  baz\n};\n",
    "src/extension/schemas.js": "export const bar = 'from src';\nexport const baz = 'from src';\n",
  }, { exports: { ".": { default: "./bundle/index.js" } } });
  assert.equal(isBundled(bundled.pkg), true);
  const module = await importInternal(bundled.loader, bundled.pkg, "src/extension/schemas", ["bar", "baz"]);
  assert.deepEqual([module.bar, module.baz], [1, 2]);
  await assert.rejects(importInternal(bundled.loader, bundled.pkg, "src/extension/schemas", ["bar", "missing"]), /no chunk .* exports bar, missing/);
});
