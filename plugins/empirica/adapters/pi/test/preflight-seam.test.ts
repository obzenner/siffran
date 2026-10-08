// The preflight seam against responses captured from real pi-subagents packages
// (test/fixtures/preflight-<version>.json, written by scripts/capture_pi_preflight_fixture.mjs) and
// the policy cases shared with the Python identity test (tests/fixtures/thinking-level-cases.json).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { AUDIT_LAUNCH_POLICY, THINKING_LEVELS } from "../src/host-profile.ts";
import {
  ACCEPTED_LAUNCH_CONTRACT_VERSIONS, admitAuditPreflight, withoutThinkingLevel,
  type AuditPreflightDescriptor,
} from "../src/preflight-seam.ts";
import { AUDIT_LAUNCH_PARAMETERS, COMPAT_DIR, loadInventory, observeInventory, reviewedActionNames } from "../src/subagent-inventory.ts";
import { schemaDeclaring } from "./owner-fixture.ts";
import { reviewedVersions } from "./compat-versions.ts";
import { AUDITOR, bound, FIXTURES } from "./preflight-fixtures.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, "..", "..", "..");
const VERSIONS = FIXTURES.map((fixture) => fixture.version);

function descriptor(version: string, overrides: Partial<AuditPreflightDescriptor> = {}): AuditPreflightDescriptor {
  return {
    version, inventory: loadInventory(version),
    expected: { agent_file_path: AUDITOR, model: "audit/reviewer", child_tools: AUDIT_LAUNCH_POLICY.audit_child_tools },
    launch: { async: false, output_mode: undefined, force_top_level_async: undefined, context: "fresh" },
    canonical: (file) => path.resolve(file),
    ...overrides,
  };
}

const refusal = (admission: ReturnType<typeof admitAuditPreflight>) =>
  admission.kind === "refuse" ? admission.code : `accepted ${JSON.stringify(admission.contract)}`;

test("fixtures exist for both launch-contract generations, each with a reviewed inventory", () => {
  assert.deepEqual(VERSIONS, reviewedVersions(), "a fixture for exactly every reviewed version");
  for (const fixture of FIXTURES) {
    assert.ok(loadInventory(fixture.version), `no inventory for ${fixture.version}`);
    assert.ok(ACCEPTED_LAUNCH_CONTRACT_VERSIONS.includes(bound(fixture.cases.canonical_auditor).contract.version));
  }
  assert.deepEqual(readdirSync(COMPAT_DIR).filter((f) => f.endsWith(".json")).sort(),
    VERSIONS.map((version) => `pi-subagents-${version}.json`));
});

test("launch contract v2 (0.50, 0.64) and v3 (0.74, 0.75) differ exactly where the seam branches", () => {
  for (const fixture of FIXTURES) {
    const contract = bound(fixture.cases.canonical_auditor).contract;
    const v3 = contract.version === 3;
    assert.equal(contract.version, loadInventory(fixture.version)!.launch_contract_version, fixture.version);
    assert.equal("modelCandidates" in contract, !v3, `${fixture.version}: modelCandidates is v2-only`);
    assert.equal("intercomBridge" in contract, v3, `${fixture.version}: intercomBridge is v3-only`);
    assert.equal(contract.agent.definitionProjectionVersion, v3 ? 2 : 1, fixture.version);
    assert.equal(contract.protocol.packageVersion, fixture.version);
    assert.equal(contract.model, "audit/reviewer:high", "both generations append the auditor's thinking level");
  }
});

test("the canonical auditor is accepted from every captured package, thinking level stripped", () => {
  for (const fixture of FIXTURES) {
    const admission = admitAuditPreflight(descriptor(fixture.version), bound(fixture.cases.canonical_auditor));
    assert.deepEqual(admission, { kind: "accept", contract: {
      agent_file_path: AUDITOR, model: "audit/reviewer", audit_retrieval: ["local", "workspace"] } }, fixture.version);
  }
});

test("a v3 contract has no modelCandidates and the seam reads contract.model; v2 reads modelCandidates[0]", () => {
  for (const fixture of FIXTURES) {
    const response = bound(fixture.cases.canonical_auditor);
    const v2 = response.contract.version === 2;
    if (v2) { response.contract.model = "decoy/model:high"; response.contract.modelCandidates = ["audit/reviewer:xhigh"]; }
    else { response.contract.model = "audit/reviewer:xhigh"; response.contract.modelCandidates = ["decoy/model:high"]; }
    assert.deepEqual(admitAuditPreflight(descriptor(fixture.version), response),
      { kind: "accept", contract: { agent_file_path: AUDITOR, model: "audit/reviewer",
        audit_retrieval: ["local", "workspace"] } }, fixture.version);
  }
});

test("a captured preflight refusal (unknown agent) is reported as preflight-refused with the runtime's message", () => {
  for (const fixture of FIXTURES) {
    const admission = admitAuditPreflight(descriptor(fixture.version), bound(fixture.cases.unknown_agent));
    assert.equal(admission.kind, "refuse");
    if (admission.kind === "refuse") {
      assert.equal(admission.code, "preflight-refused");
      assert.match(admission.detail, /Unknown agent: empirica\.empirica-auditor/);
    }
  }
});

test("a model the registry cannot serve makes the real preflight throw; there is no response to admit", () => {
  for (const fixture of FIXTURES) {
    const captured = fixture.cases.unservable_model;
    assert.equal(captured.response, undefined, fixture.version);
    assert.match(captured.threw ?? "", /Unknown subagent model 'missing\/reviewer'/, fixture.version);
  }
});

test("the seam refuses a substituted model, with or without a thinking level", () => {
  for (const fixture of FIXTURES) {
    const response = bound(fixture.cases.canonical_auditor);
    for (const configured of ["audit/other", "other/reviewer", "audit/reviewer:0"])
      assert.equal(refusal(admitAuditPreflight(
        descriptor(fixture.version, { expected: { agent_file_path: AUDITOR, model: configured, child_tools: AUDIT_LAUNCH_POLICY.audit_child_tools } }), response)),
      "model-substituted", `${fixture.version} ${configured}`);
    // A configured level is irrelevant to identity: the model, not its level, is compared.
    assert.equal(admitAuditPreflight(descriptor(fixture.version,
      { expected: { agent_file_path: AUDITOR, model: "audit/reviewer:max", child_tools: AUDIT_LAUNCH_POLICY.audit_child_tools } }), response).kind, "accept");
  }
});

test("the seam refuses a non-canonical auditor file (shadowing) and a missing file path", () => {
  for (const fixture of FIXTURES) {
    const shadowed = bound(fixture.cases.canonical_auditor);
    shadowed.contract.agent.filePath = "/elsewhere/installed/empirica-auditor.md";
    assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version), shadowed)), "agent-not-canonical");
    const anonymous = bound(fixture.cases.canonical_auditor);
    delete anonymous.contract.agent.filePath;
    assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version), anonymous)), "malformed-response");
  }
});

test("the seam canonicalises through the injected function, not string equality", () => {
  const fixture = FIXTURES.at(-1)!;
  const response = bound(fixture.cases.canonical_auditor);
  response.contract.agent.filePath = "/link/empirica-auditor.md";
  const canonical = (file: string) => file === "/link/empirica-auditor.md" ? AUDITOR : file;
  assert.equal(admitAuditPreflight(descriptor(fixture.version, { canonical }), response).kind, "accept");
});

test("the seam refuses a missing or unreviewed owner version and an inventory for another version", () => {
  const fixture = FIXTURES[0];
  const response = bound(fixture.cases.canonical_auditor);
  assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version, { version: undefined }), response)), "owner-unverified");
  assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version, { version: "" }), response)), "owner-unverified");
  assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version, { inventory: undefined }), response)), "owner-unverified");
  assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version, { inventory: loadInventory("0.64.0") }), response)),
    "owner-unverified");
});

test("the seam refuses a launch contract the inventory does not vouch for", () => {
  const v2 = FIXTURES[0], v3 = FIXTURES.at(-1)!;
  // A v3 answer from a package reviewed as v2, a v2 answer from one reviewed as v3, and unknown versions.
  const v3Response = bound(v3.cases.canonical_auditor);
  v3Response.contract.protocol.packageVersion = v2.version;
  assert.equal(refusal(admitAuditPreflight(descriptor(v2.version), v3Response)), "launch-contract-version");
  const v2Response = bound(v2.cases.canonical_auditor);
  v2Response.contract.protocol.packageVersion = v3.version;
  assert.equal(refusal(admitAuditPreflight(descriptor(v3.version), v2Response)), "launch-contract-version");
  for (const version of [1, 4, "3", null, undefined]) {
    const response = bound(v3.cases.canonical_auditor);
    response.contract.version = version;
    assert.equal(refusal(admitAuditPreflight(descriptor(v3.version), response)), "launch-contract-version", String(version));
  }
});

test("the seam refuses a contract that reports another package version than the bound owner", () => {
  for (const reported of ["0.0.1", undefined]) {
    const response = bound(FIXTURES.at(-1)!.cases.canonical_auditor);
    response.contract.protocol = reported === undefined ? {} : { ...response.contract.protocol, packageVersion: reported };
    assert.equal(refusal(admitAuditPreflight(descriptor(FIXTURES.at(-1)!.version), response)), "owner-version-mismatch");
  }
  const noProtocol = bound(FIXTURES.at(-1)!.cases.canonical_auditor);
  delete noProtocol.contract.protocol;
  assert.equal(refusal(admitAuditPreflight(descriptor(FIXTURES.at(-1)!.version), noProtocol)), "owner-version-mismatch");
});

test("the seam refuses forced-async, non-foreground, and non-inline launch requests", () => {
  const fixture = FIXTURES.at(-1)!;
  const response = bound(fixture.cases.canonical_auditor);
  const launch = (overrides: Partial<AuditPreflightDescriptor["launch"]>) =>
    descriptor(fixture.version, { launch: { async: false, output_mode: undefined, force_top_level_async: undefined, context: "fresh", ...overrides } });
  assert.equal(refusal(admitAuditPreflight(launch({ force_top_level_async: true }), response)), "forced-async");
  for (const value of [true, undefined, null, "false", 0])
    assert.equal(refusal(admitAuditPreflight(launch({ async: value }), response)), "not-foreground", String(value));
  assert.equal(refusal(admitAuditPreflight(launch({ output_mode: "file-only" }), response)), "output-mode-not-inline");
  assert.equal(refusal(admitAuditPreflight(launch({ output_mode: "other" }), response)), "output-mode-not-inline");
  assert.equal(admitAuditPreflight(launch({ output_mode: "inline", force_top_level_async: false }), response).kind, "accept");
});

test("EHB-AUDLAUNCH-02: the seam refuses a launch whose context is not fresh, and a preflight that resolved another context", () => {
  const fixture = FIXTURES.at(-1)!;
  const response = bound(fixture.cases.canonical_auditor);
  const launch = (overrides: Partial<AuditPreflightDescriptor["launch"]>) =>
    descriptor(fixture.version, { launch: { async: false, output_mode: undefined, force_top_level_async: undefined, context: "fresh", ...overrides } });
  for (const value of [undefined, "fork", "profile", null, ""])
    assert.equal(refusal(admitAuditPreflight(launch({ context: value }), response)), "context-not-fresh", String(value));
  assert.equal(admitAuditPreflight(launch({ context: "fresh" }), response).kind, "accept");
  for (const fixture of FIXTURES) {
    const forked = bound(fixture.cases.canonical_auditor);
    forked.contract.context = "fork";
    assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version), forked)), "context-not-fresh", fixture.version);
    const silent = bound(fixture.cases.canonical_auditor);
    delete silent.contract.context;
    assert.equal(refusal(admitAuditPreflight(descriptor(fixture.version), silent)), "context-not-fresh", `${fixture.version} reports no context`);
  }
});

test("the seam refuses responses that are not a preflight answer", () => {
  const fixture = FIXTURES.at(-1)!;
  const noModel = bound(fixture.cases.canonical_auditor);
  delete noModel.contract.model;
  const noCandidates = bound(FIXTURES[0].cases.canonical_auditor);
  delete noCandidates.contract.modelCandidates;
  const emptyCandidates = bound(FIXTURES[0].cases.canonical_auditor);
  emptyCandidates.contract.modelCandidates = [];
  for (const [label, response, version] of [
    ["null", null, fixture.version], ["array", [], fixture.version], ["string", "ok", fixture.version],
    ["no ok", { contract: {} }, fixture.version], ["ok true without contract", { ok: true }, fixture.version],
    ["contract is a list", { ok: true, contract: [] }, fixture.version],
    ["v3 without model", noModel, fixture.version],
    ["v2 without modelCandidates", noCandidates, FIXTURES[0].version],
    ["v2 with empty modelCandidates", emptyCandidates, FIXTURES[0].version],
  ] as const)
    assert.equal(refusal(admitAuditPreflight(descriptor(version), response)), "malformed-response", label);
});

// --- thinking levels: contract data, shared policy cases ----------------------------------

interface LevelCase { id: string; provider: string; model_id: string; strips_level: string | null;
  without_thinking_level: string; python_identity: string | null }
const LEVEL_CASES = (JSON.parse(readFileSync(path.join(PLUGIN, "tests", "fixtures", "thinking-level-cases.json"), "utf8")) as
  { cases: LevelCase[] }).cases;

test("withoutThinkingLevel matches every shared policy case", () => {
  for (const row of LEVEL_CASES)
    assert.equal(withoutThinkingLevel(`${row.provider}/${row.model_id}`), `${row.provider}/${row.without_thinking_level}`, row.id);
});

test("the shared cases cover exactly the contract's thinking levels and are self-consistent", () => {
  assert.deepEqual(new Set(LEVEL_CASES.flatMap((row) => row.strips_level === null ? [] : [row.strips_level])), THINKING_LEVELS);
  for (const row of LEVEL_CASES) {
    const expected = row.strips_level === null ? row.model_id : row.model_id.slice(0, -(row.strips_level.length + 1));
    assert.equal(row.without_thinking_level, expected, row.id);
    if (row.strips_level !== null) assert.ok(row.model_id.endsWith(`:${row.strips_level}`), row.id);
  }
});

test("withoutThinkingLevel takes its levels from the data it is given, not from a built-in list", () => {
  assert.equal(withoutThinkingLevel("a/b:turbo", new Set(["turbo"])), "a/b");
  assert.equal(withoutThinkingLevel("a/b:high", new Set(["turbo"])), "a/b:high");
  assert.equal(withoutThinkingLevel("a/b:high", new Set()), "a/b:high");
});

test("an auditor given any tool outside audit_child_tools is refused for every version, reviewed or observed", () => {
  const observed = observeInventory("0.99.0", schemaDeclaring(AUDIT_LAUNCH_PARAMETERS), reviewedActionNames());
  assert.equal(observed.ok, true);
  const inventories = [...VERSIONS.map((version) => ({ version, inventory: loadInventory(version) })),
    { version: "0.99.0", inventory: observed.ok ? observed.inventory : undefined }];
  for (const { version, inventory } of inventories) {
    const fixture = FIXTURES.find((f) => f.version === (version === "0.99.0" ? "0.76.1" : version))!;
    for (const extra of ["edit", "write", "bash", "subagent"]) {
      const response = bound(fixture.cases.canonical_auditor);
      response.contract.protocol.packageVersion = version;
      response.contract.tools.effectiveAllowlist = [...response.contract.tools.effectiveAllowlist, extra];
      const admission = admitAuditPreflight(descriptor(version, { inventory }), response);
      assert.equal(refusal(admission), "tools-not-read-only", `${version} + ${extra}`);
      if (admission.kind === "refuse") assert.match(admission.detail, new RegExp(`"${extra}"`));
    }
  }
});

test("the five-tool set the 0.74/0.75/0.76.1 packages emit (with contact_supervisor) is admitted", () => {
  for (const fixture of FIXTURES.filter((f) => ["0.74.0", "0.75.0", "0.76.1"].includes(f.version))) {
    const response = bound(fixture.cases.canonical_auditor);
    assert.deepEqual(response.contract.tools.effectiveAllowlist, ["read", "grep", "find", "ls", "contact_supervisor"]);
    assert.equal(admitAuditPreflight(descriptor(fixture.version), response).kind, "accept", fixture.version);
  }
});

// ADR-0069: a version with no compat record is admitted by the seam alone, so each property of the preflight and
// of the launch request must refuse it with its own code.
test("an observed inventory (no compat record) is admitted by a good preflight and refused for each broken property", () => {
  const observed = observeInventory("0.99.0", schemaDeclaring(AUDIT_LAUNCH_PARAMETERS), reviewedActionNames());
  assert.equal(observed.ok, true);
  if (!observed.ok) return;
  const inventory = observed.inventory;
  assert.equal(inventory.launch_contract_version, null);
  const good = () => { const response = bound(FIXTURES.at(-1)!.cases.canonical_auditor); response.contract.protocol.packageVersion = "0.99.0"; return response; };
  const launch = (patch: Partial<AuditPreflightDescriptor["launch"]>) =>
    descriptor("0.99.0", { inventory, launch: { async: false, output_mode: undefined, force_top_level_async: undefined, context: "fresh", ...patch } });
  assert.equal(admitAuditPreflight(descriptor("0.99.0", { inventory }), good()).kind, "accept");
  const cases: Array<[string, AuditPreflightDescriptor, unknown, string]> = [
    ["async launch", launch({ async: true }), good(), "not-foreground"],
    ["forced async", launch({ force_top_level_async: true }), good(), "forced-async"],
    ["output not inline", launch({ output_mode: "file" }), good(), "output-mode-not-inline"],
    ["launch context fork", launch({ context: "fork" }), good(), "context-not-fresh"],
    ["response context fork", launch({}), Object.assign(good(), { contract: { ...good().contract, context: "fork" } }), "context-not-fresh"],
    ["unknown contract version", launch({}), Object.assign(good(), { contract: { ...good().contract, version: 9 } }), "launch-contract-version"],
    ["package version differs", launch({}), (() => { const r = good(); r.contract.protocol.packageVersion = "0.98.0"; return r; })(), "owner-version-mismatch"],
    ["shadowed auditor", launch({}), (() => { const r = good(); r.contract.agent.filePath = "/shadow/auditor.md"; return r; })(), "agent-not-canonical"],
    ["substituted model", descriptor("0.99.0", { inventory, expected: { agent_file_path: AUDITOR, model: "other/model", child_tools: AUDIT_LAUNCH_POLICY.audit_child_tools } }), good(), "model-substituted"],
    ["a writing tool", launch({}), (() => { const r = good(); r.contract.tools.effectiveAllowlist.push("bash"); return r; })(), "tools-not-read-only"],
    ["preflight refused", launch({}), { ok: false, message: "no" }, "preflight-refused"],
    ["not an object", launch({}), "nope", "malformed-response"],
  ];
  for (const [label, d, response, code] of cases) assert.equal(refusal(admitAuditPreflight(d, response)), code, label);
});
