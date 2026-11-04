// The PR 18 live bridge: a cancel mid-batch must not throw away answers already given. One real
// two-item evidence flow through the REAL bridge (`bridge.py`), the real private bridge
// (`private_bridge.py`) and a real run on its own isolated machine home — with a scripted dialog
// standing in for the human only: item 1 is confirmed, item 2's select is cancelled (Esc), and the
// exact opposite of the Lima finding must come out of the core: the completed answer is admitted
// once, the cancelled item reads `later` (the observer's "Not answered") and nothing is invented.
// This file is alone in the live-bridge set (like its siblings) so one full flow stays far inside
// node --test's 30 s per-file cap, sharing the setup through live-bridge-harness.ts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { evaluateRunRequest, observeActionRequest, startRunRequest } from "../src/translate.ts";
import { confirmEvidence, observeSurface } from "../src/evidence-ui.ts";
import { govern } from "../src/governance-ui.ts";
import { createPrivateIngress } from "../src/private-transport.ts";
import type { Response } from "../src/contract.ts";
import { fakeCtx } from "./fakes.ts";
import { INVOCATION, liveBridgeDirectory, liveBridgeDispatch } from "./live-bridge-harness.ts";

const py = process.env.EMPIRICA_PYTHON ?? "python3";
let pythonAvailable = true;
try {
  execFileSync(py, ["--version"], { stdio: "ignore" });
} catch {
  pythonAvailable = false;
}

test(
  "live bridge: a cancelled second item submits the first — the observer reads item 2 as Not answered (PR 18)",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const { testRepo, testHome } = liveBridgeDirectory("evidence-partial");
    const dispatch = liveBridgeDispatch(testRepo, testHome);
    const runOf = (response: Response) => {
      assert.equal(response.result.type, "Allow", JSON.stringify(response.result));
      assert.ok("run" in response.result && response.result.run);
      return response.result.run as { id: string };
    };

    // The same real flow as live-bridge-evidence.test.ts, with TWO items: two research records
    // done for one claim, with two distinct source/quote pairs so grouping cannot merge them
    // (A5) — the human confirms the first and cancels the second, and only the first answer may
    // be admitted.
    const run = runOf(await dispatch(startRunRequest(
      { project: "pi-evidence-live", session: "smoke" }, "keep completed answers end to end", "evidence-partial-start", INVOCATION)));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "route", reason: "live evidence partial flow" }, "evidence-partial-route")));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "graph", payload: {
      root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
    } }, "evidence-partial-graph")));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "configure_run",
      budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 },
      rationale: "sized for the supplied claim and one audit attempt" }, "evidence-partial-proposal")));

    const ctx = fakeCtx(testRepo);
    ctx.hasUI = true;
    ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
    ctx.ui.custom = async factory => await new Promise(resolve => {
      const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
      component.handleInput("\r");
      component.handleInput("\r");
    });
    const shown: Array<{ kind: string; title: string }> = [];
    ctx.ui.select = async (title: string, options: string[]) => {
      shown.push({ kind: "select", title });
      assert.deepEqual(options, ["Confirm", "Reject", "Later"]);
      return shown.filter((entry) => entry.kind === "select").length === 1 ? "Confirm" : undefined;  // item 2: Esc
    };
    ctx.ui.input = async (title: string) => {
      shown.push({ kind: "input", title });
      return "";
    };

    const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
    try {
      process.env.EMPIRICA_HOME = testHome;
      process.env.EMPIRICA_REPO_DIR = testRepo;
      const privateIngress = createPrivateIngress();
      const approved = runOf(await govern(run.id, ctx, privateIngress));
      assert.equal((approved as unknown as { governance: { state: string } }).governance.state, "approved");
      runOf(await dispatch(observeActionRequest(run.id, { kind: "investigate" }, "evidence-partial-investigate")));
      assert.equal(await observeSurface(run.id, ctx, privateIngress), true, "the human surface is recorded");
      runOf(await dispatch(observeActionRequest(run.id, { kind: "research", claim_id: "C0",
        source_kind: "web", result: "supports",
        payload: { source_ref: "https://example.test/spec", citation: "seen directly on the page" } }, "evidence-partial-research-1")));
      runOf(await dispatch(observeActionRequest(run.id, { kind: "research", claim_id: "C0",
        source_kind: "runtime", result: "supports",
        payload: { source_ref: "deploy output shown to the human", citation: "the service is up",
          spike_unavailable: "needs production credentials to re-observe" } }, "evidence-partial-research-2")));

      const blocked = await dispatch(evaluateRunRequest(run.id, "report_convergence", "evidence-partial-wait"));
      assert.equal(blocked.result.type, "Block", JSON.stringify(blocked.result));
      const confirmed = await confirmEvidence(blocked, run.id, ctx, privateIngress,
        async hostFailure => dispatch(evaluateRunRequest(run.id, "report_convergence", randomUUID(), hostFailure)),
        Date.now() + 120_000);
      assert.equal(confirmed.failure, undefined, "the batch was shown");
      // The first item's answer was kept: the run still blocks, on the second record that stayed for later.
      assert.equal(confirmed.response.result.type, "Block", JSON.stringify(confirmed.response.result));
      assert.equal((confirmed.response.result as unknown as { reasons: Array<{ code: string }> }).reasons[0]!.code,
        "evidence.confirmation_required", "the unanswered record still needs a human");
      assert.equal(shown.filter((s) => s.kind === "select").length, 2, "two item selects, the second cancelled");
      assert.ok(shown.filter((s) => s.kind === "select")[1]!.title
        .includes("Esc or timeout keeps completed answers; this and the rest stay for later."),
        "the second item's select named what Esc does before it was pressed (A4)");

      // Exactly one presentation and exactly one confirmation per batch (A5d), from the run's own artifact repo.
      const rows: Array<Record<string, unknown>> = [];
      const refs = execFileSync("git",
        ["for-each-ref", "--format=%(objectname)", "refs/empirica/artifacts/"],
        { cwd: testRepo, encoding: "utf8" }).trim().split("\n").filter(Boolean);
      for (const commit of refs) {
        const lines = execFileSync("git", ["ls-tree", "-r", commit], { cwd: testRepo, encoding: "utf8" });
        for (const line of lines.split("\n").filter(Boolean)) {
          const oid = line.split(/\s+/)[2]!;
          const wrapper = JSON.parse(execFileSync("git", ["cat-file", "-p", oid], { cwd: testRepo, encoding: "utf8" }));
          const body = typeof wrapper.body === "string" ? JSON.parse(wrapper.body) : null;
          if (body && (body.kind === "evidence_presentation" || body.kind === "evidence_confirmation"))
            rows.push(body);
        }
      }
      const kinds = rows.map((row) => row.kind);
      assert.equal(kinds.filter((kind) => kind === "evidence_presentation").length, 1, "one presentation, no incremental submits");
      assert.equal(kinds.filter((kind) => kind === "evidence_confirmation").length, 1,
        "one confirmed item: one confirmation artifact, the cancelled item never became a receipt row");
      const recorded = rows.find((row) => row.kind === "evidence_confirmation")!;
      assert.equal(recorded.outcome, "confirm");
      assert.equal(recorded.subject, "research");
      assert.equal(recorded.claim_id, "C0");
      assert.equal(new Set(rows.map((row) => row.receipt_id)).size, 1,
        "the receipt confirms its own presentation (one presentation, one confirmation: A5d)");

      // The observer answers with the human's real answers (`core.observer.decisions`): the first
      // record's confirm_source row reads approve/record_confirmed, the cancelled second one —
      // still presented, nothing recorded — reads later, which the UI shows as "Not answered".
      const sheetDecisions = JSON.parse(execFileSync(py, ["-", run.id, testHome, testRepo], {
        cwd: testRepo, env: { ...process.env, EMPIRICA_HOME: testHome, EMPIRICA_REPO_DIR: testRepo },
        input: `import json, sys
sys.path.insert(0, ${JSON.stringify(join(process.cwd(), "..", ".."))})
from application.location import decode_handle
from application.observer import Observer, run_key
from adapters.observer.source import StoreSource
key = decode_handle(sys.argv[1])
assert key is not None, "the handle must decode"
sheet = Observer(StoreSource(sys.argv[2], [sys.argv[3]])).sheet(run_key(key))
assert sheet is not None, "the observer must read the run"
decisions = sheet["decisions"]
row = [d for d in decisions if d["source"] == "deploy output shown to the human"]
assert len(row) == 1, row
assert row[0]["answer"] == "later" and row[0]["effect"] == "none", row
kept = [d for d in decisions if d["source"] == "https://example.test/spec"]
assert len(kept) == 1, kept
assert kept[0]["answer"] == "approve" and kept[0]["effect"] == "record_confirmed", kept
print(json.dumps({"cancelled": row[0], "kept": kept[0]}))
`, encoding: "utf8" })) as { cancelled: { answer: string; effect: string } };
      assert.equal(sheetDecisions.cancelled.answer, "later", "the cancelled item reads Not answered, still open");
      assert.equal(sheetDecisions.cancelled.effect, "none");
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
    }
  },
);
