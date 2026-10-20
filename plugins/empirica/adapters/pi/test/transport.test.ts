// Proves the production JSON stdio transport round-trips a request over a child
// process, guards the response through the central runtime guard, and rejects
// on the failure modes the gate relies on to fail closed. The "bridge" here is a
// trivial `node -e` script — deterministic, no network, no Python, no Pi runtime.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { HOST_PROFILE_ID } from "../src/host-profile.ts";
import {
  CLAIM_ID_PATTERN,
  OBSERVE_ACTION_OPTIONAL_KEYS,
  OBSERVE_ACTION_REQUIRED_KEYS,
  ORDINARY_BRIDGE_TIMEOUT_MS,
  ROOT_OPTIONAL_KEYS,
  ROOT_REQUIRED_KEYS,
  SPIKE_ACTION_OPTIONAL_KEYS,
  SPIKE_ACTION_REQUIRED_KEYS,
  SPIKE_BRIDGE_TIMEOUT_MS,
  bridgeProcessConfig,
  bridgeTimeoutMs,
  createStdioBridgeDispatch,
  defaultBridgeConfig,
  isExactSpikeRequest,
} from "../src/stdio-transport.ts";
import { createPrivateIngress, type PrivateOperation } from "../src/private-transport.ts";
import { resolveRunRequest } from "../src/translate.ts";
import { GuardError } from "../src/guard.ts";
import { contractProfileId } from "./fakes.ts";

// A stand-in bridge: read the whole request, echo request_id, return Inert(no_run).
const ECHO_BRIDGE =
  "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{" +
  "const r=JSON.parse(d);" +
  "process.stdout.write(JSON.stringify({protocol:'empirica/v2',request_id:r.request_id," +
  "result:{type:'Inert',reason:'no_run'}}));});";

function nodeBridge(script: string, timeoutMs = 10_000) {
  return createStdioBridgeDispatch({
    command: process.execPath,
    args: ["-e", script],
    timeoutMs,
  });
}

test("round-trips a request and the guard accepts the response", async () => {
  const dispatch = nodeBridge(ECHO_BRIDGE);
  const response = await dispatch(resolveRunRequest({ project: "p", session: "s" }, "rid-42"));
  assert.equal(response.request_id, "rid-42");
  assert.equal(response.result.type, "Inert");
  if (response.result.type === "Inert") assert.equal(response.result.reason, "no_run");
});

test("guard rejects a non-v2 protocol response from the bridge", async () => {
  // The bridge speaks v2 only; any non-v2 protocol fails closed.
  const corrupt =
    "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{" +
    "const r=JSON.parse(d);process.stdout.write(JSON.stringify({protocol:'bad'," +
    "request_id:r.request_id,result:{type:'Inert',reason:'no_run'}}));});";
  await assert.rejects(
    async () => nodeBridge(corrupt)(resolveRunRequest({ project: "p", session: "s" }, "r")),
    (e: unknown) => e instanceof GuardError && /protocol mismatch/.test(String((e as Error).message)),
  );
});

test("guard rejects an unknown result type from the bridge", async () => {
  const badType =
    "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{" +
    "const r=JSON.parse(d);process.stdout.write(JSON.stringify({protocol:'empirica/v2'," +
    "request_id:r.request_id,result:{type:'Wat'}}));});";
  await assert.rejects(
    async () => nodeBridge(badType)(resolveRunRequest({ project: "p", session: "s" }, "r")),
    (e: unknown) => e instanceof GuardError && /unknown/.test(String((e as Error).message)),
  );
});

test("guard rejects a request_id mismatch from the bridge", async () => {
  const mismatch =
    "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{" +
    "process.stdout.write(JSON.stringify({protocol:'empirica/v2'," +
    "request_id:'different',result:{type:'Inert',reason:'no_run'}}));});";
  await assert.rejects(
    async () => nodeBridge(mismatch)(resolveRunRequest({ project: "p", session: "s" }, "my-rid")),
    (e: unknown) => e instanceof GuardError && /request_id mismatch/.test(String((e as Error).message)),
  );
});

test("rejects when the bridge exits non-zero (so the gate can fail closed)", async () => {
  const dispatch = nodeBridge("process.stderr.write('boom');process.exit(3);");
  await assert.rejects(
    async () => dispatch(resolveRunRequest({ project: "p", session: "s" }, "r")),
    /exited with code 3.*boom/s,
  );
});

test("rejects on unparseable bridge output", async () => {
  const dispatch = nodeBridge("process.stdout.write('not json at all');");
  await assert.rejects(
    async () => dispatch(resolveRunRequest({ project: "p", session: "s" }, "r")),
    /invalid JSON/,
  );
});

test("rejects when the bridge output is empty", async () => {
  const dispatch = nodeBridge("process.stdout.write('   ');");
  await assert.rejects(
    async () => dispatch(resolveRunRequest({ project: "p", session: "s" }, "r")),
    /no output/,
  );
});

test("rejects when the bridge command cannot be spawned", async () => {
  const dispatch = createStdioBridgeDispatch({
    command: "definitely-not-a-real-binary-xyzzy",
    args: [],
    timeoutMs: 10_000,
  });
  await assert.rejects(
    async () => dispatch(resolveRunRequest({ project: "p", session: "s" }, "r")),
    /failed to start/,
  );
});

test("only an exact closed generic spike envelope gets 300s; Python owns deep plan validation", () => {
  const spike = {
    protocol: "empirica/v2",
    request_id: "request-1",
    command: {
      type: "ObserveAction",
      run_id: "er2.run",
      action: {
        kind: "spike_request",
        claim_id: "C0",
        plan: { schema: "empirica.spike-plan-draft/2" },
      },
    },
  };
  assert.equal(ORDINARY_BRIDGE_TIMEOUT_MS, 30_000);
  assert.equal(SPIKE_BRIDGE_TIMEOUT_MS, 300_000);
  assert.equal(isExactSpikeRequest(spike), true);
  // Deliberately incomplete beyond the schema discriminator: TypeScript classifies only the closed
  // outer action for transport time; the Python request/core validators remain authoritative.
  assert.equal(bridgeTimeoutMs(spike, defaultBridgeConfig()), 300_000);
  assert.equal(bridgeProcessConfig(defaultBridgeConfig(), spike).timeoutMs, 300_000);
  const deepPlanNearMiss = { ...spike, command: { ...spike.command, action: {
    ...spike.command.action, plan: { ...spike.command.action.plan, framework_ceiling: 1 },
  } } };
  assert.equal(isExactSpikeRequest(deepPlanNearMiss), true,
    "deep closed-plan validation belongs only to the Python request/core boundary");

  const nearMisses = [
    { ...spike, extra: true },
    { ...spike, protocol: "empirica/v1" },
    { ...spike, request_id: "" },
    { ...spike, command: { ...spike.command, extra: true } },
    { ...spike, command: { ...spike.command, type: "GetRun" } },
    { ...spike, command: { ...spike.command, run_id: "" } },
    { ...spike, command: { ...spike.command, action: { ...spike.command.action, extra: true } } },
    { ...spike, command: { ...spike.command, action: { ...spike.command.action, claim_id: "bad claim" } } },
    { ...spike, command: { ...spike.command, action: { kind: "spike_request", claim_id: "C0",
      command: "old", dependent_files: ["old"] } } },
    { ...spike, command: { ...spike.command, action: { ...spike.command.action, plan: null } } },
    { ...spike, command: { ...spike.command, action: { ...spike.command.action,
      plan: { schema: "empirica.spike-plan/1" } } } },
  ];
  for (const request of nearMisses) {
    assert.equal(isExactSpikeRequest(request), false);
    assert.equal(bridgeProcessConfig(defaultBridgeConfig(), request).timeoutMs, 30_000);
  }
});

test("timeout classifier mirrors every duplicated canonical request-schema semantic", () => {
  const plugin = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const contracts = resolve(plugin, "../../contracts/empirica/v2");
  const request = JSON.parse(readFileSync(join(contracts, "request.schema.json"), "utf8"));
  const shared = JSON.parse(readFileSync(join(contracts, "shared-defs.json"), "utf8"));
  const optional = (node: { required: string[]; properties: Record<string, unknown> }) =>
    Object.keys(node.properties).filter((key) => !node.required.includes(key));
  const observe = request.$defs.observeAction;
  const spike = request.$defs.actionSpikeRequest;
  const plan = request.$defs.spikePlanDraft;

  assert.equal(request.type, "object");
  assert.equal(request.additionalProperties, false);
  assert.deepEqual(ROOT_REQUIRED_KEYS, request.required);
  assert.deepEqual(ROOT_OPTIONAL_KEYS, optional(request));
  assert.equal(request.properties.protocol.const, "empirica/v2");
  assert.equal(request.properties.request_id.type, "string");
  assert.equal(request.properties.request_id.minLength, 1);
  assert.ok(request.properties.command.oneOf.some(
    (item: { $ref?: string }) => item.$ref === "#/$defs/observeAction"));

  assert.equal(observe.type, "object");
  assert.equal(observe.additionalProperties, false);
  assert.deepEqual(OBSERVE_ACTION_REQUIRED_KEYS, observe.required);
  assert.deepEqual(OBSERVE_ACTION_OPTIONAL_KEYS, optional(observe));
  assert.equal(observe.properties.type.const, "ObserveAction");
  assert.equal(observe.properties.run_id.type, "string");
  assert.equal(observe.properties.run_id.minLength, 1);
  assert.deepEqual(observe.properties.observed_at.type, ["string", "null"]);
  assert.deepEqual(observe.properties.action, { $ref: "#/$defs/action" });
  assert.ok(request.$defs.action.oneOf.some(
    (item: { $ref?: string }) => item.$ref === "#/$defs/actionSpikeRequest"));

  assert.equal(spike.type, "object");
  assert.equal(spike.additionalProperties, false);
  assert.deepEqual(SPIKE_ACTION_REQUIRED_KEYS, spike.required);
  assert.deepEqual(SPIKE_ACTION_OPTIONAL_KEYS, optional(spike));
  assert.equal(spike.properties.kind.const, "spike_request");
  assert.deepEqual(spike.properties.claim_id, { $ref: "#/$defs/claimId" });
  assert.deepEqual(spike.properties.plan, { $ref: "#/$defs/spikePlanDraft" });
  assert.equal(plan.type, "object");
  assert.equal(plan.additionalProperties, false);
  assert.equal(plan.properties.schema.const, "empirica.spike-plan-draft/2");

  assert.equal(request.$defs.claimId.type, "string");
  assert.equal(shared.$defs.claimId.type, "string");
  assert.equal(CLAIM_ID_PATTERN, request.$defs.claimId.pattern);
  assert.equal(CLAIM_ID_PATTERN, shared.$defs.claimId.pattern);
});

test("generated public spike tool exposes every bounded draft field and description", () => {
  const plugin = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const tools = JSON.parse(readFileSync(join(plugin, "vendor/contracts/empirica/v2/public-tools.json"), "utf8"));
  const actions = tools.schemas.model.empirica_observe.properties.action.oneOf;
  const spike = actions.find((item: { properties?: { kind?: { const?: string } } }) =>
    item.properties?.kind?.const === "spike_request");
  assert.ok(spike);
  assert.match(spike.description, /generic deterministic spike plan draft/);
  assert.deepEqual(spike.required, ["kind", "claim_id", "plan"]);
  assert.equal("command" in spike.properties, false);
  assert.equal("dependent_files" in spike.properties, false);
  const plan = spike.properties.plan;
  assert.deepEqual(new Set(plan.required), new Set(["schema", "image", "argv", "inputs", "environment"]));
  for (const name of plan.required)
    assert.ok(typeof plan.properties[name].description === "string" && plan.properties[name].description.length > 0, name);
  assert.deepEqual([plan.properties.image.minLength, plan.properties.image.maxLength], [1, 255]);
  assert.deepEqual([plan.properties.argv.minItems, plan.properties.argv.maxItems], [1, 256]);
  assert.equal(plan.properties.inputs.maxItems, 256);
  assert.equal(plan.properties.environment.maxProperties, 128);
  assert.equal("containers" in plan.properties, false);
  assert.equal("networks" in plan.properties, false);
});

test("workload, backend, Pi, and Claude timeout owners preserve strict outer margins", () => {
  const plugin = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
  const policySource = readFileSync(join(plugin, "core/spike_plan.py"), "utf8");
  const seconds = (name: string) => {
    const match = policySource.match(new RegExp(`^${name} = ([0-9.]+)$`, "m"));
    assert.ok(match, `missing ${name}`);
    return Number(match[1]);
  };
  const workload = seconds("WORKLOAD_TIMEOUT_SECONDS");
  const backend = seconds("BACKEND_OPERATION_TIMEOUT_SECONDS");
  const piOuter = SPIKE_BRIDGE_TIMEOUT_MS / 1000;
  const claudeOuter = JSON.parse(readFileSync(join(plugin, ".mcp.json"), "utf8"))
    .mcpServers.empirica.tool_timeout_sec;
  assert.deepEqual([workload, backend, piOuter, claudeOuter], [120, 210, 300, 360]);
  assert.ok(workload < backend && backend < piOuter && piOuter < claudeOuter);
});

test("ordinary and spike test overrides are separate, positive, and bounded", () => {
  const ordinary = resolveRunRequest({ project: "p", session: "s" }, "ordinary");
  const spike = {
    protocol: "empirica/v2", request_id: "spike",
    command: { type: "ObserveAction", run_id: "r", action: {
      kind: "spike_request", claim_id: "C",
      plan: { schema: "empirica.spike-plan-draft/2" },
    } },
  };
  const config = { command: "python3", timeoutMs: 17, spikeTimeoutMs: 23 };
  assert.equal(bridgeProcessConfig(config, ordinary).timeoutMs, 17);
  assert.equal(bridgeProcessConfig(config, spike).timeoutMs, 23);
  for (const invalid of [0, -1, 30_001, 1.5, Number.NaN, Number.POSITIVE_INFINITY])
    assert.throws(() => bridgeTimeoutMs(ordinary, { command: "x", timeoutMs: invalid }), /timeoutMs/);
  for (const invalid of [0, -1, 300_001, 1.5, Number.NaN, Number.POSITIVE_INFINITY])
    assert.throws(() => bridgeTimeoutMs(spike, { command: "x", spikeTimeoutMs: invalid }), /spikeTimeoutMs/);
});

test("private ingress kills and rejects a bridge that exceeds its deadline", async () => {
  const directory = mkdtempSync(join(tmpdir(), "empirica-private-timeout-"));
  const script = join(directory, "hang.py");
  writeFileSync(script, "import time\ntime.sleep(60)\n", "utf8");
  try {
    const ingress = createPrivateIngress(25, script);
    await assert.rejects(
      ingress({ operation: "audit_failure", run_id: "run", child_id: "child" }),
      /private bridge timed out after 25ms/,
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("HOST_PROFILE_ID is the contract's pi profile with no default", () => {
  assert.equal(HOST_PROFILE_ID, contractProfileId("pi"));
});

test("assertPrivateResponse accepts the real private shapes and rejects every malformed one", async () => {
  const { assertPrivateResponse } = await import("../src/private-transport.ts");
  const plan = { child_id: "c", role_profile: "empirica.empirica-auditor", operation_id: "o", argument: {} };
  const accepted: Array<[PrivateOperation, unknown]> = [
    ["classify_identity", null],
    ["classify_identity", { identity: "anthropic/claude" }],
    ["audit_prepare", { type: "audit_plan", plan }],
    ["audit_prepare", { type: "audit_refused", reason: "runtime_changed" }],
    ["audit_prepare", { type: "audit_excluded", message: "x", identities: ["a/b"] }],
    ["audit_verdict", { type: "audit_verdict", admitted: true }],
    ["audit_verdict", { type: "audit_verdict", admitted: false }],
    ["audit_start", { type: "audit_started" }],
    ["audit_identity", { type: "audit_identity" }],
    ["audit_reject", { type: "audit_terminal" }],
    ["audit_failure", { type: "audit_terminal" }],
    ["child_event", { protocol: "empirica/v2", request_id: "trusted-child", result: { type: "Allow" } }],
  ];
  for (const [operation, value] of accepted)
    assert.deepEqual(assertPrivateResponse(operation, value), value, operation);
  const rejected: Array<[PrivateOperation, unknown]> = [
    ["classify_identity", { identity: "" }],
    ["audit_prepare", { type: "audit_plan", plan: { ...plan, argument: "x" } }],
    ["audit_prepare", { type: "audit_refused", reason: "anything else" }],
    ["audit_prepare", { type: "audit_refused" }],
    ["audit_prepare", { type: "audit_excluded", message: "x", identities: [1] }],
    ["audit_verdict", { type: "audit_verdict", admitted: {} }],
    ["audit_verdict", { type: "audit_verdict" }],
    ["audit_start", { type: "audit_terminal" }],
    ["child_event", { result: { type: "Allow" } }],
    ["child_event", []],
    ["audit_verdict", null],
  ];
  for (const [operation, value] of rejected)
    assert.throws(() => assertPrivateResponse(operation, value), /response is invalid/, operation);
});
