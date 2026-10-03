// Proves the adapter registers with Pi: the /empirica command, the tool_call
// gate handler, and the resources_discover handler that contributes the real
// Empirica skill directory. No removed surfaces (agent_settled, tool_result,
// /empirica-status, /report-convergence commands, empirica_knowledge tool).

import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { renderAuthorView } from "../src/author-view.ts";
import { createEmpiricaExtension, DEFAULT_SKILLS_DIR } from "../src/index.ts";
import type { Response } from "../src/contract.ts";
import type { Request } from "../src/contract.ts";
import type { PrivateIngress } from "../src/private-transport.ts";
import { FakePi, fakeCtx } from "./fakes.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function noopDispatch(): Response {
  throw new Error("dispatch should not be called during registration");
}

function register(): FakePi {
  const pi = new FakePi();
  createEmpiricaExtension({ ownerEnv: {}, dispatch: noopDispatch })(pi);
  return pi;
}

test("a malformed governance timeout fails extension load with the variable named", () => {
  const prior = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  try {
    process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = "soon";
    assert.throws(() => createEmpiricaExtension({ ownerEnv: {}, dispatch: noopDispatch }),
      /EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS must be a whole number of seconds from 1 to 1500, got "soon"/);
  } finally {
    if (prior === undefined) delete process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
    else process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = prior;
  }
});

test("registers the /empirica command with a description", () => {
  const pi = register();
  const command = pi.commands.get("empirica");
  assert.ok(command, "expected an 'empirica' command to be registered");
  assert.ok((command.description ?? "").length > 0);
  assert.equal(typeof command.handler, "function");
});

test("does NOT register removed commands", () => {
  const pi = register();
  assert.equal(pi.commands.get("empirica-status"), undefined);
  assert.equal(pi.commands.get("report-convergence"), undefined);
});

test("registers tool, reviewer, and model observation lifecycle hooks", () => {
  const pi = register();
  assert.equal(typeof pi.handlers.get("tool_call"), "function");
  assert.equal(typeof pi.handlers.get("tool_result"), "function");
  assert.equal(typeof pi.handlers.get("model_select"), "function");
  assert.equal(pi.handlers.get("agent_settled"), undefined);
});

test("registers the shared public driving tools", () => {
  const pi = register();
  assert.deepEqual([...pi.tools.keys()].sort(),
    ["empirica_observe", "empirica_read", "report_convergence"]);
});

test("does NOT register legacy empirica_knowledge tool", () => {
  const pi = register();
  assert.equal(pi.tools.get("empirica_knowledge"), undefined);
});

test("resources_discover contributes the empirica skills directory", async () => {
  const pi = register();
  const result = await pi.resourcesDiscover()(
    { cwd: HERE, reason: "startup" },
    { ui: undefined as never },
  );
  assert.deepEqual(result.skillPaths, [DEFAULT_SKILLS_DIR]);
});

test("the contributed skills directory actually holds the empirica skill", () => {
  assert.ok(existsSync(DEFAULT_SKILLS_DIR), `${DEFAULT_SKILLS_DIR} must exist`);
  assert.ok(
    existsSync(path.join(DEFAULT_SKILLS_DIR, "empirica", "SKILL.md")),
    "expected empirica/SKILL.md under the contributed skills dir",
  );
});

test("a custom skillsDir overrides the default", async () => {
  const pi = new FakePi();
  createEmpiricaExtension({ ownerEnv: {}, dispatch: noopDispatch, skillsDir: "/tmp/x" })(pi);
  const result = await pi.resourcesDiscover()(
    { cwd: HERE, reason: "reload" },
    { ui: undefined as never },
  );
  assert.deepEqual(result.skillPaths, ["/tmp/x"]);
});

test("every registered tool declares a JSON-Schema object parameters block", () => {
  const host = new FakePi();
  createEmpiricaExtension({ ownerEnv: {}, dispatch: noopDispatch })(host);
  for (const def of host.tools.values()) {
    const schema = def.parameters as { type?: unknown; properties?: unknown };
    assert.equal(schema.type, "object", `${def.name}: parameters.type must be "object"`);
    assert.equal(typeof schema.properties, "object", `${def.name}: parameters.properties missing`);
  }
});

// D4 at the observe-tool boundary: when the configure_run governance ingress is unavailable, the
// model-facing observe result is the typed governance.approval_unavailable Block over the public
// configure RunView, never a host-level tool failure (untyped rejection).
test("empirica_observe configure_run fails closed when the governance ingress throws", async () => {
  const pi = new FakePi();
  const configureRun = {
    id: "obs-run", status: "active", goal: "g",
    governance: { state: "pending", control_mode: "deliberative" },
  };
  const dispatch = (request: Request): Response => ({
    protocol: "empirica/v2", request_id: request.request_id,
    result: { type: "Allow", converged: false, run: configureRun as never },
  });
  const throwingIngress: PrivateIngress = async () => {
    throw new Error("injected governance ingress unavailable");
  };
  createEmpiricaExtension({ ownerEnv: {}, dispatch, privateIngress: throwingIngress })(pi);
  // Restore the durable run handle; session_start's own refresh throws (ingress down) but the
  // handle is already set, so the observe tool has an active run to gate.
  const ctx = fakeCtx("/work/repo", [{ customType: "empirica.run", data: { runHandle: "obs-run" } }]);
  ctx.hasUI = true;
  const sessionStart = pi.handlers.get("session_start") as
    (e: unknown, c: unknown) => Promise<void>;
  await sessionStart({}, ctx).catch(() => {});
  const observe = pi.tools.get("empirica_observe");
  assert.ok(observe, "empirica_observe must be registered");
  const out = await observe!.execute(
    "call-1", { action: { kind: "configure_run" } },
    new AbortController().signal, () => {}, ctx);
  const details = out.details as Response["result"];
  assert.equal(details.type, "Block");
  if (details.type === "Block")
    assert.equal(details.reasons[0].code, "governance.approval_unavailable");
});

// QUAL-2 C5 measures the final rendered text returned after mediation. The same measured
// ceiling is enforced by Python and Pi.
const POST_MEDIATION_CEILING = 1200;
const APPROVED_CONFIGURE = JSON.parse(readFileSync(
  path.join(HERE, "fixtures", "approved-configure-runview.json"), "utf8")) as
  { type: string; converged: boolean; run: { id: string; governance: Record<string, unknown> } };
const PRESENTATION = { dialog: {
  epoch: 0, control_mode: "deliberative", state: "pending",
  reviews_left: { proposal: 2, total: 127 }, goal: "Review the run configuration below.",
  invocation: { host: "pi", interactive: true, signal: "operator", delegation: false },
  budgets: [
    { key: "max_passes", label: "Investigation passes", short: "passes", help: "Investigation passes the run may use", value: 8, used: 0, minimum: 1, maximum: 1024 },
    { key: "max_spawns", label: "Child spawns", short: "spawns", help: "Non-audit child agents", value: 1, used: 0, minimum: 0, maximum: 128 },
    { key: "max_audit_spawns", label: "Audit spawns", short: "audits", help: "Independent auditor launches", value: 1, used: 0, minimum: 0, maximum: 128 },
  ] }, scope: { root: "C0" } };

function mediatedObserve(choice: "approve" | "dismiss"): {
  observe: () => Promise<{ content: { text: string }[]; details?: unknown }>; } {
  const pi = new FakePi();
  const approvedRun = structuredClone(APPROVED_CONFIGURE.run);
  const pendingRun = structuredClone(APPROVED_CONFIGURE.run);
  (pendingRun.governance as { state: string }).state = "pending";
  const allow = (run: unknown, withPresentation: boolean): Record<string, unknown> => ({
    protocol: "empirica/v2", request_id: "trusted-governance",
    result: withPresentation
      ? { type: "Allow", converged: false, run, presentation: PRESENTATION }
      : { type: "Allow", converged: false, run },
  });
  const dispatch = (request: Request): Response => ({
    protocol: "empirica/v2", request_id: request.request_id,
    result: { type: "Allow", converged: false, run: pendingRun as never },
  });
  const trusted: PrivateIngress = async (request) => {
    if (request.operation === "governance_context") return allow(pendingRun, true);
    const payload = request.payload as { outcome?: string; submission?: { action: string } };
    if (payload.outcome === "present") return allow(pendingRun, true);
    if (payload.submission?.action === "approve") return allow(approvedRun, true);
    return allow(pendingRun, false); // dismiss / stored
  };
  createEmpiricaExtension({ ownerEnv: {}, dispatch, privateIngress: trusted })(pi);
  const ctx = fakeCtx("/work/repo",
    [{ customType: "empirica.run", data: { runHandle: APPROVED_CONFIGURE.run.id } }]);
  ctx.hasUI = true;
  ctx.ui.custom = async factory => await new Promise(resolve => {
    const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
    component.handleInput(choice === "approve" ? "\r" : "\x1b");
    if (choice === "approve") component.handleInput("\r");
  });
  return {
    observe: async () => {
      const sessionStart = pi.handlers.get("session_start") as
        (e: unknown, c: unknown) => Promise<void>;
      await sessionStart({}, ctx).catch(() => {});
      const tool = pi.tools.get("empirica_observe");
      assert.ok(tool, "empirica_observe must be registered");
      return tool!.execute("call-1", { action: { kind: "configure_run" } },
        new AbortController().signal, () => {}, ctx);
    },
  };
}

test("Pi rendered-text ceiling matches Python and covers audit-pending and terminal views", () => {
  assert.equal(POST_MEDIATION_CEILING, 1200, "Pi and Python must share the measured ceiling");
  for (const name of ["block-pending-audit", "allow-converged"]) {
    const golden = JSON.parse(readFileSync(
      path.join(HERE, "author-view-golden", `${name}.json`), "utf8"));
    const text = renderAuthorView(golden.result);
    assert.equal(text, golden.text, `${name} must measure the final rendered text`);
    assert.ok(text.length < POST_MEDIATION_CEILING,
      `${name} rendered text ${text.length} must stay under ${POST_MEDIATION_CEILING}`);
  }
});

test("empirica_observe configure_run approve: model-visible size stays under the ceiling, no private leak", async () => {
  const out = await mediatedObserve("approve").observe();
  const details = out.details as Response["result"];
  assert.equal(details.type, "Allow", "approve must return Allow");
  if (details.type === "Allow")
    assert.equal((details.run?.governance as { state?: string }).state, "approved");
  const text = out.content[0].text;
  // The private presentation injected on the mediated responses must be stripped before display.
  for (const key of ["presentation", "dialog", "\"scope\""])
    assert.ok(!text.includes(key), `model-visible text leaked ${key}`);
  assert.ok(text.length < POST_MEDIATION_CEILING,
    `post-mediation approve text ${text.length} must stay under ${POST_MEDIATION_CEILING}`);
});

test("empirica_observe configure_run dismiss: pending Block model-visible size stays under the ceiling", async () => {
  const out = await mediatedObserve("dismiss").observe();
  const details = out.details as Response["result"];
  assert.equal(details.type, "Block", "dismiss must return the documented pending Block");
  if (details.type === "Block")
    assert.equal(details.reasons[0].code, "governance.approval_unavailable");
  const text = out.content[0].text;
  assert.ok(text.length < POST_MEDIATION_CEILING,
    `post-mediation dismiss text ${text.length} must stay under ${POST_MEDIATION_CEILING}`);
});

// Dialog lock: Pi runs tool calls in parallel, so the lock must be taken before the first await.
function lockHarness() {
  const pi = new FakePi();
  const approvedRun = structuredClone(APPROVED_CONFIGURE.run);
  const pendingRun = structuredClone(APPROVED_CONFIGURE.run);
  (pendingRun.governance as { state: string }).state = "pending";
  const autoRun = structuredClone(APPROVED_CONFIGURE.run);
  Object.assign(autoRun.governance, { state: "revision_pending", control_mode: "auto", first_approval: true });
  let current: unknown = pendingRun;
  const deferred: Array<{ resolve: (run: unknown) => void }> = [];
  const allow = (run: unknown, withPresentation: boolean): Record<string, unknown> => ({
    protocol: "empirica/v2", request_id: "trusted-governance",
    result: withPresentation
      ? { type: "Allow", converged: false, run, presentation: PRESENTATION }
      : { type: "Allow", converged: false, run },
  });
  const dispatch = (request: Request): Promise<Response> | Response => {
    const respond = (run: unknown): Response => ({ protocol: "empirica/v2", request_id: request.request_id,
      result: { type: "Allow", converged: false, run: run as never } });
    if (request.command.type !== "ObserveAction") return respond(pendingRun);
    return new Promise<Response>(resolve => deferred.push({ resolve: run => {
      current = run; resolve(respond(run));
    } }));
  };
  const trusted: PrivateIngress = async (request) => {
    if (request.operation === "governance_context") return allow(current, true);
    const payload = request.payload as { outcome?: string; submission?: { action: string } };
    if (payload.outcome === "present") return allow(pendingRun, true);
    if (payload.submission?.action === "approve") return allow(approvedRun, true);
    return allow(approvedRun, false); // outcome "approve": auto-approval, no dialog
  };
  createEmpiricaExtension({ ownerEnv: {}, dispatch, privateIngress: trusted })(pi);
  const ctx = fakeCtx("/work/repo",
    [{ customType: "empirica.run", data: { runHandle: APPROVED_CONFIGURE.run.id } }]);
  ctx.hasUI = true;
  const dialogs: Array<{ handleInput(data: string): void }> = [];
  ctx.ui.custom = async factory => await new Promise(resolve => {
    dialogs.push(factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve));
  });
  const observe = async () => {
    const sessionStart = pi.handlers.get("session_start") as (e: unknown, c: unknown) => Promise<void>;
    await sessionStart({}, ctx).catch(() => {});
    const tool = pi.tools.get("empirica_observe");
    assert.ok(tool, "empirica_observe must be registered");
    return () => tool!.execute("call", { action: { kind: "configure_run" } },
      new AbortController().signal, () => {}, ctx);
  };
  return { dialogs, deferred, pendingRun, autoRun, observe,
    approveDialog: (index: number) => { dialogs[index].handleInput("\r"); dialogs[index].handleInput("\r"); } };
}

async function settle(until: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !until(); i += 1) await new Promise(resolve => setImmediate(resolve));
  assert.ok(until(), "condition was not reached");
}

test("empirica_observe: concurrent configure_run opens exactly one dialog and rejects the second", async () => {
  const h = lockHarness();
  const execute = await h.observe();
  const first = execute();
  // The second call arrives while the first is still awaiting dispatch.
  await assert.rejects(execute(), /Governance dialog in progress/);
  assert.equal(h.deferred.length, 1, "the rejected call must not dispatch");
  h.deferred[0].resolve(h.pendingRun);
  await settle(() => h.dialogs.length === 1);
  await assert.rejects(execute(), /Governance dialog in progress/);
  assert.equal(h.dialogs.length, 1, "exactly one dialog may be open");
  h.approveDialog(0);
  const out = await first;
  assert.equal((out.details as Response["result"]).type, "Allow");
  assert.equal(h.dialogs.length, 1);
  // Released once the owner finishes: the next configure_run reaches dispatch.
  const next = execute();
  await settle(() => h.deferred.length === 2);
  h.deferred[1].resolve(h.pendingRun);
  await settle(() => h.dialogs.length === 2);
  h.approveDialog(1);
  await next;
});

test("empirica_observe: a configure_run that opens no dialog never clears a held dialog lock", async () => {
  const h = lockHarness();
  const execute = await h.observe();
  const owner = execute();
  h.deferred[0].resolve(h.pendingRun);
  await settle(() => h.dialogs.length === 1);
  // Post-approval auto (no dialog) while the dialog is held: rejected, and the lock stays held.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await assert.rejects(execute(), /Governance dialog in progress/);
    assert.equal(h.deferred.length, 1, "a rejected call must not dispatch");
  }
  h.approveDialog(0);
  await owner;
  // The owner released the lock exactly once; the auto configure_run now runs to completion
  // without a dialog, and releases the lock itself.
  const auto = execute();
  await settle(() => h.deferred.length === 2);
  h.deferred[1].resolve(h.autoRun);
  const out = await auto;
  assert.equal((out.details as Response["result"]).type, "Allow");
  assert.equal(h.dialogs.length, 1, "auto approval must not open a dialog");
  const after = execute();
  await settle(() => h.deferred.length === 3);
  h.deferred[2].resolve(h.autoRun);
  await after;
});
