// One end-to-end Pi evidence confirmation through the REAL bridge (PR 12, ADR-0072): the real
// stdio bridge (bridge.py), the real private bridge (private_bridge.py), a real run on its own
// isolated machine home — and a scripted dialog standing in for the human only. The presentation
// writes the batch's HITL document before anything is shown; the confirmation repeats the
// presentation's digest; and both history rows in the run's own committed artifact repo name the
// sha256 of the file the bridge wrote. This file is alone (like live-bridge-rejections.test.ts)
// so one full flow stays far inside node --test's 30 s per-file cap, sharing the setup through
// live-bridge-harness.ts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync, statSync } from "node:fs";
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
  "live bridge: one real evidence confirmation — the presentation writes the document, the receipt repeats its digest",
  { skip: !pythonAvailable ? "python3 is missing" : false },
  async () => {
    const { testRepo, testHome } = liveBridgeDirectory("evidence");
    const dispatch = liveBridgeDispatch(testRepo, testHome);
    const runOf = (response: Response) => {
      assert.equal(response.result.type, "Allow", JSON.stringify(response.result));
      assert.ok("run" in response.result && response.result.run);
      return response.result.run as { id: string };
    };

    // A real run: the claim graph, a human-approved configuration, an investigation, research.
    const run = runOf(await dispatch(startRunRequest(
      { project: "pi-evidence-live", session: "smoke" }, "live evidence end to end", "evidence-start", INVOCATION)));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "route", reason: "live evidence flow" }, "evidence-route")));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "graph", payload: {
      root: "C0", claims: [{ id: "C0", text: "The supplied goal is achievable.", gating: true, kind: "ordinary" }], edges: [],
    } }, "evidence-graph")));
    runOf(await dispatch(observeActionRequest(run.id, { kind: "configure_run",
      budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 },
      rationale: "sized for the supplied claim and one audit attempt" }, "evidence-proposal")));

    // The dialog stands in for the human only: everything else is production code — the same
    // scripted approve that live-bridge.test.ts drives, without its raw edit (this flow needs the
    // approval, not the amendment round, and every saved private round keeps the file inside
    // node --test's 30 s per-file cap under load). Select "Confirm" for the evidence item.
    const ctx = fakeCtx(testRepo);
    ctx.hasUI = true;
    ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
    const shown: string[] = [];
    let dialogs = 0;
    ctx.ui.custom = async factory => await new Promise(resolve => {
      const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
      component.handleInput("\r");
      component.handleInput("\r");
      dialogs++;
    });
    ctx.ui.select = async (title: string, options: string[]) => {
      shown.push(title);
      assert.deepEqual(options, ["Confirm", "Reject", "Later"]);
      return "Confirm";
    };
    ctx.ui.input = async () => "";

    const previous = { EMPIRICA_HOME: process.env.EMPIRICA_HOME, EMPIRICA_REPO_DIR: process.env.EMPIRICA_REPO_DIR };
    try {
      process.env.EMPIRICA_HOME = testHome;
      process.env.EMPIRICA_REPO_DIR = testRepo;
      const privateIngress = createPrivateIngress();
      const approved = runOf(await govern(run.id, ctx, privateIngress));
      assert.equal((approved as unknown as { governance: { state: string } }).governance.state, "approved");

      runOf(await dispatch(observeActionRequest(run.id, { kind: "investigate" }, "evidence-investigate")));
      assert.equal(await observeSurface(run.id, ctx, privateIngress), true, "the human surface is recorded");
      runOf(await dispatch(observeActionRequest(run.id, { kind: "research", claim_id: "C0",
        source_kind: "web", result: "supports",
        payload: { source_ref: "https://example.test/spec", citation: "seen directly on the page" } }, "evidence-research")));

      // The end of the pass blocks on the evidence confirmation — the dialog mirrors PR 11's short
      // Claude form (PR 12), and the confirmation repeats the digest its presentation recorded.
      const blocked = await dispatch(evaluateRunRequest(run.id, "report_convergence", "evidence-wait"));
      assert.equal(blocked.result.type, "Block", JSON.stringify(blocked.result));
      const confirmed = await confirmEvidence(blocked, run.id, ctx, privateIngress,
        async hostFailure => dispatch(evaluateRunRequest(run.id, "report_convergence", randomUUID(), hostFailure)),
        Date.now() + 120_000);
      assert.equal(confirmed.failure, undefined, "the batch was shown");
      // The confirmation resolved the evidence — the run's next blocker is the audit the
      // configuration owes (the fake responders of the unit tests answer Allow here; the real
      // core keeps blocking on `audit.required`, which is exactly what should happen).
      assert.equal(confirmed.response.result.type, "Block", JSON.stringify(confirmed.response.result));
      assert.equal(confirmed.response.result.reasons[0]!.code, "audit.required");
      if ("run" in confirmed.response.result && confirmed.response.result.run)
        assert.equal(
          (confirmed.response.result.run as { confirmation_batch: unknown }).confirmation_batch,
          null,
          "the evidence batch is resolved: no confirmation waits anymore");

      // The dialog the human saw: the source and the quote, the pointer to the moved text, the
      // document's Details and short digest — never the research records' claim context. This
      // test's home serves no observer, so the fallback keeps the exact absolute path (A11),
      // labelled `File: ` and broken at `/`; the digest line is the LAST line (A12).
      assert.equal(shown.length, 1, "one item, one dialog");
      const title = shown[0]!;
      assert.match(title, /web source: https:\/\/example\.test\/spec/);
      assert.ok(title.includes('Quote: "seen directly on the page"'));
      assert.ok(title.includes("Claim texts and their no-deterministic-check reasons: in the document."),
        "the pointer names what moved");
      assert.ok(!title.includes("For claim"), "the per-claim context is in the document");
      assert.match(title, /Details: \/empirica-ui → run [0-9a-f]{12} → Human decisions/,
        "the fallback is one short arrow line naming this host's own command (review 16-1)");
      const titleLines = title.split("\n");
      assert.match(titleLines[titleLines.length - 1]!, /^Document sha256:[0-9a-f]{12}$/,
        "the short digest line ends the title (A12)");
      const titleLines2 = title.split("\n");
      const documentLine = titleLines2.find(line => /^Document sha256:[0-9a-f]{12}$/.test(line))!;
      const fileAt = titleLines2.findIndex(line => line.startsWith("File: /"));
      const pathPieces = titleLines2.slice(fileAt, titleLines2.indexOf(documentLine))
        .map((line, index) => index === 0 ? line.slice("File: ".length) : line);
      const pathLine = pathPieces.join("");
      assert.equal(pathPieces[0]!.startsWith("/"), true, "the path region opens with its `File: ` label");
      assert.ok(pathLine.startsWith(testHome) && pathLine.endsWith(".json"),
        `the exact absolute path, broken at \`/\` (A11): the pieces rebuild ${pathLine}`);

      // The run's sidecar now holds two documents: the one the governance approval wrote (PR 09)
      // and the one this evidence flow wrote. The dialog named a real file, published read-only —
      // and its bytes are what both artifacts record.
      const written = readdirSync(testHome, { recursive: true, encoding: "utf8" })
        .filter(name => name.split("/").includes("hitl") && name.endsWith(".json"))
        .map(name => join(testHome, name));
      assert.equal(written.length, 2, "the governance document and the evidence document");
      assert.ok(written.includes(pathLine), "the dialog's path names a file the bridge wrote");
      assert.equal(statSync(pathLine).mode & 0o777, 0o400, "read-only once published");
      const digest = `sha256:${createHash("sha256").update(readFileSync(pathLine)).digest("hex")}`;
      assert.equal(`Document ${digest.slice(0, 19)}`, documentLine, "the dialog showed the file's own digest");

      // Both committed rows — read from the run's own artifact repo, not the in-memory view — name
      // the same file: the presentation recorded it before the show, the receipt after the answer.
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
      assert.deepEqual(
        Object.fromEntries(rows.map(row => [row.kind, row.document_digest])),
        { evidence_presentation: digest, evidence_confirmation: digest },
        "both artifacts carry the sha256 of the file the mediation wrote");
      assert.equal(new Set(rows.map(row => row.receipt_id)).size, 1, "the receipt confirms its own presentation");
      assert.deepEqual(rows.flatMap(row => Object.keys(row)).filter(k => k === "path"), [],
        "the artifacts never carry a document path");
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
    }
  },
);
