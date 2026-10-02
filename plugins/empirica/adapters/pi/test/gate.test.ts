// End-to-end through the registered handlers: the /empirica command and the
// tool_call convergence gate. Dispatch is a fake that records requests and
// returns scripted decisions — no core, no bridge, no Pi runtime. Proves the
// central guard rejects malformed responses so they cannot permit the hard gate.

import { test } from "node:test";
import assert from "node:assert/strict";

import { PROTOCOL, type Request, type Response, type Result } from "../src/contract.ts";
import { REPORT_CONVERGENCE_TOOL, SUBAGENT_TOOL } from "../src/translate.ts";
import {
  BUDGET_EXHAUSTED_NOTICE, budgetExhaustedWait, HUMAN_WAIT_NOTICE, humanApprovalWait,
} from "../src/governance-ui.ts";
import {
  createEmpiricaExtension, DEFAULT_SKILLS_DIR, defaultAuditContractResolver, resolvePiAuditorModel,
  withoutThinkingLevel,
} from "../src/index.ts";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { FakePi, FakeUi, fakeCtx } from "./fakes.ts";
import type { ToolCallEvent, ToolInfo, ToolResultEvent } from "../src/pi-types.ts";
import type { OwnerEnv } from "../src/runtime-owner.ts";
import { PUBLIC_TOOLS } from "../src/public-tools.ts";
import { makeSubagentsPackage, tempParent } from "./owner-fixture.ts";
import type { PrivateIngressRequest } from "../src/private-transport.ts";

const HANDLE = "run-handle-1";

test("Pi auditor model follows host settings precedence; identity policy owns equivalence", () => {
  const global = { subagents: { agentOverrides: {
    "empirica.empirica-auditor": { model: "global/override" } }, defaultModel: "global/default" } };
  const project = { subagents: { agentOverrides: {
    "empirica.empirica-auditor": { model: "project/override" } }, defaultModel: "project/default" } };
  assert.equal(resolvePiAuditorModel(global, project, "main/model"), "project/override");
  assert.equal(resolvePiAuditorModel(global, { subagents: { defaultModel: "project/default" } }, "main/model"),
    "global/override");
  assert.equal(resolvePiAuditorModel({}, { subagents: { defaultModel: "project/default" } }, "main/model"),
    "project/default");
  assert.equal(resolvePiAuditorModel({}, {}, "main/model"), "main/model");
});

test("production Pi auditor resolver tries project then user scope", async () => {
  const expectedAgent = resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md");
  const scopes: unknown[] = [];
  const ctx = fakeCtx("/work");
  ctx.model = { provider: "main", id: "model" };
  ctx.modelRegistry = { getAvailable: () => [{ provider: "audit", id: "model" }] };
  const resolved = await defaultAuditContractResolver(
    { agent: "empirica.empirica-auditor", task: "audit", expectedAgent }, ctx,
    {
      settings: {
        getAgentDir: () => "/agent",
        SettingsManager: { create: () => ({
          getGlobalSettings: () => ({}),
          getProjectSettings: () => ({ subagents: { defaultModel: "audit/model" } }),
        }) },
      },
      preflight: { resolveSubagentLaunchContract: async input => {
        scopes.push(input.agentScope);
        return { ok: true, contract: { agent: { filePath: input.agentScope === "project"
          ? "/shadow/auditor.md" : expectedAgent }, model: "audit/model", modelCandidates: ["audit/model"] } };
      } },
    },
  );
  assert.deepEqual(scopes, ["project", "user"]);
  assert.equal(resolved.agentScope, "user");
});

// P1-D1 (native Pi qualification): preflight appends the auditor file's `thinking` level to the candidate.
function suffixedResolver(candidate: string | string[], configured = "audit/model") {
  const candidates = typeof candidate === "string" ? [candidate] : candidate;
  const expectedAgent = resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md");
  const ctx = fakeCtx("/work");
  ctx.model = { provider: "main", id: "model" };
  ctx.modelRegistry = { getAvailable: () => [{ provider: "audit", id: "model" }] };
  return defaultAuditContractResolver(
    { agent: "empirica.empirica-auditor", task: "audit", expectedAgent }, ctx,
    {
      settings: {
        getAgentDir: () => "/agent",
        SettingsManager: { create: () => ({
          getGlobalSettings: () => ({ subagents: { defaultModel: configured } }),
          getProjectSettings: () => ({}),
        }) },
      },
      preflight: { resolveSubagentLaunchContract: async () => ({ ok: true, contract: {
        agent: { filePath: expectedAgent }, model: candidates[0], modelCandidates: candidates,
      } }) },
    },
  );
}

test("production Pi auditor resolver accepts the configured model with the agent's thinking level", async () => {
  assert.equal((await suffixedResolver("audit/model:high")).model, "audit/model");
});

test("production Pi auditor resolver keeps a configured thinking level for the launch", async () => {
  assert.equal((await suffixedResolver("audit/model:max", "audit/model:max")).model, "audit/model:max");
  assert.equal((await suffixedResolver("audit/model:high", "audit/model:max")).model, "audit/model:max");
});

test("production Pi auditor resolver still refuses any substituted model carrying a thinking level", async () => {
  for (const candidates of [["other/model:high"], ["audit/other:high"], ["fallback/model:high", "audit/model:high"]])
    await assert.rejects(suffixedResolver(candidates), /substituted by preflight/, candidates.join(","));
  await assert.rejects(suffixedResolver("audit/other:max", "audit/model:max"), /substituted by preflight/);
});

test("withoutThinkingLevel strips only a known thinking level", () => {
  assert.deepEqual(["a/b:high", "a/b:max", "a/b", "a/b:v1:0", "a/b:v1:0:low"].map(withoutThinkingLevel),
                   ["a/b", "a/b", "a/b", "a/b:v1:0", "a/b:v1:0"]);
});

test("production Pi auditor resolver blocks a registry-unavailable configured model", async () => {
  const expectedAgent = resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md");
  const ctx = fakeCtx("/work");
  ctx.model = { provider: "main", id: "model" };
  ctx.modelRegistry = { getAvailable: () => [] };
  await assert.rejects(defaultAuditContractResolver(
    { agent: "empirica.empirica-auditor", task: "audit", expectedAgent }, ctx,
    {
      settings: {
        getAgentDir: () => "/agent",
        SettingsManager: { create: () => ({
          getGlobalSettings: () => ({ subagents: { defaultModel: "stale/model" } }),
          getProjectSettings: () => ({}),
        }) },
      },
      preflight: { resolveSubagentLaunchContract: async () => ({ ok: true, contract: {
        agent: { filePath: expectedAgent }, model: "stale/model", modelCandidates: ["stale/model"],
      } }) },
    },
  ), /configured auditor model is unavailable/);
});

function envelope(result: Result, requestId = "x"): Response {
  return { protocol: PROTOCOL, request_id: requestId, result };
}
function run(status = "active") {
  return { id: HANDLE, status: status as never, governance: { state: "approved",
    proposal: { budgets: {} } } };
}

interface Wired {
  pi: FakePi;
  requests: Request[];
  privateRequests: PrivateIngressRequest[];
  auditResolutions: Record<string, unknown>[];
}

interface WireOptions {
  /** Replaces the fixture owner registrations before ``session_start`` (default: one healthy owner). */
  owners?: ToolInfo[];
  /** Environment the owner resolver sees (default: none set). */
  env?: OwnerEnv;
}

async function wire(
  responder: (req: Request) => Response,
  echoRequestId = true,
  classify: (provider: unknown, model: unknown) => string | null =
    (provider, model) => `${String(provider)}/${String(model)}`,
  options: WireOptions = {},
): Promise<Wired> {
  const requests: Request[] = [];
  const privateRequests: PrivateIngressRequest[] = [];
  const auditResolutions: Record<string, unknown>[] = [];
  const pi = new FakePi();
  if (options.owners) pi.subagentOwners = options.owners;
  const dispatch = (req: Request): Response => {
    requests.push(req);
    const resp = responder(req);
    // Echo the request_id so the guard's correlation check passes. Tests that
    // deliberately test a mismatch pass echoRequestId=false.
    return echoRequestId ? { ...resp, request_id: req.request_id } : resp;
  };
  createEmpiricaExtension({
    dispatch,
    deriveSelector: () => ({ project: "p", session: "s" }),
    privateIngress: async (request) => {
      if (request.operation === "governance_context") return { protocol: PROTOCOL,
        request_id: "trusted-governance", result: { type: "Inert", reason: "unsupported_host_event", run: run() } };
      privateRequests.push(request);
      if (request.operation === "classify_identity") {
        const payload = request.payload as { provider_id?: unknown; model_id?: unknown } | undefined;
        const identity = classify(payload?.provider_id, payload?.model_id);
        return identity === null ? null as unknown as Record<string, unknown> : { identity };
      }
      if (request.operation === "audit_prepare") return {
        type: "audit_plan",
        plan: { child_id: "ch-1", role_profile: "empirica.empirica-auditor",
          operation_id: `sha256:${"b".repeat(64)}`,
          argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } },
      };
      if (request.operation === "audit_verdict")
        return { type: "audit_verdict", admitted: true };
      if (request.operation === "audit_start") return { type: "audit_started" };
      if (request.operation === "audit_identity") return { type: "audit_identity" };
      if (["audit_reject", "audit_failure"].includes(request.operation))
        return { type: "audit_terminal" };
      return { protocol: PROTOCOL, request_id: "trusted", result: {
        type: "Inert", reason: "unsupported_host_event", run: run() } };
    },
    ownerEnv: options.env ?? {},
    resolveAuditContract: async (input, _ctx, preflight) => {
      // Which package's preflight the adapter handed over: the owner's own sentinel answer.
      const answer = await preflight.resolveSubagentLaunchContract({});
      auditResolutions.push({ ...input, preflightAnswer: answer.ok ? "ok" : answer.message });
      return { agentFilePath: resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md"),
        model: "bedrock/auditor-model", agentScope: "project" };
    },
  })(pi);
  await pi.sessionStart();
  return { pi, requests, privateRequests, auditResolutions };
}

function toolEvent(toolName: string): ToolCallEvent {
  return { toolName, toolCallId: "tc-1", input: {} };
}

async function startRun(w: Wired): Promise<void> {
  const ctx = fakeCtx("/work", [
    { customType: "empirica.run", data: { runHandle: HANDLE } },
  ]);
  await (w.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, ctx);
}

// --- /empirica ---------------------------------------------------------------

test("/empirica dispatches StartRun and persists the opaque handle", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  const ui = new FakeUi();

  await w.pi.command("empirica").handler("build the thing", { ui, mode: "tui" });

  assert.equal(w.requests.length, 1);
  assert.equal(w.requests[0].command.type, "StartRun");
  if (w.requests[0].command.type === "StartRun") assert.deepEqual(w.requests[0].command.invocation,
    { host: "pi", interactive: true, signal: "ctx.mode=tui", delegation: false });
  assert.equal(w.pi.entries.length, 1);
  assert.equal(w.pi.entries[0].customType, "empirica.run");
  assert.match(w.pi.modelMessages[0].content, /empirica_observe/);
  assert.equal(w.pi.userMessages.length, 1);
  assert.deepEqual(w.pi.userMessageOptions, [{ deliverAs: "followUp" }]);
  assert.match(w.pi.userMessages[0], /^# Empirica/);
  assert.match(w.pi.userMessages[0], /The user invocation is `build the thing`/);
  assert.match(w.pi.userMessages[0], /"kind":"route","reason":/);
  assert.match(w.pi.userMessages[0], /operation="GetRun"/);
  assert.match(w.pi.userMessages[0], /Pi calls the\npackaged auditor/);
  assert.doesNotMatch(w.pi.userMessages[0], /\$ARGUMENTS/);
});

test("/empirica records every Pi mode and operator delegation", async () => {
  const prior = process.env.EMPIRICA_AUTO_DELEGATION;
  try {
    for (const [mode, interactive] of [["tui", true], ["rpc", true], ["print", false], ["json", false]] as const) {
      for (const delegated of [false, true]) {
        if (delegated) process.env.EMPIRICA_AUTO_DELEGATION = "1";
        else delete process.env.EMPIRICA_AUTO_DELEGATION;
        const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
        await w.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode });
        if (w.requests[0].command.type === "StartRun") assert.deepEqual(w.requests[0].command.invocation,
          { host: "pi", interactive, signal: `ctx.mode=${mode}`, delegation: delegated });
      }
    }
  } finally {
    if (prior === undefined) delete process.env.EMPIRICA_AUTO_DELEGATION;
    else process.env.EMPIRICA_AUTO_DELEGATION = prior;
  }
});

test("/empirica preserves replacement tokens in the literal goal", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await w.pi.command("empirica").handler("keep $& and $$ literal", { ui: new FakeUi() });
  assert.match(w.pi.userMessages[0], /The user invocation is `keep \$& and \$\$ literal`/);
  assert.doesNotMatch(w.pi.userMessages[0], /\$ARGUMENTS/);
});

test("/empirica empty goal reports the core refusal without starting", async () => {
  const w = await wire(() => envelope({ type: "Block", reasons: [
    { code: "run.goal_required", message: "A non-empty goal is required" },
  ] }));
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("", { ui, mode: "json" });
  assert.equal(w.pi.userMessages.length, 0);
  assert.equal(w.pi.entries.length, 0);
  assert.match(ui.notifications[0].message, /non-empty goal is required/);
  if (w.requests[0].command.type === "StartRun")
    assert.equal(w.requests[0].command.invocation?.interactive, false);
});

test("/empirica rejects a busy session before creating a run", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("build the thing", { ui, isIdle: () => false });
  assert.equal(w.requests.length, 0);
  assert.equal(w.pi.entries.length, 0);
  assert.equal(w.pi.userMessages.length, 0);
  assert.match(ui.notifications[0].message, /requires an idle session/);
  assert.equal(ui.notifications[0].type, "warning");
});

test("/empirica refuses unknown flags (including withdrawn modes) without starting", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("--cli-exec --multi-provider build the thing", { ui });
  assert.equal(w.requests.length, 0);
  assert.equal(w.pi.entries.length, 0);
  assert.equal(w.pi.userMessages.length, 0);
  assert.match(ui.notifications[0].message, /not started — unknown flags: --cli-exec --multi-provider/);
  assert.equal(ui.notifications[0].type, "error");
});

const reasonMessage = (code: string): string => PUBLIC_TOOLS.recovery[code].message;

test("/empirica refuses to start when the pi-subagents tool is not active (P1b)", async () => {
  for (const hide of [(pi: FakePi) => { pi.activeTools = []; },
                      (pi: FakePi) => { Object.assign(pi, { getActiveTools: undefined }); }]) {
    const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
    hide(w.pi);
    const ui = new FakeUi();
    await w.pi.command("empirica").handler("build the thing", { ui });
    assert.equal(w.requests.length, 0);
    assert.equal(w.pi.entries.length, 0);
    assert.equal(w.pi.userMessages.length, 0);
    assert.equal(w.pi.modelMessages.length, 0);
    assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_missing")),
      ui.notifications[0].message);
    assert.match(ui.notifications[0].message, /missing-tool: the `subagent` tool is registered but not active/);
    assert.equal(ui.notifications[0].type, "error");
  }
});

// --- active owner (Empirica 4.1): refusal before any run exists ---------------------------------

const otherOwner = (version = "0.64.0", sentinel = "other"): ToolInfo =>
  makeSubagentsPackage(tempParent(), { version, sentinel }).tool();

const OWNER_REFUSALS: Array<[string, () => WireOptions, string]> = [
  ["no subagent tool registered", () => ({ owners: [] }), "host.subagents_missing"],
  // Pi reports one `subagent` (first wins); the second copy shows only through its slash commands.
  ["two pi-subagents copies loaded (one subagent tool reported)", () => ({ owners: [otherOwner(), otherOwner("0.74.0")] }),
    "host.subagents_duplicate_owner"],
  ["a pi-subagents child process", () => ({ env: { PI_SUBAGENT_CHILD: "1" } }), "host.subagents_owner_unverified"],
  ["an owner outside any pi-subagents package", () => ({ owners: [
    makeSubagentsPackage(tempParent(), { name: "not-subagents" }).tool()] }), "host.subagents_owner_unverified"],
  ["an owner without a readable version", () => ({ owners: [
    makeSubagentsPackage(tempParent(), { version: null }).tool()] }), "host.subagents_owner_unverified"],
  ["an owner without the preflight export", () => ({ owners: [
    makeSubagentsPackage(tempParent(), { preflight: "export const other = 1;\n" }).tool()] }),
    "host.subagents_version_unsupported"],
];

for (const [name, options, reason] of OWNER_REFUSALS) {
  test(`/empirica is refused before StartRun: ${name} (${reason})`, async () => {
    const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined, options());
    const ui = new FakeUi();
    await w.pi.command("empirica").handler("build the thing", { ui });
    assert.equal(w.requests.length, 0, "no run may be created");
    assert.equal(w.pi.entries.length, 0);
    assert.equal(w.pi.userMessages.length, 0);
    assert.ok(ui.notifications[0].message.includes(reasonMessage(reason)), ui.notifications[0].message);
    assert.equal(ui.notifications[0].type, "error");
  });

  test(`configure_run is refused with its contract reason: ${name} (${reason})`, async () => {
    const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined, options());
    const ctx = fakeCtx("/work", [{ customType: "empirica.run", data: { runHandle: HANDLE } }]);
    await w.pi.sessionStart(ctx);
    const observe = w.pi.tools.get("empirica_observe");
    assert.ok(observe);
    const out = await observe.execute("c1", { action: { kind: "configure_run",
      budgets: { max_passes: 4, max_spawns: 1, max_audit_spawns: 1 }, rationale: "small" } },
      new AbortController().signal, () => {}, ctx);
    const details = out.details as Result;
    assert.equal(details.type, "Block");
    assert.deepEqual(details.type === "Block" ? details.reasons.map((item) => item.code) : [], [reason]);
    assert.ok(out.content[0].text.includes(reasonMessage(reason)), out.content[0].text);
    assert.deepEqual(w.requests.map((request) => request.command.type), ["GetRun"],
      "no ObserveAction (and so no governance dialog) may be dispatched");
  });
}

test("the fake host is faithful to Pi: two loaded copies yield one subagent tool but two package commands", () => {
  const pi = new FakePi();
  pi.subagentOwners = [otherOwner("0.64.0", "first"), otherOwner("0.74.0", "second")];
  assert.equal(pi.getAllTools().filter((tool) => tool.name === SUBAGENT_TOOL).length, 1, "first registrant wins");
  assert.equal(pi.getAllTools().find((tool) => tool.name === SUBAGENT_TOOL)?.sourceInfo.path,
    pi.subagentOwners[0].sourceInfo.path);
  const doctors = pi.getCommands().filter((command) => command.name.startsWith("subagents-doctor"));
  assert.deepEqual(doctors.map((command) => command.sourceInfo.path),
    pi.subagentOwners.map((tool) => tool.sourceInfo.path));
});

test("a second copy visible only through commands refuses /empirica and configure_run as a duplicate owner", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined,
    { owners: [otherOwner("0.64.0", "first"), otherOwner("0.74.0", "second")] });
  assert.equal(w.pi.getAllTools().filter((tool) => tool.name === SUBAGENT_TOOL).length, 1);
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("goal", { ui });
  assert.equal(w.requests.length, 0);
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_duplicate_owner")), ui.notifications[0].message);
  assert.match(ui.notifications[0].message, /multiple-owners: 2 distinct pi-subagents packages are loaded/);
  const ctx = fakeCtx("/work", [{ customType: "empirica.run", data: { runHandle: HANDLE } }]);
  await w.pi.sessionStart(ctx);
  const out = await w.pi.tools.get("empirica_observe")!.execute("c1", { action: { kind: "configure_run",
    budgets: { max_passes: 4, max_spawns: 1, max_audit_spawns: 1 }, rationale: "small" } },
    new AbortController().signal, () => {}, ctx);
  const details = out.details as Result;
  assert.equal(details.type === "Block" ? details.reasons[0].code : details.type, "host.subagents_duplicate_owner");
});

test("an unavailable tool inventory is an unobservable owner, not a crash", async () => {
  const pi = new FakePi();
  pi.inventoryError = new Error("inventory down");
  const ui = new FakeUi();
  createEmpiricaExtension({ ownerEnv: {}, dispatch: () => { throw new Error("no dispatch expected"); } })(pi);
  await pi.sessionStart();
  await pi.command("empirica").handler("goal", { ui });
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_owner_unverified")));
  assert.match(ui.notifications[0].message, /could not be observed: inventory down/);
});

test("an unavailable command inventory is an unobservable owner, not a crash", async () => {
  const pi = new FakePi();
  pi.commandsError = new Error("commands down");
  const ui = new FakeUi();
  createEmpiricaExtension({ ownerEnv: {}, dispatch: () => { throw new Error("no dispatch expected"); } })(pi);
  await pi.sessionStart();
  await pi.command("empirica").handler("goal", { ui });
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_owner_unverified")));
  assert.match(ui.notifications[0].message, /commands down/);
});

test("a malformed inventory cannot escape session_start: the run is restored and still gated (fails closed)", async () => {
  const w = await wire((req) => req.command.type === "EvaluateRun"
    ? envelope({ type: "Block", run: run(), reasons: [{ code: "claim.research_missing", message: "3 claims lack evidence" }] })
    : envelope({ type: "Allow", converged: false, run: run() }), true, undefined, { owners: [null as never] });
  // The run handle is restored by a session_start whose owner inventory is malformed.
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.deepEqual(decision, { block: true, reason: "3 claims lack evidence\nhandle: run-handle-1" },
    "the restored handle must be live, so report_convergence is gated");
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("goal", { ui });
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_owner_unverified")), ui.notifications[0].message);
});

test("a /empirica issued before session_start observed an owner is refused, not assumed", async () => {
  const pi = new FakePi();
  const ui = new FakeUi();
  createEmpiricaExtension({ ownerEnv: {}, dispatch: () => { throw new Error("no dispatch expected"); } })(pi);
  await pi.command("empirica").handler("goal", { ui });
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_owner_unverified")));
  assert.match(ui.notifications[0].message, /not observed at session_start/);
});

test("a healthy single owner starts the run", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await w.pi.command("empirica").handler("build the thing", { ui: new FakeUi() });
  assert.deepEqual(w.requests.map((request) => request.command.type), ["StartRun"]);
});

// --- active owner: the correlated audit binds to the owner observed at admission ---------------

const AUDIT_CHILD = { child_id: "ch-1", purpose: "audit", resource_class: "audit", state: "reserved" };
const auditResponder = (req: Request): Response => req.command.type === "ObserveAction"
  ? envelope({ type: "Allow", converged: false, run: { ...run(), children: [AUDIT_CHILD] } as never })
  : req.command.type === "GetArgument"
    ? envelope({ type: "Allow", converged: false, run: run(), argument: { artifacts: [] } } as never)
    : envelope({ type: "Allow", converged: false, run: run() });
const launchAuditor = (w: Wired, id: string) => w.pi.toolCall()(
  { toolName: SUBAGENT_TOOL, toolCallId: id, input: { agent: "empirica.empirica-auditor", task: "audit" } },
  fakeCtx());

test("the audit launch contract is resolved through the registered owner's own preflight", async () => {
  const w = await wire(auditResponder, true, undefined,
    { owners: [makeSubagentsPackage(tempParent(), { sentinel: "registered-owner" }).tool()] });
  await startRun(w);
  assert.equal(await launchAuditor(w, "tc-owner"), undefined);
  assert.equal(w.auditResolutions.length, 1);
  assert.equal(w.auditResolutions[0].preflightAnswer, "preflight:registered-owner");
});

test("an owner that changes after the snapshot refuses the audit before any reservation", async () => {
  const original = makeSubagentsPackage(tempParent(), { version: "0.74.0" }).tool();
  const w = await wire(auditResponder, true, undefined, { owners: [original] });
  await startRun(w);
  const before = w.requests.length;
  w.pi.subagentOwners = [otherOwner("0.64.0", "replacement")];
  const decision = await launchAuditor(w, "tc-changed");
  assert.equal(decision?.block, true);
  assert.ok(decision?.reason?.includes(reasonMessage("host.subagents_owner_unverified")), decision?.reason);
  assert.match(decision?.reason ?? "", /owner changed from .*@0\.74\.0 to .*@0\.64\.0/);
  assert.equal(w.auditResolutions.length, 0, "the replacement's preflight must never be consulted");
  assert.deepEqual(w.privateRequests, [], "no audit plan, identity, or start may be requested");
  // Only the investigation witness every subagent launch records; never a child reservation.
  assert.deepEqual(w.requests.slice(before).map((request) => request.command.type === "ObserveAction"
    ? request.command.action.kind : request.command.type), ["investigate"]);
  // The invalidation is sticky: restoring the original registration cannot revive this snapshot
  // (a fresh resolution would now equal the binding), and configure_run refuses with the contract reason.
  w.pi.subagentOwners = [original];
  const ctx = fakeCtx("/work");
  const observe = w.pi.tools.get("empirica_observe");
  assert.ok(observe);
  const out = await observe.execute("c2", { action: { kind: "configure_run",
    budgets: { max_passes: 4, max_spawns: 1, max_audit_spawns: 1 }, rationale: "small" } },
    new AbortController().signal, () => {}, ctx);
  const details = out.details as Result;
  assert.equal(details.type === "Block" ? details.reasons[0].code : details.type, "host.subagents_owner_unverified");
  // ...and so does a later audit launch, although the registration is the original again.
  const later = await launchAuditor(w, "tc-after-restore");
  assert.equal(later?.block, true);
  assert.ok(later?.reason?.includes(reasonMessage("host.subagents_owner_unverified")), later?.reason);
});

test("a deactivated subagent tool refuses the audit without poisoning the session: reactivating admits it", async () => {
  const w = await wire(auditResponder);
  await startRun(w);
  w.pi.activeTools = [];
  const refused = await launchAuditor(w, "tc-off");
  assert.equal(refused?.block, true);
  assert.ok(refused?.reason?.includes(reasonMessage("host.subagents_missing")), refused?.reason);
  w.pi.activeTools = ["subagent"];
  assert.equal(await launchAuditor(w, "tc-on"), undefined, "the unchanged owner is admitted again");
  assert.equal(w.auditResolutions.length, 1);
});

test("a vanished, deactivated, or duplicated owner refuses the audit at admission", async () => {
  const cases: Array<[string, (pi: FakePi) => void, string]> = [
    ["vanished", (pi) => { pi.subagentOwners = []; }, "host.subagents_missing"],
    ["duplicated", (pi) => { pi.subagentOwners = [...pi.subagentOwners, otherOwner()]; }, "host.subagents_duplicate_owner"],
    ["inventory failure", (pi) => { pi.inventoryError = new Error("boom"); }, "host.subagents_owner_unverified"],
  ];
  for (const [name, change, reason] of cases) {
    const w = await wire(auditResponder);
    await startRun(w);
    change(w.pi);
    const decision = await launchAuditor(w, `tc-${name}`);
    assert.equal(decision?.block, true, name);
    assert.ok(decision?.reason?.includes(reasonMessage(reason)), `${name}: ${decision?.reason}`);
    assert.deepEqual(w.privateRequests, [], name);
  }
  const inactive = await wire(auditResponder);
  await startRun(inactive);
  inactive.pi.activeTools = [];
  const decision = await launchAuditor(inactive, "tc-inactive");
  assert.equal(decision?.block, true);
  assert.ok(decision?.reason?.includes(reasonMessage("host.subagents_missing")), decision?.reason);
});

// --- tool_call gate ----------------------------------------------------------

test("gate: report_convergence tool is blocked with the reason on Block", async () => {
  const w = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Block", run: run(), reasons: [{ code: "claim.research_missing", message: "3 claims lack evidence" }] }),
  );
  await startRun(w);

  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.deepEqual(decision, { block: true, reason: "3 claims lack evidence\nhandle: run-handle-1" });

  const gate = w.requests.at(-1)!;
  assert.equal(gate.command.type, "EvaluateRun");
  assert.equal(
    gate.command.type === "EvaluateRun" ? gate.command.intent : null,
    "report_convergence",
  );
});

function waitingRun(overrides: Record<string, unknown> = {}, context: Record<string, unknown> = {},
                    controlMode = "deliberative") {
  return { id: HANDLE, status: "active" as never, governance: {
    state: "pending", control_mode: controlMode, first_approval: false, prompt_error: null,
    interactions_remaining: { proposal: 3, total: 128 },
    proposal: { budgets: {}, rationale: "sized" },
    context: { ingress: "pi_ui", interactive: true, delegation: false, author: null, ...context },
    ...overrides } };
}

const APPROVAL_REQUIRED = { code: "governance.approval_required", message: "approval required" };

for (const controlMode of ["deliberative", "auto"]) {
  test(`gate: the sole initial-approval blocker pauses report_convergence without converging (${controlMode})`,
    async () => {
      const blocked = { type: "Block" as const, run: waitingRun({}, {}, controlMode), reasons: [APPROVAL_REQUIRED] };
      const w = await wire((req) => req.command.type === "StartRun"
        ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(blocked as never));
      await startRun(w);
      const event = toolEvent(REPORT_CONVERGENCE_TOOL);
      const privateBefore = w.privateRequests.length;
      const ctx = fakeCtx();
      let customCalls = 0;
      ctx.ui.custom = async () => { customCalls += 1; return undefined as never; };
      assert.equal(await w.pi.toolCall()(event, { ui: new FakeUi() }), undefined);
      const execute = (toolCallId: string) => w.pi.tools.get(REPORT_CONVERGENCE_TOOL)!.execute(
        toolCallId, event.input, new AbortController().signal, () => {}, ctx);
      const output = await execute(event.toolCallId);
      const text = (output.content[0] as { text: string }).text;
      assert.ok(text.startsWith(HUMAN_WAIT_NOTICE), text);
      assert.equal((output.details as { type: string }).type, "Block");
      // The wait is settlement only: no decision ingress, no dialog, and the run handle stays active.
      assert.equal(w.privateRequests.length, privateBefore, "no governance decision or audit ingress call");
      assert.equal(customCalls, 0, "no dialog opened");
      const evaluations = w.requests.length;
      const again = await execute("tc-2");
      assert.ok(((again.content[0] as { text: string }).text).startsWith(HUMAN_WAIT_NOTICE));
      const last = w.requests.at(-1)!;
      assert.equal(w.requests.length, evaluations + 1, "a follow-up report_convergence dispatches");
      assert.equal(last.command.type, "EvaluateRun");
      assert.equal((last.command as { run_id: string }).run_id, HANDLE);
    });
}

test("gate: only the legitimate initial-approval blocker is a human wait", async () => {
  const cases: Array<[string, Record<string, unknown>]> = [
    ["mixed reasons", { reasons: [APPROVAL_REQUIRED, { code: "run.corrupt", message: "corrupt" }] }],
    ["proposal exhausted", { run: waitingRun({ interactions_remaining: { proposal: 0, total: 128 } }) }],
    ["total exhausted", { run: waitingRun({ interactions_remaining: { proposal: 3, total: 0 } }) }],
    ["prompt error", { run: waitingRun({ prompt_error: "governance.interaction_limit" }) }],
    ["ui unavailable", { run: waitingRun({}, { ingress: "unavailable" }) }],
    ["delegated auto", { run: waitingRun({}, { interactive: false, delegation: true }, "auto") }],
    ["post-approval auto", { run: waitingRun({ first_approval: true, state: "revision_pending" }, {}, "auto"),
      reasons: [{ code: "governance.revision_required", message: "revision required" }] }],
    ["malformed reason", { reasons: [{ code: "governance.revision_required", message: "mismatch" }] }],
  ];
  for (const [name, changed] of cases) {
    const blocked = { type: "Block" as const, run: waitingRun(), reasons: [APPROVAL_REQUIRED], ...changed };
    assert.equal(humanApprovalWait(blocked as never), false, name);
    const w = await wire((req) => req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(blocked as never));
    await startRun(w);
    const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
    assert.equal((decision as { block?: boolean } | undefined)?.block, true, name);
  }
});

function exhausted(resource: string) {
  return { code: "budget.exhausted", parameters: { resource }, message: "budget exhausted" };
}

for (const resource of ["audit_spawn", "pass"]) {
  test(`gate: the sole budget.exhausted blocker settles report_convergence nonterminally (${resource})`,
    async () => {
      const blocked = { type: "Block" as const, run: run(), reasons: [exhausted(resource)] };
      const w = await wire((req) => req.command.type === "StartRun"
        ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(blocked as never));
      await startRun(w);
      const event = toolEvent(REPORT_CONVERGENCE_TOOL);
      const privateBefore = w.privateRequests.length;
      const ctx = fakeCtx();
      let customCalls = 0;
      ctx.ui.custom = async () => { customCalls += 1; return undefined as never; };
      assert.equal(await w.pi.toolCall()(event, { ui: new FakeUi() }), undefined);
      const output = await w.pi.tools.get(REPORT_CONVERGENCE_TOOL)!.execute(
        event.toolCallId, event.input, new AbortController().signal, () => {}, ctx);
      const text = (output.content[0] as { text: string }).text;
      assert.ok(text.startsWith(BUDGET_EXHAUSTED_NOTICE.replace("{resource}", resource)), text);
      assert.ok(text.includes(resource));
      // The original Block is preserved; the pause is settlement only.
      assert.equal((output.details as { type: string }).type, "Block");
      assert.equal(w.privateRequests.length, privateBefore, "no governance decision or audit ingress call");
      assert.equal(customCalls, 0, "no dialog opened");
      // The run stays active: a follow-up report dispatches against the same handle.
      const evaluations = w.requests.length;
      await w.pi.tools.get(REPORT_CONVERGENCE_TOOL)!.execute(
        "tc-2", event.input, new AbortController().signal, () => {}, ctx);
      const last = w.requests.at(-1)!;
      assert.equal(w.requests.length, evaluations + 1);
      assert.equal((last.command as { run_id: string }).run_id, HANDLE);
    });
}

test("gate: only a sole budget.exhausted blocker on an active run is settled", async () => {
  const cases: Array<[string, Record<string, unknown>]> = [
    ["mixed reasons", { reasons: [exhausted("pass"), { code: "run.corrupt", message: "corrupt" }] }],
    ["terminal run", { run: run("stopped_budget") }],
    ["other reason", { reasons: [{ code: "audit.failed", parameters: {}, message: "failed" }] }],
  ];
  for (const [name, changed] of cases) {
    const blocked = { type: "Block" as const, run: run(), reasons: [exhausted("pass")], ...changed };
    assert.equal(budgetExhaustedWait(blocked as never), false, name);
    const w = await wire((req) => req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(blocked as never));
    await startRun(w);
    const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
    assert.equal((decision as { block?: boolean } | undefined)?.block, true, name);
  }
});

test("gate: report_convergence tool is permitted on Allow", async () => {
  const w = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Allow", converged: true, run: run("converged") }),
  );
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision, undefined); // permit
});

test("gate: honest stop intent reaches pre-tool evaluation and execute exactly once", async () => {
  const w = await wire((req) => req.command.type === "StartRun"
    ? envelope({ type: "Allow", converged: false, run: run() })
    : envelope({ type: "Allow", converged: false, run: run("stopped_residual") }));
  await startRun(w);
  const event = { ...toolEvent(REPORT_CONVERGENCE_TOOL), input: { intent: "stop" } };
  assert.equal(await w.pi.toolCall()(event, { ui: new FakeUi() }), undefined);
  await w.pi.tools.get(REPORT_CONVERGENCE_TOOL)!.execute(
    event.toolCallId, event.input, new AbortController().signal, () => {}, fakeCtx());
  const evaluations = w.requests.filter((request) => request.command.type === "EvaluateRun");
  assert.equal(evaluations.length, 1);
  assert.equal(evaluations[0].command.type === "EvaluateRun"
    ? evaluations[0].command.intent : null, "stop");
});

test("gate: an investigative tool records investigation before execution", async () => {
  const w = await wire((req) => req.command.type === "ObserveAction"
    ? envelope({ type: "Allow", converged: false, run: run() })
    : envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const before = w.requests.length;
  const decision = await w.pi.toolCall()(toolEvent("bash"), { ui: new FakeUi() });
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, before + 1);
  const request = w.requests.at(-1)!;
  assert.equal(request.command.type, "ObserveAction");
  assert.equal(request.command.type === "ObserveAction" ? request.command.action.kind : null,
    "investigate");
});

test("gate: an investigative tool is blocked when route ordering is denied", async () => {
  const w = await wire((req) => req.command.type === "ObserveAction"
    ? envelope({ type: "Block", run: run(), reasons: [{ code: "route.required",
        message: "route first" }] })
    : envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent("read"), { ui: new FakeUi() });
  assert.deepEqual(decision, { block: true, reason: "empirica investigation denied: route first" });
});

test("gate: with no active run the gated tool passes (nothing to gate)", async () => {
  const w = await wire(() => {
    throw new Error("should not dispatch without a run");
  });
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, 0);
});

test("gate: an unavailable transport fails CLOSED (blocks the report)", async () => {
  const w = await wire(() => {
    throw new Error("core unreachable");
  });
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /failing closed/);
});

test("gate: closed and open Fault both block the report", async () => {
  const wClosed = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Fault", code: "corrupt_run", fail_direction: "closed" }),
  );
  await startRun(wClosed);
  const closed = await wClosed.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), {
    ui: new FakeUi(),
  });
  assert.equal(closed?.block, true);

  const wOpen = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Fault", code: "unavailable", fail_direction: "open" }),
  );
  await startRun(wOpen);
  const open = await wOpen.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(open?.block, true);
});

// --- malformed response cannot permit the hard gate (D6-C C3) ----------------

test("gate: malformed Allow (converged not boolean) fails closed", async () => {
  const w = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Allow", converged: "yes" as never, run: run() } as never),
  );
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /failing closed/);
});

// The exhaustive guard mutation matrix (guard.test.ts) proves every malformed
// variant rejects at the guard. One end-to-end malformed gate test here proves
// the guard-to-gate closure path: a malformed response the guard rejects
// reaches the hard gate and fails closed.

test("gate: malformed Block reason (null entry) fails closed", async () => {
  const w = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Block", run: run(), reasons: [null] as never } as never),
  );
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /failing closed/);
});

test("gate: a well-formed Inert is denied (run gone but handle exists)", async () => {
  const w = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Inert", reason: "no_run" }),
  );
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /no active run to report/);
});

// --- bound foreground auditor lifecycle -------------------------------------

test("canonical audit guidance keeps the exact two-field example in procedural references", () => {
  const example = '{"agent":"empirica.empirica-auditor","task":"Audit the host-provided dossier."}';
  for (const file of ["empirica/references/audit.md",
    "../../../.claude/skills/native-qualification/SKILL.md"]) {
    assert.ok(readFileSync(resolve(DEFAULT_SKILLS_DIR, file), "utf8").includes(example), file);
  }
});

test("subagent: canonical input errors are specific and have no audit side effects", async (t) => {
  const missingTask = "empirica auditor launch requires the canonical agent and a string task; the host replaces task with its dossier";
  const overrides = "empirica auditor launch accepts only agent and task; omit async, model, context, tools, and other overrides";
  const cases: { label: string; fields: Record<string, unknown>; reason: string }[] = [
    { label: "native bare call", fields: {}, reason: missingTask },
    { label: "native foreground flag without task", fields: { async: false }, reason: missingTask },
    ...[null, 0, false, [], {}].map((task) => ({ label: `task ${JSON.stringify(task)}`,
      fields: { task }, reason: missingTask })),
    ...["async", "model", "context", "tools", "acceptance", "toolBudget", "unknown\nkey"].map((key) => ({
      label: `forbidden ${JSON.stringify(key)}`, fields: { task: "audit", [key]: false }, reason: overrides })),
  ];
  for (const row of cases) await t.test(row.label, async () => {
    const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
    await startRun(w);
    const input = { agent: "empirica.empirica-auditor", ...row.fields };
    const before = structuredClone(input);
    const requests = w.requests.length, entries = w.pi.entries.length;
    const decision = await w.pi.toolCall()(
      { toolName: SUBAGENT_TOOL, toolCallId: "bad-audit", input }, fakeCtx());
    assert.deepEqual(decision, { block: true, reason: row.reason });
    assert.deepEqual(input, before);
    // Shared governance/investigation admission still precedes input validation.
    assert.deepEqual(w.requests.slice(requests).map((request) => [request.command.type,
      request.command.type === "ObserveAction" ? request.command.action.kind : null]),
    [["ObserveAction", "investigate"]]);
    assert.equal(w.pi.entries.length, entries);
    assert.equal(w.auditResolutions.length, 0);
    assert.equal(w.privateRequests.length, 0);
  });
});

test("subagent: empty string task retains its existing accepted meaning", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const input: Record<string, unknown> = { agent: "empirica.empirica-auditor", task: "" };
  assert.equal(await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "empty-task", input }, fakeCtx()), undefined);
  assert.equal(input.async, false);
  assert.match(String(input.task), /AUDIT DOSSIER/);
  assert.equal(w.auditResolutions.length, 1);
  assert.deepEqual(w.privateRequests.map((request) => request.operation),
    ["classify_identity", "classify_identity", "audit_prepare"]);
});

test("subagent: canonical auditor is reserved, bound, attributed, and prompt-injected", async () => {
  const child = { child_id: "ch-1", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire((req) => {
    if (req.command.type === "ObserveAction")
      return envelope({ type: "Allow", converged: false,
        run: { ...run(), children: [child] } as never });
    if (req.command.type === "GetArgument")
      return envelope({ type: "Allow", converged: false, run: run(),
        argument: { artifacts: [{ kind: "research", artifact_id: `sha256:${"1".repeat(64)}` }] }
      } as never);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  const input: Record<string, unknown> = {
    agent: "empirica.empirica-auditor", task: "AUTHOR_TASK_IS_NOT_AUTHORITY",
  };
  const decision = await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-sub", input }, fakeCtx(),
  );
  assert.equal(decision, undefined);
  assert.equal(input.async, false);
  assert.deepEqual(input.acceptance, { level: "none",
    reason: "Empirica's bound canonical auditor is read-only and has its own verdict contract." });
  assert.equal(input.timeoutMs, 900_000);
  assert.deepEqual(input.turnBudget, { maxTurns: 8, graceTurns: 1 });
  assert.deepEqual(input.toolBudget, { soft: 20, hard: 30, block: ["write", "edit"] });
  assert.match(String(input.task), /AUDIT DOSSIER/);
  assert.doesNotMatch(String(input.task), /AUTHOR_TASK_IS_NOT_AUTHORITY/);
  assert.equal(input.model, "bedrock/auditor-model");
  assert.equal(input.agentScope, "project");
  assert.deepEqual(w.privateRequests.map((item) => item.operation),
    ["classify_identity", "classify_identity", "audit_prepare"]);
  assert.equal(w.pi.entries.at(-1)?.customType, "empirica.audit");
});

test("canonical auditor blocks equal normalized identity classes", async () => {
  const child = { child_id: "ch-1", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire(() => envelope({ type: "Allow", converged: false,
    run: { ...run(), children: [child] } }), true, () => "same-class");
  await startRun(w);
  const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "same-class",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(decision?.block, true);
  assert.match(decision?.reason ?? "", /agentOverrides/);
  assert.deepEqual(w.privateRequests.map((item) => item.operation),
    ["classify_identity", "classify_identity"]);
});

test("canonical auditor clearly blocks a null reviewer identity classification", async () => {
  const child = { child_id: "ch-1", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire(() => envelope({ type: "Allow", converged: false,
    run: { ...run(), children: [child] } }), true,
    (_provider, model) => String(model).includes("auditor") ? null : "author-class");
  await startRun(w);
  const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "null-class",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(decision?.block, true);
  assert.match(decision?.reason ?? "", /reviewer identity unobservable/);
  assert.deepEqual(w.privateRequests.map((item) => item.operation),
    ["classify_identity", "classify_identity"]);
});

test("canonical auditor rejects a reservation when a later launch step throws", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  w.pi.appendEntry = () => { throw new Error("late host failure"); };
  const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "late-failure",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(decision?.block, true);
  assert.match(decision?.reason ?? "", /late host failure/);
  assert.deepEqual(w.privateRequests.map((item) => item.operation),
    ["classify_identity", "classify_identity", "audit_prepare", "audit_reject"]);
  // The rejection must release the exact reserved plan, not some other child.
  assert.equal((w.privateRequests.at(-1) as { plan?: { child_id?: string } }).plan?.child_id, "ch-1");
  w.pi.appendEntry = (customType, data) => { w.pi.entries.push({ customType, data }); };
  const corrected = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "corrected",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(corrected, undefined);
  assert.deepEqual(w.privateRequests.map((item) => item.operation).slice(-3),
    ["classify_identity", "classify_identity", "audit_prepare"]);
});

test("tool_result redacts before privately admitting the correlated verdict", async () => {
  const child = { child_id: "ch-1", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire((req) => {
    if (req.command.type === "ObserveAction")
      return envelope({ type: "Allow", converged: false,
        run: { ...run(), children: [child] } as never });
    if (req.command.type === "GetArgument")
      return envelope({ type: "Allow", converged: false, run: run(),
        argument: { artifacts: [{ kind: "research", artifact_id: `sha256:${"1".repeat(64)}` }] }
      } as never);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-result",
      input: { agent: "empirica.empirica-auditor", task: "audit" } },
    fakeCtx(),
  );
  const nativeSession = join(mkdtempSync(join(tmpdir(), "empirica-pi-audit-")), "session.jsonl");
  writeFileSync(nativeSession, JSON.stringify({
    type: "message",
    message: {
      role: "assistant",
      content: [{ type: "text", text: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```" }],
      provider: "amazon-bedrock-us",
      model: "us.anthropic.claude-opus-4-8",
    },
  }) + "\n");
  const event = {
    toolCallId: "tc-result", toolName: SUBAGENT_TOOL,
    content: [{ type: "text", text: JSON.stringify({
      output: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```",
      finalOutput: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```",
    }) }],
    details: { results: [{ model: "configured/wrong-model", sessionFile: nativeSession,
      output: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```",
      finalOutput: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```" }] },
  };
  const handler = w.pi.handlers.get("tool_result") as
    (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>;
  const replacement = await handler(event, fakeCtx()) as { content?: unknown; details?: unknown };

  assert.doesNotMatch(JSON.stringify(replacement.content), /```empirica-verdict/);
  assert.doesNotMatch(JSON.stringify(replacement.details), /```empirica-verdict/);
  assert.match(JSON.stringify(replacement.content), /recorded by host/);
  assert.equal(w.privateRequests.at(-1)?.operation, "audit_verdict");
  const identity = w.privateRequests.find((request) => request.operation === "audit_identity");
  assert.equal(identity?.auditor?.provider_id, "amazon-bedrock-us");
  assert.equal(identity?.auditor?.model_id, "us.anthropic.claude-opus-4-8");
  assert.equal(identity?.auditor?.source, "pi-child-session");
  assert.equal(w.pi.entries.at(-1)?.customType, "empirica.audit.done");
});

test("missing native session keeps auditor identity unverified", async () => {
  const child = { child_id: "ch-unverified", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire((req) => {
    if (req.command.type === "ObserveAction")
      return envelope({ type: "Allow", converged: false,
        run: { ...run(), children: [child] } as never });
    if (req.command.type === "GetArgument")
      return envelope({ type: "Allow", converged: false, run: run(),
        argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } } as never);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-unverified",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  const event: ToolResultEvent = {
    toolCallId: "tc-unverified",
    content: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```",
    details: { results: [{
      model: "bedrock/configured-model", sessionFile: "/missing/child-session.jsonl",
      finalOutput: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```",
    }] },
  };
  await (w.pi.handlers.get("tool_result") as
    (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>)(
      event, fakeCtx());
  const identity = w.privateRequests.find((request) => request.operation === "audit_identity");
  assert.equal(identity?.auditor?.provider_id, null);
  assert.equal(identity?.auditor?.model_id, null);
  assert.equal(identity?.auditor?.source, "pi-child-session-unverified");
});

test("session restore orphans unresolved audits and tombstones completed correlations", async () => {
  const plan = { child_id: "ch-1", role_profile: "empirica.empirica-auditor",
    operation_id: `sha256:${"b".repeat(64)}`,
          auditor: { provider_id: "bedrock", model_id: "auditor-model" },
    argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } };
  const correlation = { toolCallId: "tc-restored", runHandle: HANDLE, nativeId: "tc-restored",
    plan,
    author: { provider_id: "bedrock", model_id: "author-model", observed_by: "host", source: "pi" },
    auditor: { provider_id: "bedrock", model_id: "auditor-model",
      observed_by: "configuration", source: "preflight" } };
  const unresolved = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await (unresolved.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)(
    {}, fakeCtx("/work", [
      { customType: "empirica.run", data: { runHandle: HANDLE } },
      { customType: "empirica.audit", data: correlation },
    ]));
  const unresolvedEvent: ToolResultEvent = { toolCallId: "tc-restored",
    content: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```" };
  const unresolvedReplacement = await (unresolved.pi.handlers.get("tool_result") as
    (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>)(
      unresolvedEvent, fakeCtx()) as { content?: unknown };
  assert.deepEqual(unresolved.privateRequests.map((item) => [item.operation, item.state]),
    [["audit_failure", "orphaned"]]);
  assert.doesNotMatch(JSON.stringify(unresolvedReplacement.content), /```empirica-verdict/);

  const completed = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await (completed.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)(
    {}, fakeCtx("/work", [
      { customType: "empirica.run", data: { runHandle: HANDLE } },
      { customType: "empirica.audit", data: correlation },
      { customType: "empirica.audit.done", data: { toolCallId: "tc-restored" } },
    ]));
  const replay: ToolResultEvent = { toolCallId: "tc-restored",
    content: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```" };
  const replayReplacement = await (completed.pi.handlers.get("tool_result") as
    (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>)(
      replay, fakeCtx()) as { content?: unknown };
  assert.equal(completed.privateRequests.length, 0);
  assert.doesNotMatch(JSON.stringify(replayReplacement.content), /```empirica-verdict/);
});

test("session shutdown orphans a newly admitted unresolved audit", async () => {
  const child = { child_id: "ch-orphan", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire((req) => {
    if (req.command.type === "ObserveAction")
      return envelope({ type: "Allow", converged: false,
        run: { ...run(), children: [child] } as never });
    if (req.command.type === "GetArgument")
      return envelope({ type: "Allow", converged: false, run: run(),
        argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } } as never);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-orphan",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  await (w.pi.handlers.get("session_shutdown") as () => Promise<unknown>)();
  assert.equal(w.privateRequests.at(-1)?.operation, "audit_failure");
  assert.equal(w.privateRequests.at(-1)?.state, "orphaned");
  assert.equal(w.pi.entries.at(-1)?.customType, "empirica.audit.done");
});

test("malformed auditor result returns a propagated redacted replacement", async () => {
  const child = { child_id: "ch-malformed", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire((req) => {
    if (req.command.type === "ObserveAction")
      return envelope({ type: "Allow", converged: false,
        run: { ...run(), children: [child] } as never });
    if (req.command.type === "GetArgument")
      return envelope({ type: "Allow", converged: false, run: run(),
        argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } } as never);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-malformed",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  const event: ToolResultEvent = {
    toolCallId: "tc-malformed",
    content: [{ type: "text", text: "not a verdict" }],
    details: { output: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```" },
  };
  const replacement = await (w.pi.handlers.get("tool_result") as
    (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>)(
      event, fakeCtx()) as { content?: unknown; details?: unknown };
  assert.doesNotMatch(JSON.stringify(replacement.details), /```empirica-verdict/);
  assert.equal(w.privateRequests.at(-1)?.operation, "audit_failure");
  assert.equal(w.privateRequests.at(-1)?.state, "failed");
});

test("errored auditor output is redacted and cannot admit a fenced verdict", async () => {
  const child = { child_id: "ch-error", purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire((req) => {
    if (req.command.type === "ObserveAction")
      return envelope({ type: "Allow", converged: false,
        run: { ...run(), children: [child] } as never });
    if (req.command.type === "GetArgument")
      return envelope({ type: "Allow", converged: false, run: run(),
        argument: { artifacts: [{ kind: "research", artifact_id: `sha256:${"1".repeat(64)}` }] }
      } as never);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-error",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  const event: ToolResultEvent = { toolCallId: "tc-error", isError: true,
    content: "```empirica-verdict\n{\"verdict\":\"pass\"}\n```" };
  const handler = w.pi.handlers.get("tool_result") as
    (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>;
  await handler(event, fakeCtx());
  assert.doesNotMatch(String(event.content), /```empirica-verdict/);
  assert.equal(w.privateRequests.at(-1)?.operation, "audit_failure");
  assert.equal(w.privateRequests.at(-1)?.state, "failed");
  assert.equal(w.privateRequests.filter((item) => item.operation === "audit_verdict").length, 0);
});

test("non-canonical auditors stay ordinary budgeted children; model overrides get no trusted admission", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const before = w.privateRequests.length;
  const evil = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "evil",
    input: { agent: "evil-empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(evil?.block, true);
  const ordinaryReserve = w.requests.find((request) => request.command.type === "ObserveAction"
    && request.command.action.kind === "child_reserve");
  assert.equal(ordinaryReserve?.command.type === "ObserveAction"
    ? ordinaryReserve.command.action.resource_class : null, "investigation");
  const overridden = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "override",
    input: { agent: "empirica.empirica-auditor", task: "audit", model: "author-model" } }, fakeCtx());
  assert.equal(overridden?.block, true);
  assert.equal(w.privateRequests.length, before);
});

test("canonical auditor identity follows filesystem symlinks", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "empirica-agent-path-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const realPackage = join(root, "real-package");
  const realAgent = join(realPackage, "agents", "pi", "empirica-auditor.md");
  mkdirSync(join(realPackage, "skills"), { recursive: true });
  mkdirSync(join(realPackage, "agents", "pi"), { recursive: true });
  writeFileSync(realAgent, "auditor");
  const aliasPackage = join(root, "alias-package");
  symlinkSync(realPackage, aliasPackage, "dir");

  const pi = new FakePi();
  createEmpiricaExtension({
    ownerEnv: {},
    dispatch: (request) => ({
      ...envelope({ type: "Allow", converged: false, run: run() }),
      request_id: request.request_id,
    }),
    deriveSelector: () => ({ project: "p", session: "s" }),
    privateIngress: async (request) => request.operation === "governance_context"
      ? { protocol: PROTOCOL, request_id: "trusted-governance", result: { type: "Inert", reason: "unsupported_host_event", run: run() } }
      : request.operation === "audit_prepare"
      ? { type: "audit_plan", plan: { child_id: "ch-1",
          role_profile: "empirica.empirica-auditor",
          operation_id: `sha256:${"b".repeat(64)}`,
          argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } } }
      : request.operation === "classify_identity"
      ? { identity: String((request.payload as { model_id?: unknown })?.model_id) }
      : request.operation === "audit_start" ? { type: "audit_started" }
      : request.operation === "audit_identity" ? { type: "audit_identity" }
      : ["audit_reject", "audit_failure"].includes(request.operation) ? { type: "audit_terminal" }
      : request.operation === "audit_verdict" ? { type: "audit_verdict", admitted: true }
      : { protocol: PROTOCOL, request_id: "trusted", result: {
          type: "Inert", reason: "unsupported_host_event", run: run() } },
    resolveAuditContract: async () => ({ agentFilePath: realAgent,
                                         model: "bedrock/auditor-model", agentScope: "project" }),
    skillsDir: join(aliasPackage, "skills"),
  })(pi);
  await (pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)(
    {}, fakeCtx("/work", [{ customType: "empirica.run", data: { runHandle: HANDLE } }]));
  const event = { toolName: SUBAGENT_TOOL, toolCallId: "symlinked",
    input: { agent: "empirica.empirica-auditor", task: "audit" } };
  const decision = await pi.toolCall()(event, fakeCtx());
  assert.equal(decision, undefined);
  assert.equal((event.input as Record<string, unknown>).model, "bedrock/auditor-model");
});

test("unresolvable auditor package is blocked before reservation", async () => {
  const requests: Request[] = [];
  const pi = new FakePi();
  createEmpiricaExtension({
    ownerEnv: {},
    dispatch: (request) => { requests.push(request); return {
      ...envelope({ type: "Allow", converged: false, run: run() }),
      request_id: request.request_id,
    }; },
    deriveSelector: () => ({ project: "p", session: "s" }),
    privateIngress: async () => ({ protocol: PROTOCOL, request_id: "trusted-governance",
      result: { type: "Inert", reason: "unsupported_host_event", run: run() } }),
    resolveAuditContract: async () => { throw new Error("auditor package unresolvable"); },
  })(pi);
  await (pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)(
    {}, fakeCtx("/work", [{ customType: "empirica.run", data: { runHandle: HANDLE } }]));
  const decision = await pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "shadow",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /auditor package unresolvable/);
  assert.equal(requests.length, 2); // investigation + read-only approved-selection lookup
  assert.equal(requests[0].command.type, "ObserveAction");
  assert.equal(requests[0].command.type === "ObserveAction"
    ? requests[0].command.action.kind : null, "investigate");
});

test("subagent: management list with a real handle is inert (no denial)", async () => {
  const w = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Allow", converged: true, run: run("converged") }),
  );
  await startRun(w);
  const before = w.requests.length;
  const decision = await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-list", input: { action: "list" } },
    { ui: new FakeUi() },
  );
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, before); // no dispatch for management
});

test("subagent: executable launch with no handle is inert (nothing to deny)", async () => {
  const w = await wire(() => {
    throw new Error("should not dispatch without a handle");
  });
  const decision = await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-nohandle", input: { agent: "x" } },
    { ui: new FakeUi() },
  );
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, 0);
});

test("subagent: malformed multi-key launch with a real handle is inert", async () => {
  const w = await wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Allow", converged: true, run: run("converged") }),
  );
  await startRun(w);
  const before = w.requests.length;
  const decision = await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-multi", input: { agent: "x", workflowScript: "y" } },
    { ui: new FakeUi() },
  );
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, before);
});

// --- direct registered tool execute (Block/Inert/openFault) + status + compaction
// The registered report_convergence tool's execute() throws on a guarded deny;
// the tool_call gate (above) returns {block}. Both route through gateFromDecision.

async function execTool(w: Wired, name: string, params: unknown = {}) {
  return w.pi.tools.get(name)!.execute(
    "x", params, new AbortController().signal, () => {}, fakeCtx());
}

/** StartRun -> Allow; every later command returns the scripted deny result. */
const deny = (r: Result): ((req: Request) => Response) => (req) =>
  req.command.type === "StartRun" ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(r);

test("direct tool: report_convergence execute rejects on Block with active handle", async () => {
  const w = await wire(deny({ type: "Block", run: run(), reasons: [{ code: "audit.required", message: "independent audit required" }] }));
  await startRun(w);
  await assert.rejects(() => execTool(w, "report_convergence"), /independent audit required/);
});

test("direct tool: report_convergence execute rejects on Inert(no_run) with active handle", async () => {
  const w = await wire(deny({ type: "Inert", reason: "no_run" }));
  await startRun(w);
  await assert.rejects(() => execTool(w, "report_convergence"), /no active run to report/);
});

test("direct tool: report_convergence execute rejects on open Fault with active handle", async () => {
  const w = await wire(deny({ type: "Fault", code: "unavailable", fail_direction: "open" }));
  await startRun(w);
  await assert.rejects(() => execTool(w, "report_convergence"), /unavailable/);
});

test("direct tool: report_convergence forwards an honest stop intent", async () => {
  const w = await wire((req) => req.command.type === "StartRun"
    ? envelope({ type: "Allow", converged: false, run: run() })
    : envelope({ type: "Allow", converged: false, run: run("stopped_residual") }));
  await startRun(w);
  await execTool(w, "report_convergence", { intent: "stop" });
  const request = w.requests.at(-1)!;
  assert.equal(request.command.type === "EvaluateRun" ? request.command.intent : null, "stop");

  const before = w.requests.length;
  const decision = await w.pi.toolCall()(toolEvent("read"), { ui: new FakeUi() });
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, before); // verified terminal run is no longer gated
  assert.equal(w.pi.entries.at(-1)?.customType, "empirica.run.done");
});

test("direct convergence also retires the terminal run handle", async () => {
  const w = await wire((req) => req.command.type === "StartRun"
    ? envelope({ type: "Allow", converged: false, run: run() })
    : envelope({ type: "Allow", converged: true, run: run("converged") }));
  await startRun(w);
  await execTool(w, "report_convergence");
  const before = w.requests.length;
  assert.equal(await w.pi.toolCall()(toolEvent("read"), { ui: new FakeUi() }), undefined);
  assert.equal(w.requests.length, before);
  assert.equal(w.pi.entries.at(-1)?.customType, "empirica.run.done");
});

test("session restore keeps a verified terminal run inactive", async () => {
  const w = await wire(() => { throw new Error("terminal handle must not dispatch"); });
  const ctx = fakeCtx("/work", [
    { customType: "empirica.run", data: { runHandle: HANDLE } },
    { customType: "empirica.run.done", data: { runHandle: HANDLE } },
  ]);
  await (w.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, ctx);
  assert.equal(await w.pi.toolCall()(toolEvent("read"), { ui: new FakeUi() }), undefined);
  assert.equal(w.requests.length, 0);
});

test("a retired run stays readable but not writable", async () => {
  const w = await wire((req) => req.command.type === "StartRun"
    ? envelope({ type: "Allow", converged: false, run: run() })
    : req.command.type === "ResolveRun" ? envelope({ type: "Inert", reason: "no_run" })
    : envelope({ type: "Allow", converged: true, run: run("converged") }));
  await startRun(w);
  await execTool(w, "report_convergence");
  const result = await execTool(w, "empirica_read", { operation: "GetRun" });
  const read = w.requests.at(-1)!;
  assert.equal(read.command.type, "GetRun");
  assert.equal(read.command.type === "GetRun" ? read.command.run_id : null, HANDLE);
  assert.match(result.content[0].text, /converged/);
  const before = w.requests.length;
  const observed = await execTool(w, "empirica_observe", { action: { kind: "investigate" } });
  assert.match(observed.content[0].text, /No active Empirica run/);
  assert.equal(w.requests.length, before);
});

test("session restore keeps a retired run readable", async () => {
  const w = await wire((req) => {
    // No active run resolves for the selector, so the read falls back to the retired handle.
    if (req.command.type === "ResolveRun") return envelope({ type: "Inert", reason: "no_run" });
    assert.equal(req.command.type, "GetRun");
    assert.equal(req.command.type === "GetRun" ? req.command.run_id : null, HANDLE);
    return envelope({ type: "Allow", converged: true, run: run("converged") });
  });
  const ctx = fakeCtx("/work", [
    { customType: "empirica.run", data: { runHandle: HANDLE } },
    { customType: "empirica.run.done", data: { runHandle: HANDLE } },
  ]);
  await (w.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, ctx);
  const result = await execTool(w, "empirica_read", { operation: "GetRun" });
  assert.match(result.content[0].text, /converged/);
});

test("empirica_read uses the restored opaque handle", async () => {
  const w = await wire((req) => {
    assert.equal(req.command.type, "GetRun");
    assert.equal(req.command.type === "GetRun" ? req.command.run_id : null, HANDLE);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  const result = await execTool(w, "empirica_read", { operation: "GetRun" });
  assert.match(result.content[0].text, /run-handle-1/);
  assert.equal(w.requests.length, 1);
});

test("empirica_read without a restored handle resolves the current selector", async () => {
  const w = await wire((req) => {
    assert.equal(req.command.type, "ResolveRun");
    assert.deepEqual(
      req.command.type === "ResolveRun" ? req.command.selector : null,
      { project: "p", session: "s" },
    );
    return envelope({ type: "Inert", reason: "no_run" });
  });

  const result = await execTool(w, "empirica_read", { operation: "GetRun" });

  assert.match(result.content[0].text, /No active Empirica run/);
  assert.equal(w.requests.length, 1);
});

test("session_start reconstructs the handle; compaction carries the handle text", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const ctx = fakeCtx("/work", [{ customType: "empirica.run", data: { runHandle: "restored" } }]);
  await (w.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, ctx);
  const out = await (w.pi.handlers.get("session_before_compact") as (e: unknown, c: unknown) => unknown)({ preparation: { firstKeptEntryId: "e", tokensBefore: 4 } }, ctx);
  assert.match((out as { compaction: { summary: string } }).compaction.summary, /restored/);
  assert.equal(w.pi.entries.length, 0);
});
