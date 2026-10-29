// The real-bridge rejection flows, in their own file to keep every test file inside node
// --test's 30 s per-file cap with margin (PR 09; the harness is shared through
// live-bridge-harness.ts). Both control modes a started run can carry are exercised with the
// same body: each iteration owns its run (its own StartRun/configure), the same assertions hold,
// and every mediating iteration writes its HITL document under the run's isolated machine home.

import { test } from "node:test";
import assert from "node:assert/strict";

import { observeActionRequest, getRunRequest, startRunRequest } from "../src/translate.ts";
import { govern } from "../src/governance-ui.ts";
import { createPrivateIngress } from "../src/private-transport.ts";
import { fakeCtx } from "./fakes.ts";
import { INVOCATION, liveBridgeDirectory, liveBridgeDispatch, runOfAllow } from "./live-bridge-harness.ts";

const { testRepo, testHome } = liveBridgeDirectory("reject");

test(`Pi Reject submits ceilings only and persists a rejection (deliberative) + (auto)`, async () => {
  for (const controlMode of ["deliberative", "auto"] as const) {
    const dispatch = liveBridgeDispatch(testRepo, testHome);
    const run = runOfAllow(await dispatch(startRunRequest(
      { project: "pi-decisions", session: `real-reject-${controlMode}` }, "governed reject test",
      `reject-start-${controlMode}`, INVOCATION, { controlMode })));
    runOfAllow(await dispatch(observeActionRequest(run.id, { kind: "graph", payload: {
      root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
    } }, `reject-graph-${controlMode}`)));
    runOfAllow(await dispatch(observeActionRequest(run.id, { kind: "configure_run",
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
      const persisted = runOfAllow(await dispatch(getRunRequest(run.id, `reject-persisted-${controlMode}`)));
      assert.equal(persisted.governance.state, "rejected");
      assert.equal(persisted.governance.approved_digest, null);
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
    }
  }
});
