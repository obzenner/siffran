// Live bridge binding (review-2 item 8, m2): only the mediator's own refresh binds
// the HITL document. A plain governance_context — the refresh that runs in front of
// every tool_call, model_select and session event — must carry no document and write
// nothing under the machine home; the bridge writes only for the raw-level
// `bind_document: true` the TypeScript mediation sends. Real stdio bridge and real
// private bridge, one fresh machine home per file (live-bridge-harness.ts), so no
// run ever reaches the developer's real ~/.empirica-plugin.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { join } from "node:path";

import { startRunRequest, observeActionRequest } from "../src/translate.ts";
import { createStdioBridgeDispatch } from "../src/stdio-transport.ts";
import { createPrivateIngress } from "../src/private-transport.ts";
import { piGovernanceContext } from "../src/governance-ui.ts";
import { contractProfileId, fakeCtx } from "./fakes.ts";
import { INVOCATION, liveBridgeDirectory } from "./live-bridge-harness.ts";

const py = process.env.EMPIRICA_PYTHON ?? "python3";
let pythonAvailable = true;
try {
  execFileSync(py, ["--version"], { stdio: "ignore" });
} catch {
  pythonAvailable = false;
}

/** The HITL documents written under one machine home. */
const hitlFiles = (home: string): string[] =>
  readdirSync(home, { recursive: true, encoding: "utf8" })
    .filter(name => name.split("/").includes("hitl") && name.endsWith(".json"))
    .map(name => join(home, name));

test(
  "live bridge: a plain refresh binds nothing; only the mediator's bind flag writes",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const { testRepo, testHome } = liveBridgeDirectory("binding");
    const env = { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo };
    const bridge = createStdioBridgeDispatch({ command: py, args: [join(process.cwd(), "bridge.py")],
      cwd: testRepo, env, timeoutMs: 15_000 });
    const started = await bridge(startRunRequest(
      { project: "pi-binding", session: "write-first" }, "governed binding test", "binding-start", INVOCATION));
    assert.equal(started.result.type, "Allow", JSON.stringify(started.result));
    assert.ok("run" in started.result && started.result.run);
    const runId = (started.result.run as { id: string }).id;
    const runOf = (response: unknown) => {
      assert.ok((response as { result: { type: string } }).result.type === "Allow");
      return (response as { result: { run: { id: string } } }).result.run;
    };
    runOf(await bridge(observeActionRequest(runId, { kind: "graph", payload: {
      root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
    } }, "binding-graph")));
    runOf(await bridge(observeActionRequest(runId, { kind: "configure_run",
      budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 },
      rationale: "sized for the supplied claim and one audit attempt" }, "binding-proposal")));
    const ctx = fakeCtx(testRepo); ctx.hasUI = true; ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
    const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
    try {
      process.env.EMPIRICA_HOME = testHome; process.env.EMPIRICA_REPO_DIR = testRepo;
      const ingress = createPrivateIngress();
      // The plain refresh — the call every tool_call repeats while the human is deciding.
      // It carries the proposal for the continuity notice, but no document, and it must
      // not write one either: the judgment is not the human's, and the mediator binds its
      // own document when it actually mediates.
      const plain = await ingress({ operation: "governance_context", run_id: runId,
        payload: piGovernanceContext(ctx) });
      const plainPresentation = (plain as { result: { presentation?: { document?: unknown } } })
        .result.presentation;
      assert.equal(plainPresentation?.document, undefined,
        "a plain refresh hands the TypeScript no document to bind");
      assert.deepEqual(hitlFiles(testHome), [], "a plain refresh writes no document");
      // The mediator's own refresh: the raw-level bind flag, the one document written
      // before any present, exactly as the write-first flow requires.
      const bound = await ingress({ operation: "governance_context", run_id: runId,
        bind_document: true, payload: piGovernanceContext(ctx) });
      const document = (bound as { result: { presentation?: { document?: unknown } } })
        .result.presentation?.document as Record<string, unknown>;
      assert.ok(document, "the mediator's refresh binds the document");
      assert.match(String(document.digest), /^sha256:[0-9a-f]{64}$/);
      assert.equal(String(document.path).split("/").pop(),
        `${String(document.digest).slice("sha256:".length)}.json`);
      const written = hitlFiles(testHome);
      assert.equal(written.length, 1, "the mediator's refresh wrote exactly one document");
      assert.equal(String(document.path), written[0]);
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
    }
  },
);

test("live bridge: the private bridge binds the parsed question the document chooses the form by", {
  skip: !pythonAvailable ? "python3 is missing" : false,
}, async () => {
  const { testRepo, testHome } = liveBridgeDirectory("question");
  const env = { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo };
  const bridge = createStdioBridgeDispatch({ command: py, args: [join(process.cwd(), "bridge.py")],
    cwd: testRepo, env, timeoutMs: 15_000 });
  const started = await bridge(startRunRequest(
    { project: "pi-question", session: "write-first" }, "governed question test", "question-start", INVOCATION));
  assert.ok("run" in started.result && started.result.run);
  const runId = (started.result.run as { id: string }).id;
  const runOf = (response: unknown) =>
    (response as { result: { run: { id: string } } }).result.run;
  runOf(await bridge(observeActionRequest(runId, { kind: "graph", payload: {
    root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
  } }, "question-graph")));
  runOf(await bridge(observeActionRequest(runId, { kind: "configure_run",
    budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 },
    rationale: "sized for the supplied claim and one audit attempt" }, "question-proposal")));
  const ctx = fakeCtx(testRepo); ctx.hasUI = true; ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
  const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
  try {
    process.env.EMPIRICA_HOME = testHome; process.env.EMPIRICA_REPO_DIR = testRepo;
    const ingress = createPrivateIngress();
    const bound = await ingress({ operation: "governance_context", run_id: runId,
      bind_document: true, payload: piGovernanceContext(ctx) });
    // M1 (review 2): the pre-approval document asks `approve` — the question the core
    // derives, never the adapter's own view of the state; the TypeScript chooses the
    // dialog from this value alone.
    const document = (bound as { result: { presentation?: { document?: Record<string, unknown> } } })
      .result.presentation?.document;
    assert.ok(document, "the mediator's refresh binds a document");
    assert.equal(document!.question, "approve");
    assert.equal(document!.text, undefined, "the TypeScript never receives the document text");
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});
