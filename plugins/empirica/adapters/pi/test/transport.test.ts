// Proves the production JSON stdio transport round-trips a request over a child
// process, guards the response through the central runtime guard, and rejects
// on the failure modes the gate relies on to fail closed. The "bridge" here is a
// trivial `node -e` script — deterministic, no network, no Python, no Pi runtime.

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createStdioBridgeDispatch, HOST_PROFILE_ID } from "../src/stdio-transport.ts";
import { createPrivateIngress, type PrivateOperation } from "../src/private-transport.ts";
import { resolveRunRequest } from "../src/translate.ts";
import { GuardError } from "../src/guard.ts";

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

test("HOST_PROFILE_ID is the exact pi profile with no default", () => {
  assert.equal(HOST_PROFILE_ID, "pi@0.84.1+pi-subagents@0.50.0");
});

test("assertPrivateResponse accepts the real private shapes and rejects every malformed one", async () => {
  const { assertPrivateResponse } = await import("../src/private-transport.ts");
  const plan = { child_id: "c", role_profile: "empirica.empirica-auditor", operation_id: "o", argument: {} };
  const accepted: Array<[PrivateOperation, unknown]> = [
    ["classify_identity", null],
    ["classify_identity", { identity: "anthropic/claude" }],
    ["audit_prepare", { type: "audit_plan", plan }],
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
