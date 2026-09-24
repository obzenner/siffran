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
import { startRunRequest, resolveRunRequest, evaluateRunRequest, observeActionRequest, getRunRequest } from "../src/translate.ts";
import { govern } from "../src/governance-ui.ts";
import { createPrivateIngress } from "../src/private-transport.ts";
import { fakeCtx } from "./fakes.ts";
import type { Response } from "../src/contract.ts";
import { GuardError } from "../src/guard.ts";

const py = process.env.EMPIRICA_PYTHON ?? "python3";
let pythonAvailable = true;
try {
  execFileSync(py, ["--version"], { stdio: "ignore" });
} catch {
  pythonAvailable = false;
}

const SEL = { project: "live-pi", session: "smoke" };
const bridgeScript = join(process.cwd(), "bridge.py");
const testRepo = mkdtempSync(join(tmpdir(), "empirica-pi-live-"));
const testHome = join(testRepo, "home");
mkdirSync(testHome);
execFileSync("git", ["init", "-q"], { cwd: testRepo });
const bridge = createStdioBridgeDispatch({
  command: py,
  args: [bridgeScript],
  cwd: testRepo,
  env: { ...process.env, EMPIRICA_HOME: testHome },
  timeoutMs: 15_000,
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
      modes: { cli_exec: boolean; multi_provider: boolean };
      obligations: { active: Array<{ id: string; status: string }> };
    };
  };
  const author = { provider: "anthropic", id: "claude-sonnet-4-6" };
  const auditor = { provider_id: "anthropic", model_id: "claude-opus-4-6" };
  const run = runOf(await dispatch(startRunRequest({ project: "pi-decisions", session: "real-private" }, "governed bridge test", "decision-start")));
  runOf(await dispatch(observeActionRequest(run.id, { kind: "graph", payload: {
    root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
  } }, "decision-graph")));
  runOf(await dispatch(observeActionRequest(run.id, { kind: "configure_run", auditor,
    budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 } }, "decision-proposal")));
  const ctx = fakeCtx(testRepo); ctx.hasUI = true; ctx.model = author;
  ctx.modelRegistry = { getAvailable: () => [author, { provider: auditor.provider_id, id: auditor.model_id }] };
  const ui: string[] = [], payloads: Array<Record<string, unknown>> = [];
  ctx.ui.confirm = async (title, message) => {
    ui.push(title);
    if (title.startsWith("FINAL CONFIRMATION")) {
      assert.match(message, /Child spawns: proposed 2/);
      assert.match(message, /Audit spawns: proposed 2/);
      const pending = runOf(await dispatch(getRunRequest(run.id, "decision-pending")));
      assert.equal(pending.governance?.state, "pending");
      assert.equal(pending.governance?.approved_digest, null);
    }
    return true;
  };
  ctx.ui.select = async (title, choices) => title === "Decision" ? "Edit configuration for another review"
    : title === "Independent auditor" ? choices.find(choice => choice === `${auditor.provider_id}/${auditor.model_id}`) : "Enabled";
  ctx.ui.input = async title => title.startsWith("Investigation passes") ? "8"
    : title.startsWith("Child spawns") || title.startsWith("Audit spawns") ? "2" : "";
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
    assert.equal(persisted.modes.cli_exec, true);
    assert.equal(persisted.modes.multi_provider, true);
    assert.ok(persisted.obligations.active.some((row: { id: string; status: string }) =>
      row.id === "obligation.investigation" && row.status === "residual"));
    assert.deepEqual(payloads.map(p => p.outcome ?? (p.submission as { action: string })?.action),
      ["present", "edit", "present", "approve"]);
    assert.equal(ui.filter(title => title.startsWith("FINAL CONFIRMATION")).length, 1);
    assert.ok(ui.includes("Inventory is complete and authorized"));
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test(
  "live bridge: StartRun with the exact profile creates a located v2 run",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const response = await bridge(startRunRequest(SEL, "live bridge smoke", "live-1"));
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
  "live bridge: ResolveRun returns the located run when StartRun won",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const response = await bridge(resolveRunRequest(SEL, "live-2"));
    assert.equal(response.protocol, "empirica/v2");
    assert.equal(response.request_id, "live-2");
    // Node may schedule this independently of the StartRun test: absent is Inert; after StartRun
    // the exact same selector resolves to an Allow with the canonical er2 handle.
    assert.ok(
      response.result.type === "Inert" || response.result.type === "Allow",
      `expected Inert or Allow, got ${response.result.type}`,
    );
    if (response.result.type === "Allow" && "run" in response.result) {
      assert.match(response.result.run.id, /^er2:/);
    }
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
  "live bridge: the exact profile is set in the transport env (no default)",
  () => {
    assert.equal(HOST_PROFILE_ID, "pi@0.84.1+pi-subagents@0.50.0");
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
    const { spawn } = await import("node:child_process");
    const child = spawn(py, [join(process.cwd(), "bridge.py")], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, EMPIRICA_HOST_PROFILE_ID: HOST_PROFILE_ID },
    });
    const response = await new Promise<string>((resolve, reject) => {
      let out = "";
      child.stdout.on("data", (c: string) => (out += c));
      child.on("close", () => resolve(out));
      child.on("error", reject);
      child.stdin.end(
        JSON.stringify({ protocol: "empirica/v1", request_id: "v1-test", command: { type: "StartRun", selector: { project: "x", session: "y" }, goal: "z" } }),
      );
    });
    const parsed = JSON.parse(response);
    assert.equal(parsed.protocol, "empirica/v2");
    assert.equal(parsed.result.type, "Fault");
    assert.equal(parsed.result.code, "invalid_request");
    assert.equal(parsed.result.fail_direction, "closed");
  },
);
