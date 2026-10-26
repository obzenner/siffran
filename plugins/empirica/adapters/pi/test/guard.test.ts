// Guard mutation matrix (D6-C C3). Proves that every malformed, partial,
// unknown, or mismatched response is rejected by the central inbound runtime
// guard — so a malformed Allow/Inert/Block/Fault can never permit the
// report_convergence hard gate. The guard is the trust boundary: anything it
// does not validate throws, and the hard gate catches and fails closed.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  allowRunStatuses, assertResponse, DIGEST, FAIL_DIRECTIONS, FAULT_CODES, GuardError, INERT_REASONS, RUN_STATUSES,
  SPIKE_BACKEND_KINDS, SPIKE_PROFILE_IDS, spikeBackendChecks,
} from "../src/guard.ts";
import {
  PROTOCOL, type FailDirection, type FaultCode, type InertReason, type Response, type RunStatus, type SpikeBackend,
  type SpikeProfile,
} from "../src/contract.ts";

const RID = "rid-1";
// The raw vendored schemas and the repository fixtures, read independently of the code under test.
const PLUGIN = join(import.meta.dirname, "..", "..", "..");
const VENDORED = join(PLUGIN, "vendor", "contracts", "empirica", "v2");
const readJson = (file: string) => JSON.parse(readFileSync(file, "utf8"));
const RESPONSE = readJson(join(VENDORED, "response.schema.json"));
const DEFS = RESPONSE.$defs;
const FIXTURES = join(PLUGIN, "..", "..", "contracts", "empirica", "v2", "fixtures");

function ok(result: Response["result"]): Response {
  return { protocol: PROTOCOL, request_id: RID, result };
}

function run(status: RunStatus = "active") {
  return { id: "run-1", status, spike_backend: { kind: "spikes_unavailable" as const } };
}

function expectThrow(label: string, response: unknown, rid = RID): void {
  test(`guard rejects: ${label}`, () => {
    assert.throws(
      () => assertResponse(response, rid),
      (e: unknown) => e instanceof GuardError,
    );
  });
}

// --- protocol / request_id / structure ---------------------------------------

expectThrow("not an object", null);
expectThrow("not an object (string)", "hello");
expectThrow("wrong protocol", { ...ok({ type: "Inert", reason: "no_run" }), protocol: "empirica/v3" });
expectThrow("missing protocol", { request_id: RID, result: { type: "Inert", reason: "no_run" } });
expectThrow("request_id mismatch", ok({ type: "Inert", reason: "no_run" }), "other-rid");
expectThrow("missing request_id", { protocol: PROTOCOL, result: { type: "Inert", reason: "no_run" } });
expectThrow("result not an object", { protocol: PROTOCOL, request_id: RID, result: null });
expectThrow("missing result", { protocol: PROTOCOL, request_id: RID });
expectThrow("unknown result.type", ok({ type: "Wat" } as never));

// --- Allow mutations ---------------------------------------------------------

expectThrow("Allow missing converged", ok({ type: "Allow", run: run() } as never));
expectThrow("Allow converged not boolean", ok({ type: "Allow", converged: "true", run: run() } as never));
expectThrow("Allow missing run", ok({ type: "Allow", converged: true } as never));
expectThrow("Allow run not object", ok({ type: "Allow", converged: true, run: null } as never));
expectThrow("Allow run.id missing", ok({ type: "Allow", converged: true, run: { status: "active" } } as never));
expectThrow("Allow run.id empty", ok({ type: "Allow", converged: true, run: { id: "", status: "active" } } as never));
expectThrow("Allow run.status missing", ok({ type: "Allow", converged: true, run: { id: "r" } } as never));
expectThrow("Allow run.status invalid", ok({ type: "Allow", converged: true, run: { id: "r", status: "unknown" } as never }));
expectThrow("Allow run.spike_backend missing", ok({ type: "Allow", converged: false, run: { id: "r", status: "active" } } as never));
for (const [label, spike_backend] of [
  ["unavailable extra field", { kind: "spikes_unavailable", diagnostic: "socket path" }],
  ["qualified missing digest", { kind: "qualified", backend: "docker", profile_id: "docker-isolated-v1",
    qualification_record_digest: `sha256:${"a".repeat(64)}` }],
  ["qualified wrong backend", { kind: "qualified", backend: "host", profile_id: "docker-isolated-v1",
    qualification_record_digest: `sha256:${"a".repeat(64)}`, execution_binding_digest: `sha256:${"b".repeat(64)}` }],
  ["qualified old profile", { kind: "qualified", backend: "docker", profile_id: "docker-rust-v1",
    qualification_record_digest: `sha256:${"a".repeat(64)}`, execution_binding_digest: `sha256:${"b".repeat(64)}` }],
  ["qualified bad digest", { kind: "qualified", backend: "docker", profile_id: "docker-isolated-v1",
    qualification_record_digest: "sha256:no", execution_binding_digest: `sha256:${"b".repeat(64)}` }],
  ["qualified extra field", { kind: "qualified", backend: "docker", profile_id: "docker-isolated-v1",
    qualification_record_digest: `sha256:${"a".repeat(64)}`, execution_binding_digest: `sha256:${"b".repeat(64)}`,
    healthy: true }],
  ["qualified numeric digest", { kind: "qualified", backend: "docker", profile_id: "docker-isolated-v1",
    qualification_record_digest: 7, execution_binding_digest: `sha256:${"b".repeat(64)}` }],
  ["unknown kind", { kind: "docker" }],
  ["missing kind", {}],
  ["array", ["spikes_unavailable"]],
] as const) expectThrow(`Allow run.spike_backend ${label}`, ok({ type: "Allow", converged: false,
  run: { id: "r", status: "active", spike_backend } } as never));

test("guard accepts both exact spike backend shapes", () => {
  const digest = (c: string) => `sha256:${c.repeat(64)}`;
  assertResponse(ok({ type: "Allow", converged: false, run: run() }), RID);
  for (const profile_id of ["docker-isolated-v1", "docker-networked-v1"] as const)
    assertResponse(ok({ type: "Allow", converged: false, run: { ...run(), spike_backend: {
      kind: "qualified", backend: "docker", profile_id,
      qualification_record_digest: digest("a"), execution_binding_digest: digest("b"),
    } } }), RID);
});

// --- Allow cross invariant mutations (D6-C C3) ------------------------------
// converged true iff status converged; converged false only non-converged statuses.

expectThrow("Allow converged=true with status=active", ok({ type: "Allow", converged: true, run: run("active") } as never));
expectThrow("Allow converged=true with status=stopped_budget", ok({ type: "Allow", converged: true, run: run("stopped_budget") } as never));
expectThrow("Allow converged=false with status=converged", ok({ type: "Allow", converged: false, run: run("converged") } as never));

// --- Block mutations ---------------------------------------------------------

expectThrow("Block run.id missing", ok({ type: "Block", run: { status: "active" }, reasons: [{ code: "x" }] } as never));
expectThrow("Block run.status invalid", ok({ type: "Block", run: { id: "r", status: "bad" }, reasons: [{ code: "x" }] } as never));
expectThrow("Block missing reasons", ok({ type: "Block", run: run() } as never));
expectThrow("Block reasons empty array", ok({ type: "Block", run: run(), reasons: [] } as never));
expectThrow("Block reasons not array", ok({ type: "Block", run: run(), reasons: "x" } as never));
expectThrow("Block reason null entry", ok({ type: "Block", run: run(), reasons: [null] } as never));
expectThrow("Block reason empty object", ok({ type: "Block", run: run(), reasons: [{}] } as never));
expectThrow("Block reason numeric code", ok({ type: "Block", run: run(), reasons: [{ code: 42 }] } as never));
expectThrow("Block reason empty code", ok({ type: "Block", run: run(), reasons: [{ code: "" }] } as never));
expectThrow("Block reason numeric message", ok({ type: "Block", run: run(), reasons: [{ code: "x", message: 42 }] } as never));
expectThrow("Block without run and non-refusal code", ok({ type: "Block", reasons: [{ code: "audit.required" }] } as never));

// --- Inert mutations ---------------------------------------------------------

expectThrow("Inert missing reason", ok({ type: "Inert" } as never));
expectThrow("Inert reason invalid", ok({ type: "Inert", reason: "bogus" } as never));
expectThrow("Inert reason not string", ok({ type: "Inert", reason: 42 } as never));

// --- Fault mutations ---------------------------------------------------------

expectThrow("Fault missing code", ok({ type: "Fault", fail_direction: "closed" } as never));
expectThrow("Fault code invalid", ok({ type: "Fault", code: "bogus", fail_direction: "closed" } as never));
expectThrow("Fault missing fail_direction", ok({ type: "Fault", code: "unavailable" } as never));
expectThrow("Fault fail_direction invalid", ok({ type: "Fault", code: "unavailable", fail_direction: "sideways" } as never));
expectThrow("Fault numeric message", ok({ type: "Fault", code: "unavailable", message: 42, fail_direction: "closed" } as never));

// --- valid responses pass ----------------------------------------------------

test("guard accepts valid Allow converged", () => {
  assertResponse(ok({ type: "Allow", converged: true, run: run("converged") }), RID);
});

test("guard accepts valid Allow not converged (all non-converged statuses)", () => {
  for (const status of ["active", "stopped_residual", "stopped_frozen", "stopped_budget"] as const) {
    assertResponse(ok({ type: "Allow", converged: false, run: run(status) }), RID);
  }
});

test("guard accepts valid Block", () => {
  assertResponse(ok({ type: "Block", run: run(), reasons: [{ code: "claim.research_missing" }] }), RID);
});

test("guard accepts valid Block with a string message", () => {
  assertResponse(ok({ type: "Block", run: run(), reasons: [{ code: "claim.research_missing", message: "3 claims lack evidence" }] }), RID);
});

test("guard accepts valid Inert", () => {
  assertResponse(ok({ type: "Inert", reason: "no_run" }), RID);
  assertResponse(ok({ type: "Inert", reason: "unsupported_host_event" }), RID);
});

test("guard accepts valid Fault", () => {
  assertResponse(ok({ type: "Fault", code: "unavailable", fail_direction: "closed" }), RID);
  assertResponse(ok({ type: "Fault", code: "corrupt_run", message: "x", fail_direction: "open" }), RID);
});

test("the guard admits the run-less contract Allow only for GetContract and only exactly", () => {
  const contract = JSON.parse(readFileSync(join(import.meta.dirname, "author-view-golden",
    "getcontract-section.json"), "utf8")).result.contract_result;
  const valid = { ...contract, digest: "sha256:" + "b".repeat(64) };
  const wrap = (result: unknown) => ({ protocol: PROTOCOL, request_id: RID, result });
  assert.doesNotThrow(() => assertResponse(wrap({ type: "Allow", contract_result: valid }), RID, "GetContract"));
  for (const [label, result, command] of [
    ["another command", { type: "Allow", contract_result: valid }, "GetRun"],
    ["no command", { type: "Allow", contract_result: valid }, undefined],
    ["extra converged", { type: "Allow", contract_result: valid, converged: false }, "GetContract"],
    ["malformed digest", { type: "Allow", contract_result: { ...valid, digest: "x" } }, "GetContract"],
    ["run Allow", { type: "Allow", converged: false, run: run() }, "GetContract"],
  ] as const)
    assert.throws(() => assertResponse(wrap(result), RID, command), GuardError, label);
});

// --- every contract value the guard checks is the vendored schema's ----------
// Each exhaustive record fails tsc on a missing or extra type member, so the type alias, the guard's
// derived runtime set and the raw vendored schema enum cannot drift apart.

const RUN_STATUS_MEMBERS: Record<RunStatus, true> = {
  active: true, converged: true, stopped_residual: true, stopped_frozen: true, stopped_budget: true,
};
const INERT_REASON_MEMBERS: Record<InertReason, true> = { no_run: true, unsupported_host_event: true };
const FAULT_CODE_MEMBERS: Record<FaultCode, true> = {
  invalid_request: true, unsupported: true, conflict: true, corrupt_run: true, corrupt_artifacts: true,
  unavailable: true,
};
const FAIL_DIRECTION_MEMBERS: Record<FailDirection, true> = { open: true, closed: true };
const SPIKE_PROFILE_MEMBERS: Record<SpikeProfile, true> = { "docker-isolated-v1": true, "docker-networked-v1": true };
const qualifiedBackend = DEFS.spikeBackend.oneOf.find(
  (branch: { properties: { kind: { const: string } } }) => branch.properties.kind.const === "qualified");

for (const [label, derived, members, schemaEnum] of [
  ["run status", RUN_STATUSES, RUN_STATUS_MEMBERS, DEFS.runView.properties.status.enum],
  ["inert reason", INERT_REASONS, INERT_REASON_MEMBERS, DEFS.inert.properties.reason.enum],
  ["fault code", FAULT_CODES, FAULT_CODE_MEMBERS, DEFS.fault.properties.code.enum],
  ["fail direction", FAIL_DIRECTIONS, FAIL_DIRECTION_MEMBERS, DEFS.fault.properties.fail_direction.enum],
  ["spike profile", SPIKE_PROFILE_IDS, SPIKE_PROFILE_MEMBERS, qualifiedBackend.properties.profile_id.enum],
] as const) test(`the guard's ${label} set is exactly the type's members and the vendored schema enum`, () => {
  assert.deepEqual([...derived].sort(), Object.keys(members).sort());
  assert.deepEqual([...derived].sort(), [...schemaEnum].sort());
});

test("the spike-backend kind type, the guard's derived kinds and the vendored oneOf kinds are one set", () => {
  const members: Record<SpikeBackend["kind"], true> = { spikes_unavailable: true, qualified: true };
  const schemaKinds = DEFS.spikeBackend.oneOf.map((branch: { properties: { kind: { const: string } } }) =>
    branch.properties.kind.const).sort();
  assert.deepEqual([...SPIKE_BACKEND_KINDS].sort(), Object.keys(members).sort());
  assert.deepEqual([...SPIKE_BACKEND_KINDS].sort(), schemaKinds);
});

test("the spike-backend derivation refuses at load a schema without unique kinds or a oneOf", () => {
  assert.equal(spikeBackendChecks(DEFS.spikeBackend).size, DEFS.spikeBackend.oneOf.length);
  const [first] = DEFS.spikeBackend.oneOf;
  assert.throws(() => spikeBackendChecks({ oneOf: [first, first] }), /kind consts are not unique/);
  assert.throws(() => spikeBackendChecks({ anyOf: DEFS.spikeBackend.oneOf }), /not a closed oneOf/);
});

test("the Allow cross-invariant derivation refuses at load a non-boolean or duplicate converged const", () => {
  const statuses = allowRunStatuses(DEFS);
  assert.deepEqual([...statuses.keys()].sort(), [false, true]);
  const withConverged = (name: string, converged: unknown) => ({ ...DEFS, [name]: { ...DEFS[name],
    properties: { ...DEFS[name].properties, converged } } });
  assert.throws(() => allowRunStatuses(withConverged("allowConverged", { const: "true" })), /not a boolean const/);
  assert.throws(() => allowRunStatuses(withConverged("allowConverged", { const: false })), /converged consts are not distinct/);
});

test("the guard's one digest pattern and the protocol are the vendored schema's", () => {
  assert.equal(DIGEST.source, DEFS.digest256.pattern);
  assert.equal(DIGEST.flags, "");
  assert.equal(PROTOCOL, RESPONSE.properties.protocol.const);
});

test("the guard accepts every repository response fixture", () => {
  const files = readdirSync(FIXTURES).filter((name) => name.endsWith(".json")).sort();
  const responses = files.map((name) => readJson(join(FIXTURES, name))).filter((fixture) => "expected" in fixture);
  assert.ok(responses.length >= 28, `expected the response fixtures, found ${responses.length}`);
  for (const { request, expected } of responses)
    assert.doesNotThrow(() => assertResponse(expected, expected.request_id, request.command.type), request.request_id);
});

test("the guard refuses a contract index of another contract identity", () => {
  const golden = readJson(join(import.meta.dirname, "author-view-golden", "getcontract-index.json")).result.contract_result;
  const index = { ...golden, digest: "sha256:" + "b".repeat(64) };
  const wrap = (contract_result: unknown) => ({ protocol: PROTOCOL, request_id: RID,
    result: { type: "Allow", contract_result } });
  assert.doesNotThrow(() => assertResponse(wrap(index), RID, "GetContract"));
  for (const [label, changed] of [
    ["version", { ...index.index, version: "5.0.0" }],
    ["id", { ...index.index, id: "empirica/private" }],
    ["extra key", { ...index.index, extra: [] }],
  ] as const)
    assert.throws(() => assertResponse(wrap({ ...index, index: changed }), RID, "GetContract"), GuardError, label);
});
