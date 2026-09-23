import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { createEmpiricaExtension } from "../src/index.ts";
import { createStdioBridgeDispatch, defaultBridgeConfig } from "../src/stdio-transport.ts";
import { createPrivateIngress } from "../src/private-transport.ts";
import { FakePi, fakeCtx } from "./fakes.ts";

// Real Python service/private bridge with simulated documented UI; not native approval.
test("Pi governance cancels, amends, rejects and reapproves without spending work", async () => {
  const root = mkdtempSync(join(tmpdir(), "empirica-pi-governance-"));
  execFileSync("git", ["init", "-q", root]);
  const previous = { home: process.env.EMPIRICA_HOME, repo: process.env.EMPIRICA_REPO_DIR };
  process.env.EMPIRICA_HOME = join(root, "state");
  process.env.EMPIRICA_REPO_DIR = root;
  try {
    const config = { ...defaultBridgeConfig(), cwd: root, env: { ...process.env } };
    const pi = new FakePi();
    createEmpiricaExtension({ dispatch: createStdioBridgeDispatch(config),
      privateIngress: createPrivateIngress(),
      deriveSelector: () => ({ project: "ui-project", session: "ui-session" }),
    })(pi);
    const ctx = fakeCtx(root);
    const auditor = { provider_id: "anthropic", model_id: "claude-opus-4-6" };
    ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
    ctx.modelRegistry = { getAvailable: () => [ctx.model!, { provider: auditor.provider_id, id: auditor.model_id }] };
    await pi.command("empirica").handler("review supplied scope", ctx);
    const execute = async (name: string, args: unknown) => (await pi.tools.get(name)!.execute(
      "tool-ui", args, new AbortController().signal, () => {}, ctx)).details as any;
    const observe = (action: unknown) => execute("empirica_observe", { action });
    const graph = { root: "G0", claims: [{ id: "G0", text: "supplied scope", kind: "ordinary", gating: true }], edges: [] };
    await observe({ kind: "route", reason: "supplied context" });
    await observe({ kind: "graph", payload: graph });
    let result = await observe({ kind: "configure_run", auditor });
    assert.equal(result.type, "Block");
    assert.equal(result.run.governance.state, "pending");
    ctx.hasUI = true;
    ctx.ui.select = async () => undefined;
    ctx.ui.confirm = async () => true;
    ctx.ui.input = async () => undefined;
    result = await observe({ kind: "configure_run", auditor });
    assert.equal(result.type, "Block");
    assert.equal(result.run.governance.budgets.passes_used, 0);
    ctx.ui.select = async () => "Reject";
    result = await observe({ kind: "configure_run", auditor });
    assert.equal(result.run.governance.state, "rejected");
    ctx.ui.select = async (title, options) => title.includes("auditor") ? options.find(o => o.endsWith(auditor.model_id)) : "Amend";
    const amended = { ...graph, claims: [{ ...graph.claims[0], text: "human-amended scope" }] };
    ctx.ui.input = async () => JSON.stringify({ graph: amended, configuration: result.run.governance.proposal });
    result = await observe({ kind: "configure_run", auditor });
    assert.equal(result.run.governance.scope.claims[0].text, "human-amended scope");
    assert.notEqual(result.run.governance.state, "approved");
    ctx.ui.select = async (title, options) => title.includes("auditor") ? options.find(o => o.endsWith(auditor.model_id)) : "Approve";
    ctx.ui.confirm = async (_title, message) => {
      assert.match(message, /human-amended scope/);
      assert.match(message, /passes_used/);
      return true;
    };
    result = await observe({ kind: "configure_run", auditor });
    assert.equal(result.run.governance.state, "approved");
    assert.equal(result.run.governance.approval_kind, "host_ui");
    assert.equal((await observe({ kind: "investigate" })).type, "Allow");
    await observe({ kind: "graph", payload: graph });
    assert.equal((await observe({ kind: "investigate" })).reasons[0].code, "governance.revision_required");
    assert.equal((await execute("report_convergence", { intent: "stop" })).converged, false);
  } finally {
    for (const [key, value] of [["EMPIRICA_HOME", previous.home], ["EMPIRICA_REPO_DIR", previous.repo]]) {
      if (value === undefined) delete process.env[key!]; else process.env[key!] = value;
    }
    rmSync(root, { recursive: true, force: true });
  }
});

import { govern, governanceTimeout } from "../src/governance-ui.ts";

test("Pi timeout host setting has a finite shared default and range", () => {
  const prior = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  try {
    for (const value of ["", "0", "1501", "NaN", "Infinity", "invalid"]) {
      process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = value;
      assert.equal(governanceTimeout(), 900_000);
    }
    process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = "1";
    assert.equal(governanceTimeout(), 1000);
  } finally {
    if (prior === undefined) delete process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
    else process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = prior;
  }
});

test("Pi real bridge durable dismissals, singleton consent, partial auto and one decision deadline", async () => {
  const root = mkdtempSync(join(tmpdir(), "empirica-pi-fix-"));
  execFileSync("git", ["init", "-q", root]);
  const old = { home: process.env.EMPIRICA_HOME, repo: process.env.EMPIRICA_REPO_DIR };
  process.env.EMPIRICA_HOME = join(root, "state");
  process.env.EMPIRICA_REPO_DIR = root;
  try {
    const config = { ...defaultBridgeConfig(), cwd: root, env: { ...process.env } };
    const dispatch = createStdioBridgeDispatch(config);
    const ctx = fakeCtx(root);
    ctx.hasUI = true;
    ctx.model = { provider: "anthropic", id: "claude-sonnet-4-6" };
    let models = [ctx.model];
    ctx.modelRegistry = { getAvailable: () => models };
    const graph = { root: "G0", claims: [{ id: "G0", text: "exact scope", kind: "ordinary", gating: true }], edges: [] };
    let sequence = 0;
    const call = async (command: any) => (await dispatch({ protocol: "empirica/v2", request_id: `${++sequence}`, command })).result as any;
    const start = async (session: string, mode = "deliberative") => {
      const id = (await call({ type: "StartRun", goal: "supplied", selector: { project: "p", session }, control_mode: mode })).run.id;
      await call({ type: "ObserveAction", run_id: id, action: { kind: "graph", payload: graph } });
      return id;
    };
    const id = await start("dismiss");
    let dialogs = 0;
    ctx.ui.select = async () => { dialogs++; return undefined; };
    ctx.ui.confirm = async () => true;
    ctx.ui.input = async () => undefined;
    for (let i = 0; i < 3; i++) {
      // New private ingress means a fresh Python process on each call, not adapter memory.
      const result = (await govern(id, ctx, createPrivateIngress())).result as any;
      assert.equal(result.type, "Block");
      assert.equal(result.run.governance.interactions_remaining.proposal, 2 - i);
    }
    const blocked = (await govern(id, ctx, createPrivateIngress())).result as any;
    assert.equal(blocked.reasons[0].code, "governance.interaction_limit");
    assert.equal(dialogs, 3);
    await call({ type: "ObserveAction", run_id: id, action: { kind: "graph", payload: { ...graph, claims: [{ ...graph.claims[0], text: "revision" }] } } });
    const revised = (await govern(id, ctx, createPrivateIngress())).result as any;
    assert.equal(revised.run.governance.interactions_remaining.total, 124);

    const concurrent = await start("concurrent-last-prompt");
    ctx.ui.select = async () => undefined;
    await govern(concurrent, ctx, createPrivateIngress());
    await govern(concurrent, ctx, createPrivateIngress());
    let opened = 0;
    let release!: (choice: string) => void;
    ctx.ui.select = async () => { opened++; return new Promise<string>(resolve => { release = resolve; }); };
    const attempts = [govern(concurrent, ctx, createPrivateIngress()), govern(concurrent, ctx, createPrivateIngress())];
    const loser = (await Promise.race(attempts)).result as any;
    assert.equal(loser.reasons[0].code, "governance.interaction_limit");
    // The winning presentation may still be returning from its private subprocess.
    while (!release) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(opened, 1);
    release("Reject");
    const completed = (await Promise.all(attempts)).map(r => r.result as any);
    assert.ok(completed.some(r => r.run.governance.state === "rejected"));
    assert.equal(opened, 1); // the third reserved prompt can complete; no fourth UI

    const stale = await start("stale-dialog");
    ctx.ui.select = async () => {
      await call({ type: "ObserveAction", run_id: stale, action: { kind: "graph", payload: {
        ...graph, claims: [{ ...graph.claims[0], text: "changed during UI" }] } } });
      return undefined;
    };
    const staleResult = (await govern(stale, ctx, createPrivateIngress())).result as any;
    assert.equal(staleResult.reasons[0].code, "governance.stale_proposal");
    assert.equal(staleResult.run.governance.interactions_remaining.total, 127);

    const single = await start("singleton");
    ctx.ui.select = async (title, options) => title.includes("auditor") ? options[0] : "Approve";
    let consent = false;
    let warning = 0;
    ctx.ui.confirm = async (title) => {
      if (title.includes("SAME MODEL")) { warning++; return consent; }
      return true;
    };
    assert.equal(((await govern(single, ctx, createPrivateIngress())).result as any).type, "Block");
    consent = true;
    let result = (await govern(single, ctx, createPrivateIngress())).result as any;
    assert.notEqual(result.run.governance.state, "approved");
    result = (await govern(single, ctx, createPrivateIngress())).result as any;
    assert.equal(result.run.governance.state, "approved");
    assert.equal(warning, 3); // fresh dedicated consent on the material replacement too

    models = [ctx.model, { provider: "anthropic", id: "claude-opus-4-6" }, { provider: "private", id: "unknown" }];
    const auto = await start("auto", "auto");
    ctx.ui.select = async () => { throw new Error("auto must not show UI"); };
    result = (await govern(auto, ctx, createPrivateIngress())).result as any;
    assert.equal(result.run.governance.state, "approved");
    assert.equal(result.run.governance.approval_kind, "auto");
    assert.equal(result.run.governance.proposal.auditor.model_id, "claude-opus-4-6");
    assert.equal(result.run.governance.context.inventory.members.length, 3);
    models = [ctx.model];
    result = (await govern(await start("auto-single", "auto"), ctx, createPrivateIngress())).result as any;
    assert.equal(result.run.governance.state, "approved");
    assert.equal(result.run.governance.proposal.allow_same_model, true);

    const timed = await start("timeout");
    const now = Date.now;
    let clock = now();
    Date.now = () => clock;
    ctx.ui.select = async () => { clock += 900_001; return "Approve"; };
    try {
      result = (await govern(timed, ctx, createPrivateIngress())).result as any;
      assert.equal(result.type, "Block");
      assert.equal(result.run.governance.interactions_remaining.proposal, 2);
      assert.notEqual(result.run.governance.state, "rejected");
    } finally { Date.now = now; }
  } finally {
    if (old.home === undefined) delete process.env.EMPIRICA_HOME; else process.env.EMPIRICA_HOME = old.home;
    if (old.repo === undefined) delete process.env.EMPIRICA_REPO_DIR; else process.env.EMPIRICA_REPO_DIR = old.repo;
    rmSync(root, { recursive: true, force: true });
  }
});
