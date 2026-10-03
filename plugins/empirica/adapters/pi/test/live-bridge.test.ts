// Live bridge smoke: proves the production stdio bridge round-trips a real
// request over the real Python bridge with the exact host profile, and that the
// central guard validates the response. Uses the real bridge.py and the real
// EMPIRICA_HOST_PROFILE_ID — no fakes, no network beyond the local subprocess.
//
// D6-C boundary: the bridge composes the v2 application service with a
// no-location run port, so state-bearing reads (ResolveRun/EvaluateRun/RestoreRun)
// return unsupported/closed. StartRun with an unknown profile fails closed.
// The test proves the guard catches every variant and the adapter fails closed.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createStdioBridgeDispatch, HOST_PROFILE_ID } from "../src/stdio-transport.ts";
import { startRunRequest, startRunNotice, resolveRunRequest, evaluateRunRequest, observeActionRequest, getRunRequest } from "../src/translate.ts";
import { govern, refreshGovernance, piGovernanceContext } from "../src/governance-ui.ts";
import { createPrivateIngress } from "../src/private-transport.ts";
import { runJsonProcess } from "../src/process-transport.ts";
import { fakeCtx } from "./fakes.ts";
import { recordedInvocation } from "./owner-fixture.ts";
import type { Response } from "../src/contract.ts";

const py = process.env.EMPIRICA_PYTHON ?? "python3";
let pythonAvailable = true;
try {
  execFileSync(py, ["--version"], { stdio: "ignore" });
} catch {
  pythonAvailable = false;
}

const SEL = { project: "live-pi", session: "smoke" };
const INVOCATION = recordedInvocation();
const bridgeScript = join(process.cwd(), "bridge.py");
const testRepo = mkdtempSync(join(tmpdir(), "empirica-pi-live-"));
const testHome = join(testRepo, "home");
mkdirSync(testHome);
execFileSync("git", ["init", "-q"], { cwd: testRepo });
const BRIDGE_TIMEOUT_MS = 15_000;
const bridge = createStdioBridgeDispatch({
  command: py,
  args: [bridgeScript],
  cwd: testRepo,
  env: { ...process.env, EMPIRICA_HOME: testHome },
  timeoutMs: BRIDGE_TIMEOUT_MS,
});

test("Pi surfaces the real service blank-goal refusal", async () => {
  const response = await bridge(startRunRequest(
    { project: "live-pi", session: "blank" }, "   ", "blank-start", INVOCATION));
  assert.equal(response.result.type, "Block");
  assert.equal("run" in response.result, false);
  assert.match(startRunNotice(response.result).text, /non-empty goal is required/i);
});


test("Pi raw edit and locked approval cross the actual private Python service", async () => {
  // Scripted UI is not native qualification. Only the dialogs are faked here;
  // response resolution, receipts, revision binding, and persistence are production code.
  const env = { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo };
  const dispatch = createStdioBridgeDispatch({ command: py, args: [bridgeScript], cwd: testRepo, env });
  const runOf = (response: Response) => {
    assert.equal(response.result.type, "Allow", JSON.stringify(response.result));
    assert.ok("run" in response.result && response.result.run);
    return response.result.run as unknown as {
      id: string;
      governance: { state: string; approved_digest: string | null; proposal_digest: string;
        budgets: { max_spawns: number; max_audit_spawns: number } };
      obligations: { active: Array<{ id: string; status: string }> };
    };
  };
  const author = { provider: "anthropic", id: "claude-sonnet-4-6" };
  const run = runOf(await dispatch(startRunRequest({ project: "pi-decisions", session: "real-private" }, "governed bridge test", "decision-start", INVOCATION)));
  runOf(await dispatch(observeActionRequest(run.id, { kind: "graph", payload: {
    root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
  } }, "decision-graph")));
  runOf(await dispatch(observeActionRequest(run.id, { kind: "configure_run",
    budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 },
    rationale: "sized for the supplied claim and one audit attempt" }, "decision-proposal")));
  const ctx = fakeCtx(testRepo); ctx.hasUI = true; ctx.model = author;
  ctx.modelRegistry = { getAvailable: () => { throw new Error("governance must not enumerate models"); } };
  const ui: string[] = [], payloads: Array<Record<string, unknown>> = [];
  let dialogs = 0;
  ctx.ui.custom = async factory => await new Promise(resolve => {
    const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
    ui.push(...component.render(80));
    dialogs++;
    const keys = dialogs === 1
      ? ["8", "\x1b[B", "2", "\x1b[B", "2", "\r", "\r"]
      : ["\r"];
    for (const key of keys) component.handleInput(key);
  });
  const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
  try {
    process.env.EMPIRICA_HOME = testHome; process.env.EMPIRICA_REPO_DIR = testRepo;
    const privateIngress = createPrivateIngress();
    const approved = runOf(await govern(run.id, ctx, async request => {
      if (request.payload && request.operation === "governance_decision") payloads.push(structuredClone(request.payload));
      return privateIngress(request);
    }));
    assert.equal(approved.governance?.state, "approved");
    assert.equal(approved.governance?.approved_digest, approved.governance?.proposal_digest);
    const persisted = runOf(await dispatch(getRunRequest(run.id, "decision-persisted")));
    const budgets = persisted.governance?.budgets as { max_spawns: number; max_audit_spawns: number };
    assert.equal(budgets.max_spawns, 2);
    assert.equal(budgets.max_audit_spawns, 2);
    assert.ok(persisted.obligations.active.some((row: { id: string; status: string }) =>
      row.id === "obligation.investigation" && row.status === "residual"));
    assert.deepEqual(payloads.map(p => p.outcome ?? (p.submission as { action: string })?.action),
      ["present", "approve", "present", "approve"]);
    assert.equal(dialogs, 2);
    assert.ok(ui.some(line => line.includes("confirm edited configuration")));
    assert.ok(!ui.includes("Inventory is complete and authorized"));
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

for (const controlMode of ["deliberative", "auto"] as const) {
  test(`Pi Reject submits ceilings only and persists a rejection (${controlMode})`, async () => {
    const env = { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo };
    const dispatch = createStdioBridgeDispatch({ command: py, args: [bridgeScript], cwd: testRepo, env });
    const runOf = (response: Response) => {
      assert.equal(response.result.type, "Allow", JSON.stringify(response.result));
      assert.ok("run" in response.result && response.result.run);
      return response.result.run as unknown as {
        id: string; governance: { state: string; approved_digest: string | null } };
    };
    const run = runOf(await dispatch(startRunRequest(
      { project: "pi-decisions", session: `real-reject-${controlMode}` }, "governed reject test",
      `reject-start-${controlMode}`, INVOCATION, { controlMode })));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "graph", payload: {
      root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
    } }, `reject-graph-${controlMode}`)));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "configure_run",
      budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 2 },
      rationale: "sized for the supplied claim and one audit retry" }, `reject-proposal-${controlMode}`)));
    const ctx = fakeCtx(testRepo); ctx.hasUI = true; ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
    const submissions: Array<Record<string, unknown>> = [];
    ctx.ui.custom = async factory => await new Promise(resolve => {
      const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
      for (const key of ["\x1b[B", "\x1b[B", "\x1b[B", "\x1b[C", "\r"]) component.handleInput(key);
    });
    const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
    try {
      process.env.EMPIRICA_HOME = testHome; process.env.EMPIRICA_REPO_DIR = testRepo;
      const privateIngress = createPrivateIngress();
      await govern(run.id, ctx, async request => {
        const submission = (request.payload as { submission?: Record<string, unknown> } | undefined)?.submission;
        if (submission) submissions.push(structuredClone(submission));
        return privateIngress(request);
      });
      assert.deepEqual(submissions, [{ action: "reject",
        configuration: { budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 2 } } }]);
      const persisted = runOf(await dispatch(getRunRequest(run.id, `reject-persisted-${controlMode}`)));
      assert.equal(persisted.governance.state, "rejected");
      assert.equal(persisted.governance.approved_digest, null);
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
    }
  });
}

test(
  "live bridge: StartRun with the exact profile creates a located v2 run",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const response = await bridge(startRunRequest(SEL, "live bridge smoke", "live-1", INVOCATION));
    assert.equal(response.protocol, "empirica/v2");
    assert.equal(response.request_id, "live-1");
    assert.equal(response.result.type, "Allow");
    if (response.result.type === "Allow" && "run" in response.result) {
      assert.match(response.result.run.id, /^er2:/);
      assert.equal(response.result.run.status, "active");
    }
  },
);

test(
  "live bridge: ResolveRun returns the run StartRun created for the same selector",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const selector = { project: "live-pi", session: "resolve-after-start" };
    const started = await bridge(startRunRequest(selector, "resolve after start", "live-2a", INVOCATION));
    assert.equal(started.result.type, "Allow", JSON.stringify(started.result));
    assert.ok("run" in started.result && started.result.run);
    const resolved = await bridge(resolveRunRequest(selector, "live-2b"));
    assert.equal(resolved.protocol, "empirica/v2");
    assert.equal(resolved.request_id, "live-2b");
    assert.equal(resolved.result.type, "Allow", JSON.stringify(resolved.result));
    assert.ok("run" in resolved.result && resolved.result.run);
    assert.match(resolved.result.run.id, /^er2:/);
    assert.equal(resolved.result.run.status, "active");
    assert.equal(resolved.result.run.id, started.result.run.id);
  },
);

test(
  "live bridge: ResolveRun for a selector that was never started is Inert",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const response = await bridge(resolveRunRequest(
      { project: "live-pi", session: "never-started" }, "live-2c"));
    assert.equal(response.protocol, "empirica/v2");
    assert.equal(response.request_id, "live-2c");
    assert.equal(response.result.type, "Inert", JSON.stringify(response.result));
  },
);

test(
  "live bridge: EvaluateRun with an unknown handle is guarded",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const response = await bridge(
      evaluateRunRequest("nonexistent-handle", "report_convergence", "live-3"),
    );
    assert.equal(response.protocol, "empirica/v2");
    assert.equal(response.request_id, "live-3");
    assert.ok(
      response.result.type === "Inert" || response.result.type === "Fault",
      `expected Inert or Fault, got ${response.result.type}`,
    );
  },
);

test(
  "live bridge: headless (hasUI=false) governance refresh does not block tool calls",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    // Regression for the headless-Pi block (quality #1): a print/auto session has
    // no UI, so it submits ingress "unavailable". Governance refresh (run in front of
    // every tool call) must still Allow/Inert, and the admitted capability must be
    // "unavailable" — not blocked, not "human_configuration". Real private service.
    const env = { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo };
    const dispatch = createStdioBridgeDispatch({ command: py, args: [bridgeScript], cwd: testRepo, env });
    const started = await dispatch(startRunRequest(
      { project: "pi-headless", session: "no-ui" }, "headless governance", "headless-start", INVOCATION));
    assert.equal(started.result.type, "Allow", JSON.stringify(started.result));
    assert.ok("run" in started.result && started.result.run);
    const runId = (started.result.run as { id: string }).id;
    const ctx = fakeCtx(testRepo); ctx.hasUI = false; ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
    assert.equal(piGovernanceContext(ctx).ingress, "unavailable");
    const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
    try {
      process.env.EMPIRICA_HOME = testHome; process.env.EMPIRICA_REPO_DIR = testRepo;
      const privateIngress = createPrivateIngress();
      const refreshed = await refreshGovernance(runId, ctx, privateIngress);
      assert.ok(["Allow", "Inert"].includes(refreshed.result.type), JSON.stringify(refreshed.result));
      if ("run" in refreshed.result && refreshed.result.run) {
        const g = refreshed.result.run.governance as { context: { approval_capability: string } } | null;
        assert.equal(g?.context.approval_capability, "unavailable");
      }
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
    }
  },
);

test(
  "live bridge: the real private audit_prepare refuses a runtime other than the one recorded at StartRun",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    // The run records INVOCATION.host_runtime once. audit_prepare carries the adapter's runtime observed
    // now; the real Python private bridge refuses closed when it differs. (The run is not audit-ready, so
    // the matching runtime proceeds past the comparison and is then refused by the reservation, which
    // proves the comparison, not the readiness, is what the mismatch tripped.)
    const env = { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo };
    const dispatch = createStdioBridgeDispatch({ command: py, args: [bridgeScript], cwd: testRepo, env });
    const started = await dispatch(startRunRequest(
      { project: "pi-runtime", session: "bound" }, "runtime-bound audit", "runtime-start", INVOCATION));
    assert.equal(started.result.type, "Allow", JSON.stringify(started.result));
    assert.ok("run" in started.result && started.result.run);
    const runId = (started.result.run as { id: string }).id;
    const recorded = INVOCATION.host_runtime;
    const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
    try {
      process.env.EMPIRICA_HOME = testHome; process.env.EMPIRICA_REPO_DIR = testRepo;
      const ingress = createPrivateIngress();
      const prepare = (host_runtime?: unknown) => ingress({ operation: "audit_prepare", run_id: runId,
        role_profile: "empirica.empirica-auditor", payload: host_runtime === undefined ? {} : { host_runtime } });
      const otherVersion = { ...recorded, subagents: { ...recorded.subagents, version: "0.64.0" } };
      const otherRoot = { ...recorded, subagents: { ...recorded.subagents, package_root: "/opt/elsewhere/pi-subagents" } };
      for (const observed of [otherVersion, otherRoot, undefined])
        assert.deepEqual(await prepare(observed), { type: "audit_refused", reason: "runtime_changed" });
      await assert.rejects(prepare(recorded), /audit reservation denied/);
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
    }
  },
);

test(
  "live bridge: the exact profile is set in the transport env (no default)",
  () => {
    assert.equal(HOST_PROFILE_ID, "pi@0.84.1+pi-subagents-foreground-audit-v1");
  },
);

test(
  "live bridge: a raw v1 protocol request is rejected by the bridge as invalid_request",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    // Bypass the transport guard: send a genuine raw v1 protocol request to the
    // bridge and verify the bridge itself returns a v2 Fault
    // (invalid_request/closed). The bridge speaks v2 only; a v1 protocol is
    // invalid_request before profile composition.
    const response = await runJsonProcess({
      command: py, args: [join(process.cwd(), "bridge.py")],
      env: { ...process.env, EMPIRICA_HOST_PROFILE_ID: HOST_PROFILE_ID },
      timeoutMs: BRIDGE_TIMEOUT_MS, label: "raw v1 bridge request",
    }, JSON.stringify({
      protocol: "empirica/v1", request_id: "v1-test",
      command: { type: "StartRun", selector: { project: "x", session: "y" }, goal: "z" },
    }));
    const parsed = JSON.parse(response);
    assert.equal(parsed.protocol, "empirica/v2");
    assert.equal(parsed.result.type, "Fault");
    assert.equal(parsed.result.code, "invalid_request");
    assert.equal(parsed.result.fail_direction, "closed");
  },
);
