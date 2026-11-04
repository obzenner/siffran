// pi-subagents 0.77.0 and later publish a compact `subagent` schema: `agent, task, workflow, args, async, model, cwd,
// worktree, output, action, id, message` at the top level and every other field inside one `options` object.
// These tests run against the schema and the launch preflight that real 0.77.0 and 0.84.0 packages served
// (test/fixtures/tool-schema-<version>.json, preflight-<version>.json, written by
// scripts/capture_pi_preflight_fixture.mjs): admission by observation, the reviewed records, the exact launch
// payload, the seam, and the classifier's view of `options`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import * as path from "node:path";

import { AUDIT_LAUNCH_POLICY } from "../src/host-profile.ts";
import { auditLaunchInput, placeAuditLaunch } from "../src/audit-launch.ts";
import { admitAuditPreflight, type AuditPreflightDescriptor } from "../src/preflight-seam.ts";
import { resolveOwnerInventory, type SubagentOwner } from "../src/runtime-owner.ts";
import {
  AUDIT_LAUNCH_PARAMETERS, COMPAT_DIR, launchPlacement, loadInventory, observeInventory, parseInventory,
  reviewedActionNames, type ReviewedInventory,
} from "../src/subagent-inventory.ts";
import { classifySubagentCall } from "../src/translate.ts";
import { AUDITOR, bound, fixtureFor, schemaCopy, TOOL_SCHEMAS, toolSchemaFor } from "./preflight-fixtures.ts";
import { reviewedVersions } from "./compat-versions.ts";
import { schemaDeclaring } from "./owner-fixture.ts";

const COMPACT = ["0.77.0", "0.84.0"] as const;
const PLACEMENT = {
  agent: "top-level", task: "top-level", model: "top-level", async: "top-level",
  agentScope: "options", context: "options", acceptance: "options", timeoutMs: "options", toolBudget: "options",
} as const;
const known = reviewedActionNames();
const observe = (version: string, parameters: unknown) => observeInventory(version, parameters, known);
const detail = (result: ReturnType<typeof observeInventory>) => result.ok ? "(admitted)" : result.detail;

test("the captured compact schemas are what the package serves: the audit's bound fields sit under options", () => {
  assert.deepEqual(TOOL_SCHEMAS.map((fixture) => fixture.version),
    ["0.77.0", "0.78.0", "0.79.0", "0.79.1", "0.79.2", "0.80.0", "0.81.0", "0.82.0", "0.82.1", "0.83.0", "0.83.1", "0.84.0"]);
  for (const version of COMPACT) {
    const { parameters, option_keys } = toolSchemaFor(version);
    const top = Object.keys(parameters.properties);
    assert.deepEqual(top.filter((name) => (AUDIT_LAUNCH_PARAMETERS as readonly string[]).includes(name)),
      ["agent", "task", "async", "model"], version);
    for (const name of ["agentScope", "context", "acceptance", "timeoutMs", "toolBudget"]) {
      assert.equal(top.includes(name), false, `${version}: ${name} is not top-level`);
      assert.ok(option_keys.includes(name), `${version}: ${name} is in SUBAGENT_OPTION_KEYS`);
    }
    assert.equal(parameters.properties.options.type, "object");
  }
});

test("observed admission: the real compact schema is admitted, each parameter placed where the schema declares it", () => {
  for (const version of COMPACT) {
    const admitted = observe(version, toolSchemaFor(version).parameters);
    assert.equal(admitted.ok, true, detail(admitted));
    if (!admitted.ok) continue;
    const inventory = admitted.inventory;
    assert.equal(inventory.reviewed, false);
    assert.equal(inventory.launch_contract_version, null, "unknown until the live preflight answers");
    assert.deepEqual(inventory.launch_parameter_placement, PLACEMENT, version);
    assert.deepEqual(inventory.launch_forms, ["agent", "workflow"], "a launch form is a top-level parameter");
    assert.equal(inventory.supports.timeoutMs && inventory.supports.toolBudget, true);
    assert.equal(inventory.supports.validateOffline, false, "nothing documents it as offline for an unreviewed version");
  }
});

test("observed admission of a flat schema is unchanged: every parameter top-level, no placement recorded as options", () => {
  const admitted = observe("0.99.0", schemaDeclaring(AUDIT_LAUNCH_PARAMETERS));
  assert.equal(admitted.ok, true);
  if (admitted.ok) assert.deepEqual(launchPlacement(admitted.inventory),
    Object.fromEntries(AUDIT_LAUNCH_PARAMETERS.map((name) => [name, "top-level"])));
});

test("a flat schema missing agentScope is refused naming it (the negative control)", () => {
  const properties = Object.fromEntries(AUDIT_LAUNCH_PARAMETERS.filter((name) => name !== "agentScope").map((name) => [name, { type: "string" }]));
  const refused = observe("0.99.0", { type: "object", properties });
  assert.equal(refused.ok, false);
  assert.match(detail(refused), /does not declare `agentScope`/);
});

test("a compact schema is refused when it lacks options, or an object-typed options, naming the shape", () => {
  const noOptions = schemaCopy("0.84.0");
  delete noOptions.properties.options;
  const refused = observe("0.99.0", noOptions);
  assert.equal(refused.ok, false);
  assert.match(detail(refused), /does not declare `agentScope`/);
  assert.match(detail(refused), /`options`/, "names the options object that could have carried it");
  for (const options of [{ type: "string" }, { type: "array" }, { description: "no type" }, true, null, "object"]) {
    const broken = schemaCopy("0.84.0");
    broken.properties.options = options;
    const result = observe("0.99.0", broken);
    assert.equal(result.ok, false, JSON.stringify(options));
    assert.match(detail(result), /`options` but not as an object schema/, JSON.stringify(options));
  }
});

test("a compact schema is refused when it lacks agent, task, model or async, naming the missing one", () => {
  for (const name of ["agent", "task", "model", "async"]) {
    const broken = schemaCopy("0.84.0");
    delete broken.properties[name];
    const result = observe("0.99.0", broken);
    assert.equal(result.ok, false, name);
    assert.match(detail(result), new RegExp(`does not declare \`${name}\``), name);
  }
});

test("the compact shape defers the options-carried parameters to the live preflight; anything else with no schema is refused", () => {
  // `options` is an opaque bag in the published schema, so the parameters it carries cannot be seen there; the seam
  // (context, tools, model, foreground) is what then confirms the launch, and the executor rejects an unknown key.
  assert.equal(observe("0.99.0", schemaCopy("0.84.0")).ok, true);
  assert.equal(observe("0.99.0", { type: "object" }).ok, false);
  assert.equal(observe("0.99.0", null).ok, false);
  assert.match(detail(observe("0.99.0", { type: "object", properties: [] })), /no parameter schema/);
});

test("reviewed records for 0.77.0 and 0.84.0 declare where each launch parameter lives", () => {
  for (const version of COMPACT) {
    const inventory = loadInventory(version)!;
    assert.deepEqual(inventory.launch_parameter_placement, PLACEMENT, version);
    assert.equal(inventory.launch_contract_version, 3);
    assert.equal(inventory.supports.toolBudget && inventory.supports.timeoutMs, true);
  }
  assert.equal(loadInventory("0.76.1")!.launch_parameter_placement, undefined, "an old record has no such field");
  assert.ok(Object.values(launchPlacement(loadInventory("0.76.1")!)).every((place) => place === "top-level"));
});

test("a reviewed record whose agentScope is declared under options is admitted by the owner resolution", () => {
  for (const version of COMPACT) {
    const owner: SubagentOwner = { owner_path: "/p/index.js", package_root: "/p", version, source: "npm:pi-subagents",
      parameters: toolSchemaFor(version).parameters };
    const resolved = resolveOwnerInventory(owner, (v) => loadInventory(v));
    assert.equal(resolved.ok, true, version);
    if (resolved.ok) {
      assert.equal(resolved.inventory.reviewed, true);
      assert.equal(launchPlacement(resolved.inventory).agentScope, "options");
    }
  }
});

test("every reviewed version 0.77.0..0.84.0 has a record, a captured preflight, and the same placement", () => {
  const reviewed = reviewedVersions();
  const expected = ["0.77.0", "0.78.0", "0.79.0", "0.79.1", "0.79.2", "0.80.0", "0.81.0", "0.82.0", "0.82.1", "0.83.0", "0.83.1", "0.84.0"];
  for (const version of expected) {
    assert.ok(reviewed.includes(version), `${version} is reviewed`);
    assert.deepEqual(loadInventory(version)!.launch_parameter_placement, PLACEMENT, version);
    assert.equal(fixtureFor(version).version, version);
  }
});

// --- parseInventory stays strict ------------------------------------------------------------------------

const shipped = (version: string): Record<string, unknown> =>
  JSON.parse(readFileSync(path.join(COMPAT_DIR, `pi-subagents-${version}.json`), "utf8"));

test("parseInventory: an old record without the field stays valid; a well-formed field is kept", () => {
  assert.doesNotThrow(() => parseInventory(shipped("0.76.1")));
  const parsed = parseInventory(shipped("0.84.0")) as Omit<ReviewedInventory, "reviewed">;
  assert.deepEqual(parsed.launch_parameter_placement, PLACEMENT);
});

test("parseInventory: a malformed placement field is a defect that throws", () => {
  const base = shipped("0.84.0");
  const placement = base.launch_parameter_placement as Record<string, string>;
  const { agentScope: _drop, ...missing } = placement;
  const cases: Array<[string, unknown, RegExp]> = [
    ["not an object", "options", /launch_parameter_placement must be an object/],
    ["null", null, /launch_parameter_placement must be an object/],
    ["a list", [], /launch_parameter_placement must be an object/],
    ["a missing parameter", missing, /missing "agentScope"/],
    ["an unknown parameter", { ...placement, turnBudget: "options" }, /unknown field "turnBudget"/],
    ["an unknown place", { ...placement, agentScope: "nested" }, /agentScope must be top-level or options/],
    ["a non-string place", { ...placement, context: 1 }, /context must be top-level or options/],
    ["agent under options", { ...placement, agent: "options" }, /agent must be top-level/],
    ["task under options", { ...placement, task: "options" }, /task must be top-level/],
    ["model under options", { ...placement, model: "options" }, /model must be top-level/],
    ["async under options", { ...placement, async: "options" }, /async must be top-level/],
  ];
  for (const [label, value, pattern] of cases)
    assert.throws(() => parseInventory({ ...base, launch_parameter_placement: value }), pattern, label);
  assert.throws(() => parseInventory({ ...shipped("0.76.1"), launch_parameter_placement: undefined }), /launch_parameter_placement must be an object/,
    "present-but-undefined is not absent once serialised as a key");
});

test("every checked-in record that carries the field carries it for exactly the versions whose schema is compact", () => {
  for (const file of readdirSync(COMPAT_DIR).filter((name) => name.endsWith(".json"))) {
    const record = JSON.parse(readFileSync(path.join(COMPAT_DIR, file), "utf8")) as Record<string, unknown>;
    const version = String(record.version);
    const [major, minor] = version.split(".").map(Number);
    assert.equal("launch_parameter_placement" in record, major > 0 || minor >= 77, file);
  }
});

// --- the launch payload ---------------------------------------------------------------------------------

const DECISION = { task: "THE DOSSIER", model: "audit/reviewer", agentScope: "project", policy: AUDIT_LAUNCH_POLICY } as const;
const ACCEPTANCE = { level: "none", reason: "Empirica's bound canonical auditor is read-only and has its own verdict contract." };
const BOUND = { timeoutMs: 900_000, toolBudget: { soft: 20, hard: 30, block: ["read", "grep", "find", "ls"] } };

test("the launch payload for a compact version is exact: core fields top-level, the rest under options, never both", () => {
  for (const version of COMPACT) {
    const placed = placeAuditLaunch(auditLaunchInput(DECISION), loadInventory(version)!);
    assert.deepEqual(placed, {
      task: "THE DOSSIER", model: "audit/reviewer", async: false,
      options: { agentScope: "project", context: "fresh", acceptance: ACCEPTANCE, ...BOUND },
    }, version);
    assert.deepEqual(Object.keys(placed), ["task", "model", "async", "options"], "no bound field at the top level");
    assert.deepEqual(Object.keys(placed.options as object).sort(),
      ["acceptance", "agentScope", "context", "timeoutMs", "toolBudget"]);
  }
});

test("the launch payload for an observed compact version is the same exact payload", () => {
  const admitted = observe("0.99.0", toolSchemaFor("0.84.0").parameters);
  assert.equal(admitted.ok, true);
  if (admitted.ok) assert.deepEqual(placeAuditLaunch(auditLaunchInput(DECISION), admitted.inventory),
    placeAuditLaunch(auditLaunchInput(DECISION), loadInventory("0.84.0")!));
});

test("the launch payload for a flat version is the unchanged flat payload (0.74.0..0.76.1)", () => {
  for (const version of ["0.74.0", "0.75.0", "0.76.1"]) {
    const flat = auditLaunchInput(DECISION);
    const placed = placeAuditLaunch(flat, loadInventory(version)!);
    assert.deepEqual(placed, flat, version);
    assert.deepEqual(Object.keys(placed).sort(),
      ["acceptance", "agentScope", "async", "context", "model", "task", "timeoutMs", "toolBudget"], version);
    assert.equal("options" in placed, false);
  }
});

test("placing a field the audit launch does not know is a defect, not a silent top-level write", () => {
  assert.throws(() => placeAuditLaunch({ turnBudget: { maxTurns: 8 } }, loadInventory("0.84.0")!), /turnBudget/);
});

// --- the preflight seam, against the captured real preflight --------------------------------------------

function descriptor(version: string, overrides: Partial<AuditPreflightDescriptor> = {}): AuditPreflightDescriptor {
  return {
    version, inventory: loadInventory(version),
    expected: { agent_file_path: AUDITOR, model: "audit/reviewer", child_tools: AUDIT_LAUNCH_POLICY.audit_child_tools },
    launch: { async: false, output_mode: undefined, force_top_level_async: undefined, context: "fresh" },
    canonical: (file) => path.resolve(file),
    ...overrides,
  };
}
const code = (admission: ReturnType<typeof admitAuditPreflight>) =>
  admission.kind === "refuse" ? admission.code : `accepted ${JSON.stringify(admission.contract)}`;

test("the seam admits the captured 0.77.0 and 0.84.0 preflight, and the flat view of the placed launch is what it checks", () => {
  for (const version of COMPACT) {
    const response = bound(fixtureFor(version).cases.canonical_auditor);
    assert.equal(response.contract.version, 3);
    assert.equal(response.contract.protocol.packageVersion, version);
    assert.deepEqual(response.contract.tools.effectiveAllowlist, ["read", "grep", "find", "ls", "contact_supervisor"]);
    assert.equal(response.contract.context, "fresh");
    assert.deepEqual(admitAuditPreflight(descriptor(version), response), { kind: "accept", contract: {
      agent_file_path: AUDITOR, model: "audit/reviewer", audit_retrieval: ["local", "workspace"] } }, version);
  }
});

test("the seam refuses the captured 0.84.0 preflight when it is tampered: agent file, model, tools, async, context", () => {
  const response = () => bound(fixtureFor("0.84.0").cases.canonical_auditor);
  const shadowed = response(); shadowed.contract.agent.filePath = "/elsewhere/empirica-auditor.md";
  assert.equal(code(admitAuditPreflight(descriptor("0.84.0"), shadowed)), "agent-not-canonical");
  const swapped = response(); swapped.contract.model = "other/model:high";
  assert.equal(code(admitAuditPreflight(descriptor("0.84.0"), swapped)), "model-substituted");
  const writing = response(); writing.contract.tools.effectiveAllowlist.push("write");
  assert.equal(code(admitAuditPreflight(descriptor("0.84.0"), writing)), "tools-not-read-only");
  for (const async of [true, undefined, "false"])
    assert.equal(code(admitAuditPreflight(descriptor("0.84.0", { launch: {
      async, output_mode: undefined, force_top_level_async: undefined, context: "fresh" } }), response())), "not-foreground", String(async));
  const forked = response(); forked.contract.context = "fork";
  assert.equal(code(admitAuditPreflight(descriptor("0.84.0"), forked)), "context-not-fresh");
  assert.equal(code(admitAuditPreflight(descriptor("0.84.0", { launch: {
    async: false, output_mode: "file-only", force_top_level_async: undefined, context: "fresh" } }), response())), "output-mode-not-inline");
});

test("the seam refuses a 0.77.0 contract presented for 0.84.0 (the contract must name its own owner)", () => {
  const response = bound(fixtureFor("0.77.0").cases.canonical_auditor);
  assert.equal(code(admitAuditPreflight(descriptor("0.84.0"), response)), "owner-version-mismatch");
});

// --- the classifier sees `options` ---------------------------------------------------------------------

test("a compact version: a launch form hidden in options, or options that is not an object, is malformed", () => {
  for (const version of COMPACT) {
    const inventory = loadInventory(version)!;
    const classify = (input: unknown) => classifySubagentCall(input, inventory);
    assert.equal(classify({ agent: "x", task: "t", options: { context: "fresh", timeoutMs: 5 } }).kind, "executable", version);
    assert.equal(classify({ agent: "x", task: "t" }).kind, "executable");
    assert.equal(classify({ agent: "x", options: { script: "runs.run('a',{agent:'x',task:'t'})" } }).kind, "malformed");
    assert.equal(classify({ options: { script: "x" } }).kind, "malformed", "a script launch is not reachable through options");
    assert.equal(classify({ workflow: "parallel", options: { script: "x" } }).kind, "malformed");
    for (const form of ["workflowScript", "workflowScriptPath", "resume", "workflow", "agent"])
      assert.equal(classify({ agent: "x", options: { [form]: "x" } }).kind, "malformed", form);
    for (const options of ["{}", [], 3, true])
      assert.equal(classify({ agent: "x", task: "t", options }).kind, "malformed", JSON.stringify(options));
    assert.equal(classify({ agent: "x", task: "t", options: null }).kind, "executable", "null is absent, as for every launch form");
    assert.equal(classify({ action: "list", options: { view: "fleet" } }).kind, "management");
    assert.equal(classify({ action: "list", options: { script: "x" } }).kind, "malformed");
  }
});

test("a flat version classifies exactly as before: options is not inspected", () => {
  const inventory = loadInventory("0.76.1")!;
  assert.equal(classifySubagentCall({ agent: "x", task: "t", options: { script: "x" } }, inventory).kind, "executable");
  assert.equal(classifySubagentCall({ agent: "x", options: "nonsense" }, inventory).kind, "executable");
});
