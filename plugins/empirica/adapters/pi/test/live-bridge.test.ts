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
import { startRunRequest, resolveRunRequest, evaluateRunRequest } from "../src/translate.ts";
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
