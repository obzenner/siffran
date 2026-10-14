// End-to-end through the registered handlers: the /empirica command and the
// tool_call convergence gate. Dispatch is a fake that records requests and
// returns scripted decisions — no core, no bridge, no Pi runtime. Proves the
// central guard rejects malformed responses so they cannot permit the hard gate.

import { test } from "node:test";
import assert from "node:assert/strict";

import { PROTOCOL, type InvocationProvenance, type Request, type Response, type Result } from "../src/contract.ts";
import { AUDIT_LAUNCH_POLICY, HOST_COMPATIBILITY, SUBAGENTS_COMPATIBILITY, spellRange } from "../src/host-profile.ts";
import { auditLaunchInput } from "../src/audit-launch.ts";
import { REPORT_CONVERGENCE_TOOL, SUBAGENT_TOOL } from "../src/translate.ts";
import { EVIDENCE_WAIT_NOTICE } from "../src/evidence-ui.ts";
import {
  BUDGET_EXHAUSTED_NOTICE, budgetExhaustedWait, HUMAN_WAIT_NOTICE, humanApprovalWait,
} from "../src/governance-ui.ts";
import {
  createEmpiricaExtension, DEFAULT_SKILLS_DIR, defaultAuditContractResolver, enabledAuditorCandidates, piAuditorModelSource,
  resolvePiAuditorModel,
} from "../src/index.ts";
import { AUDIT_LAUNCH_PARAMETERS, loadInventory, type SubagentInventory } from "../src/subagent-inventory.ts";
import { bound, FIXTURES, fixtureFor } from "./preflight-fixtures.ts";
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { isDeepStrictEqual } from "node:util";
import * as path from "node:path";
import { join, resolve } from "node:path";
import { FakePi, FakeUi, fakeCtx } from "./fakes.ts";
import type { ToolCallEvent, ToolInfo, ToolResultEvent } from "../src/pi-types.ts";
import type { OwnerEnv } from "../src/runtime-owner.ts";
import { PUBLIC_TOOLS } from "../src/public-tools.ts";
import { defaultSubagentsPackage, makeSubagentsPackage, schemaDeclaring, tempParent } from "./owner-fixture.ts";
import type { PrivateIngressRequest } from "../src/private-transport.ts";

const AUTHOR_LABELS = PUBLIC_TOOLS.author_view.labels as Readonly<Record<string, string>>;

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

test("the enabled models are Pi's own list: project replaces user, patterns expand over what is available, order is kept", () => {
  const available = [{ provider: "a", id: "sol-1" }, { provider: "a", id: "sol-2" }, { provider: "b", id: "opus-1" }, { provider: "b", id: "opus-2" }];
  const user = { enabledModels: ["a/sol-1", "b/opus-*"] };
  assert.deepEqual(enabledAuditorCandidates(user, {}, available), ["a/sol-1", "b/opus-1", "b/opus-2"]);
  assert.deepEqual(enabledAuditorCandidates(user, { enabledModels: ["opus-2:high", "a/gone", "sol-2", "a/sol-?"] }, available),
    ["b/opus-2", "a/sol-2", "a/sol-1"], "project replaces user; a thinking level is dropped; a missing model is skipped; ? matches one character");
  assert.deepEqual(enabledAuditorCandidates({}, {}, available), [], "no list: no candidates, so no pick");
});

test("only a per-agent override is explicit; a default or the main model may be replaced", () => {
  const override = { subagents: { agentOverrides: { "empirica.empirica-auditor": { model: "x/y" } } } };
  assert.equal(piAuditorModelSource({}, override), "override");
  assert.equal(piAuditorModelSource(override, {}), "override");
  assert.equal(piAuditorModelSource({ subagents: { defaultModel: "x/y" } }, {}), "default");
  assert.equal(piAuditorModelSource({}, {}), "main");
});

// The production resolver runs the owner's preflight and admits its answer through the seam
// (preflight-seam.ts). These tests feed it answers captured from real packages, v2 and v3.

const EXPECTED_AUDITOR = resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md");

function resolverFor(version: string, options: {
  configured?: string; available?: Array<{ provider: string; id: string }>;
  respond?: (input: Record<string, unknown>) => unknown;
} = {}) {
  const scopes: unknown[] = [];
  const inventory = loadInventory(version);
  assert.ok(inventory, version);
  const canonical = () => bound(fixtureFor(version).cases.canonical_auditor);
  const ctx = fakeCtx("/work");
  ctx.model = { provider: "main", id: "model" };
  ctx.modelRegistry = { getAvailable: () => options.available ?? [{ provider: "audit", id: "reviewer" }] };
  const resolve_ = () => defaultAuditContractResolver(
    { agent: "empirica.empirica-auditor", task: "audit", expectedAgent: EXPECTED_AUDITOR }, ctx,
    {
      settings: {
        getAgentDir: () => "/agent",
        SettingsManager: { create: () => ({
          getGlobalSettings: () => ({ subagents: { defaultModel: options.configured ?? "audit/reviewer" } }),
          getProjectSettings: () => ({}),
        }) },
      },
      runtime: { version, inventory, preflight: { resolveSubagentLaunchContract: async (input) => {
        scopes.push(input.agentScope);
        return options.respond ? options.respond(input) : canonical();
      } } },
    },
  );
  return { resolve: resolve_, scopes, canonical };
}

test("EHB-AUDLAUNCH-02: the preflight is asked for the same context the real launch carries", async () => {
  const launched = auditLaunchInput({ task: "", model: "audit/reviewer", agentScope: "user", policy: AUDIT_LAUNCH_POLICY }).context;
  assert.equal(launched, "fresh");
  for (const { version } of FIXTURES) {
    const asked: unknown[] = [];
    const r = resolverFor(version, { respond: (input) => { asked.push(input.context); return bound(fixtureFor(version).cases.canonical_auditor); } });
    await r.resolve();
    assert.deepEqual(asked, [launched], version);
  }
});

test("the resolver that starts a run and launches the audit refuses an auditor given a writing tool, for every reviewed version", async () => {
  for (const { version } of FIXTURES) {
    for (const extra of ["edit", "write", "bash", "subagent"]) {
      const r = resolverFor(version, { respond: () => {
        const response = bound(fixtureFor(version).cases.canonical_auditor);
        response.contract.tools.effectiveAllowlist = [...response.contract.tools.effectiveAllowlist, extra];
        return response;
      } });
      // The refusal carries the contract's own reason message, not a free-text improvisation.
      await assert.rejects(r.resolve(), (error: Error) => error.message.includes("tools-not-read-only")
        && error.message.includes(PUBLIC_TOOLS.recovery["host.subagents_tools_not_read_only"].message), `${version} + ${extra}`);
    }
  }
});

test("production Pi auditor resolver tries project then user scope", async () => {
  for (const { version } of FIXTURES) {
    const r = resolverFor(version, { respond: (input) => {
      const response = bound(fixtureFor(version).cases.canonical_auditor);
      if (input.agentScope === "project") response.contract.agent.filePath = "/shadow/auditor.md";
      return response;
    } });
    const resolved = await r.resolve();
    assert.deepEqual(r.scopes, ["project", "user"], version);
    assert.equal(resolved.agentScope, "user", version);
    assert.equal(resolved.agentFilePath, EXPECTED_AUDITOR, version);
  }
});

test("production Pi auditor resolver accepts the configured model with the agent's thinking level", async () => {
  for (const { version } of FIXTURES)
    assert.equal((await resolverFor(version).resolve()).model, "audit/reviewer", version);
});

test("production Pi auditor resolver keeps a configured thinking level for the launch", async () => {
  for (const { version } of FIXTURES)
    assert.equal((await resolverFor(version, { configured: "audit/reviewer:max" }).resolve()).model,
      "audit/reviewer:max", version);
});

test("production Pi auditor resolver still refuses any substituted model carrying a thinking level", async () => {
  for (const { version } of FIXTURES) {
    for (const substitute of ["other/reviewer:high", "audit/other:high", "audit/reviewer:0"]) {
      const r = resolverFor(version, { respond: () => {
        const response = bound(fixtureFor(version).cases.canonical_auditor);
        response.contract.model = substitute;
        if (response.contract.modelCandidates) response.contract.modelCandidates = [substitute];
        return response;
      } });
      await assert.rejects(r.resolve(), /substituted by preflight/, `${version} ${substitute}`);
    }
  }
});

test("production Pi auditor resolver blocks a registry-unavailable configured model", async () => {
  for (const { version } of FIXTURES)
    await assert.rejects(resolverFor(version, { available: [] }).resolve(), /configured auditor model is unavailable/, version);
});

test("production Pi auditor resolver reports an unresolvable auditor with the runtime's own refusal", async () => {
  for (const { version } of FIXTURES) {
    const r = resolverFor(version, { respond: () => bound(fixtureFor(version).cases.unknown_agent) });
    await assert.rejects(r.resolve(), /auditor package unresolvable \(preflight-refused: Unknown agent: empirica\.empirica-auditor/, version);
    assert.deepEqual(r.scopes, ["project", "user"], version);
  }
});

test("production Pi auditor resolver stops on an unvouched launch contract instead of trying the next scope", async () => {
  const r = resolverFor("0.75.0", { respond: () => {
    const response = bound(fixtureFor("0.75.0").cases.canonical_auditor);
    response.contract.version = 4;
    return response;
  } });
  await assert.rejects(r.resolve(), /pi-subagents preflight refused \(launch-contract-version\)/);
  assert.deepEqual(r.scopes, ["project"]);
});

test("a preflight that throws (unservable model) propagates: there is nothing to admit", async () => {
  const r = resolverFor("0.75.0", { respond: () => { throw new Error(fixtureFor("0.75.0").cases.unservable_model.threw); } });
  await assert.rejects(r.resolve(), /Unknown subagent model 'missing\/reviewer'/);
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

/** What a no-UI `/empirica` writes: the lines for stderr and how many times it asked for a failing exit. */
interface Diagnostics { lines: string[]; failures: number; write(text: string): void; fail(): void }
function diagnostics(): Diagnostics {
  const d: Diagnostics = { lines: [], failures: 0, write(text) { d.lines.push(text); }, fail() { d.failures += 1; } };
  return d;
}

interface WireOptions {
  /** The identity classes the core refuses an auditor on (`audit.auditor_excluded`); the fake core answers like the real one. */
  excluded?: readonly string[];
  /** Where a notice goes when there is no UI (default: the process's stderr and exit code). */
  diagnostics?: Diagnostics;
  /** How long a no-UI `/empirica` waits for its kickoff to start a run (default: the adapter's bound). */
  kickoffStartMs?: number;
  /** The private `human_surface` ingress fails (`throw`) or answers a typed Fault (`fault`). */
  surface?: "throw" | "fault";
  /** The private `evidence_presentation` ingress fails (`throw`) or answers a typed Fault (`fault`). */
  presentation?: "throw" | "fault";
  /** Replaces the fixture owner registrations before ``session_start`` (default: one healthy owner). */
  owners?: ToolInfo[];
  /** Environment the owner resolver sees (default: none set). */
  env?: OwnerEnv;
  /** Replaces the audit-contract resolver (default: a fixture that answers `bedrock/auditor-model`). */
  resolveAudit?: NonNullable<Parameters<typeof createEmpiricaExtension>[0]["resolveAuditContract"]>;
  /** Replaces the reviewed-inventory lookup (default: the checked-in compat/ files). */
  inventoryFor?: (version: string) => SubagentInventory | undefined;
  /** The adapter's configured subagent tool name (default: `subagent`). */
  subagentToolName?: string;
  /** Pi's own version as the host reports it (default: a version inside the Pi profile's range). */
  hostVersion?: () => unknown;
  /** What the fake preflight reports the auditor can retrieve (default: workspace and local files). */
  auditRetrieval?: readonly ("local" | "web" | "workspace")[];
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
  // What the Python core keeps of the latest StartRun (`invocation.host_runtime`); audit_prepare compares
  // the adapter's current runtime with it exactly (application/host_runtime.py `same_runtime`).
  let recordedRuntime: unknown;
  const dispatch = (req: Request): Response => {
    requests.push(req);
    if (req.command.type === "StartRun") recordedRuntime = req.command.invocation?.host_runtime;
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
      if (request.operation === "human_surface" && options.surface === "throw") throw new Error("the core went away");
      if (request.operation === "human_surface" && options.surface === "fault")
        return { protocol: PROTOCOL, request_id: "trusted", result: { type: "Fault", code: "unavailable", fail_direction: "closed" } };
      if (request.operation === "classify_identity") {
        const payload = request.payload as { provider_id?: unknown; model_id?: unknown } | undefined;
        const identity = classify(payload?.provider_id, payload?.model_id);
        return identity === null ? null as unknown as Record<string, unknown> : { identity };
      }
      if (request.operation === "audit_prepare" && options.excluded?.includes(String(request.payload?.auditor_identity)))
        return { type: "audit_excluded", identities: [...options.excluded].sort(),
          message: `The auditor would run on a model that produced or is authoring the covered evidence (${[...options.excluded].sort().join(", ")}); nothing was launched.` } as never;
      if (request.operation === "audit_prepare") return recordedRuntime !== undefined
        && !isDeepStrictEqual(recordedRuntime, request.payload?.host_runtime)
        ? { type: "audit_refused", reason: "runtime_changed" } : {
        type: "audit_plan",
        plan: { child_id: "ch-1", role_profile: "empirica.empirica-auditor",
          operation_id: `sha256:${"b".repeat(64)}`,
          argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } },
      };
      if (request.operation === "evidence_presentation" && options.presentation === "throw") throw new Error("the core went away");
      if (request.operation === "evidence_presentation" && options.presentation === "fault")
        return { type: "Fault", code: "conflict", fail_direction: "closed" } as never;
      if (request.operation === "evidence_presentation") return { type: "evidence_presentation", receipt_id: "p-1",
        items: [{ subject: "research", id: `sha256:${"a".repeat(64)}`, record_ids: [`sha256:${"a".repeat(64)}`],
          claims: [{ id: "C1", text: "C" }], source_kind: "web", source_ref: "https://example.test/x", quote: "q",
          class: "retrievable", title: "example.test/x",
          description: 'For C1 · Quote: "q" · Does the page say this?', note_title: "Note (optional)" }] };
      if (request.operation === "evidence_confirmation") return { type: "evidence_receipt", result: { type: "Allow" } };
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
    hostVersion: options.hostVersion ?? (() => "1.1.0"),
    ...(options.diagnostics ? { diagnostics: options.diagnostics } : {}),
    ...(options.kickoffStartMs ? { kickoffStartMs: options.kickoffStartMs } : {}),
    ...(options.inventoryFor ? { inventoryFor: options.inventoryFor } : {}),
    ...(options.subagentToolName ? { subagentToolName: options.subagentToolName } : {}),
    resolveAuditContract: options.resolveAudit ?? (async (input, _ctx, runtime) => {
      // Which package's preflight the adapter handed over: the owner's own sentinel answer.
      const answer = await runtime.preflight.resolveSubagentLaunchContract({}) as { ok: boolean; message?: string };
      auditResolutions.push({ ...input, preflightAnswer: answer.ok ? "ok" : answer.message,
        runtimeVersion: runtime.version, inventoryVersion: runtime.inventory.version });
      return { agentFilePath: resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md"),
        model: "bedrock/auditor-model", agentScope: "project",
        auditRetrieval: options.auditRetrieval ?? ["local", "workspace"] };
    }),
  })(pi);
  await pi.sessionStart();
  return { pi, requests, privateRequests, auditResolutions };
}

function toolEvent(toolName: string): ToolCallEvent {
  return { toolName, toolCallId: "tc-1", input: {} };
}

/** The runtime provenance /empirica must record for the default fixture owner (observed, canonical paths). */
function ownerHostRuntime(): NonNullable<InvocationProvenance["host_runtime"]> {
  const pkg = defaultSubagentsPackage();
  return { policy_id: SUBAGENTS_COMPATIBILITY.policy_id, subagents: { package: "pi-subagents", version: "0.74.0",
    owner_path: realpathSync(pkg.entry), package_root: realpathSync(pkg.root),
    preflight_path: realpathSync(path.join(pkg.root, "src", "api", "preflight.ts")), source: "npm:pi-subagents", reviewed: true } };
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
    { host: "pi", interactive: true, signal: "ctx.mode=tui", delegation: false, audit_retrieval: ["local", "workspace"], human_surface: "unavailable", host_runtime: ownerHostRuntime() });
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

test("/empirica records where the session can be found, from the session manager, and nothing it did not give", async () => {
  const seen: Array<{ session?: unknown } | undefined> = [];
  for (const manager of [
    { getEntries: () => [], getSessionId: () => "019e-abc", getSessionFile: () => "/home/u/.pi/sessions/019e-abc.jsonl" },
    { getEntries: () => [], getSessionId: () => "019e-abc", getSessionFile: () => undefined },
    { getEntries: () => [], getSessionId: () => { throw new Error("no id"); }, getSessionFile: () => "" },
    { getEntries: () => [] },
    undefined,
  ]) {
    const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
    await w.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode: "tui", sessionManager: manager });
    const command = w.requests[0].command;
    seen.push(command.type === "StartRun" ? command.invocation : undefined);
  }
  assert.deepEqual(seen.map((i) => i?.session), [
    { label: "019e-abc", locator: "/home/u/.pi/sessions/019e-abc.jsonl" },
    { label: "019e-abc", locator: "019e-abc" },
    undefined, undefined, undefined]);
});

test("/empirica records every Pi mode and operator delegation", async () => {
  const prior = process.env.EMPIRICA_AUTO_DELEGATION;
  try {
    for (const [mode, interactive] of [["tui", true], ["rpc", true], ["print", false], ["json", false]] as const) {
      for (const delegated of [false, true]) {
        if (delegated) process.env.EMPIRICA_AUTO_DELEGATION = "1";
        else delete process.env.EMPIRICA_AUTO_DELEGATION;
        const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
        const ui = Object.assign(new FakeUi(), { select: async () => undefined, input: async () => undefined });
        await w.pi.command("empirica").handler("goal", { ui, mode, hasUI: interactive });
        if (w.requests[0].command.type === "StartRun") assert.deepEqual(w.requests[0].command.invocation,
          { host: "pi", interactive, signal: `ctx.mode=${mode}`, delegation: delegated, audit_retrieval: ["local", "workspace"], human_surface: interactive ? "human" : "unavailable", host_runtime: ownerHostRuntime() });
      }
    }
  } finally {
    if (prior === undefined) delete process.env.EMPIRICA_AUTO_DELEGATION;
    else process.env.EMPIRICA_AUTO_DELEGATION = prior;
  }
});

test("G16 P-04: the start records a human surface only where the report could show a dialog (hasUI with select and input)", async () => {
  for (const [ui, expected] of [
    [Object.assign(new FakeUi(), { select: async () => undefined, input: async () => undefined }), "human"],
    [new FakeUi(), "unavailable"],  // hasUI is true but there is no select/input: the same host fails to present at the report
  ] as const) {
    const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
    await w.pi.command("empirica").handler("goal", { ui, mode: "tui", hasUI: true });
    const start = w.requests[0].command;
    assert.equal(start.type === "StartRun" ? start.invocation?.human_surface : null, expected);
  }
});

test("/empirica records what the preflight says the auditor can retrieve, and re-sends it at audit launch", async () => {
  for (const observed of [["local", "workspace"], ["local"], []] as const) {
    const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined,
      { auditRetrieval: observed });
    await w.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode: "tui" });
    const start = w.requests[0].command;
    assert.deepEqual(start.type === "StartRun" ? start.invocation?.audit_retrieval : null, observed);
    await w.pi.sessionStart(fakeCtx("/work", w.pi.entries));
    await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-r",
      input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
    const prepare = w.privateRequests.find((request) => request.operation === "audit_prepare");
    assert.deepEqual(prepare?.payload?.audit_retrieval, observed);
  }
});

test("/empirica preserves replacement tokens in the literal goal", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await w.pi.command("empirica").handler("keep $& and $$ literal", { ui: new FakeUi() });
  assert.match(w.pi.userMessages[0], /The user invocation is `keep \$& and \$\$ literal`/);
  assert.doesNotMatch(w.pi.userMessages[0], /\$ARGUMENTS/);
});

// ---- G14 item 1: print and json mode have no UI; every /empirica notice must still be seen (F-Q6P-1) -------------

test("without a UI every /empirica refusal goes to stderr with a failing exit, and no run is created", async () => {
  const cases: Array<[string, string, RegExp, (w: Wired) => void]> = [
    ["unknown flags", "--cli-exec goal", /not started — unknown flags: --cli-exec/, () => undefined],
    ["a busy session", "goal", /requires an idle session/, () => undefined],
  ];
  for (const mode of ["print", "json"] as const) {
    for (const [label, args, expected] of cases) {
      const d = diagnostics();
      const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined, { diagnostics: d });
      const ui = new FakeUi();
      await w.pi.command("empirica").handler(args, { ui, mode, hasUI: false, ...(label === "a busy session" ? { isIdle: () => false } : {}) });
      assert.match(d.lines.join("\n"), expected, `${mode}: ${label}`);
      assert.equal(d.failures, 1, `${mode}: ${label} asks for a failing exit`);
      assert.deepEqual(ui.notifications, [], "nothing goes to a UI that is not there");
      assert.equal(w.requests.length, 0);
      assert.equal(w.pi.entries.length, 0);
    }
  }
});

// ---- G19b: Pi's own version is checked against the Pi profile's range at /empirica start -----------------------

/** Versions around the contract's Pi range, derived from it (the test holds no second copy of the range). */
function hostVersionCases(): Array<[string, unknown, boolean]> {
  const [lowMajor, lowMinor] = HOST_COMPATIBILITY.minimum.split(".").map(Number);
  const [highMajor] = HOST_COMPATIBILITY.maximum_exclusive.split(".").map(Number);
  return [
    ["the minimum", HOST_COMPATIBILITY.minimum, true],
    ["a version inside", `${lowMajor}.${lowMinor + 1}.0`, true],
    ["the last of the range", `${highMajor - 1}.99.99`, true],
    ["just below the minimum", `${lowMajor - 1}.99.9`, false],
    ["far below", "0.84.2", false],
    ["the exclusive maximum", HOST_COMPATIBILITY.maximum_exclusive, false],
    ["above the maximum", `${highMajor}.0.1`, false],
    ["a much later major", `${highMajor + 8}.0.0`, false],
    ["undefined", undefined, false],
    ["an empty string", "", false],
    ["a number", 1.1, false],
    ["a two-part version", "1.1", false],
    ["a prerelease", `${lowMajor}.${lowMinor + 1}.0-beta.1`, false],
  ];
}

test("INVARIANT (G19b): /empirica starts exactly when Pi's version is an exact version inside the profile's range, in every mode", async () => {
  const range = spellRange(HOST_COMPATIBILITY);
  for (const [label, version, inside] of hostVersionCases()) {
    for (const mode of ["tui", "print", "json"] as const) {
      const d = diagnostics();
      const ui = new FakeUi();
      const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined,
        { diagnostics: d, hostVersion: () => version });
      const hasUI = mode === "tui";
      await w.pi.command("empirica").handler("goal", { ui, mode, hasUI, waitForIdle: async () => undefined });
      const where = `${label} (${String(version)}), ${mode}`;
      if (inside) {
        assert.ok(w.requests.some((r) => r.command.type === "StartRun"), `${where}: a run starts`);
      } else {
        const shown = (hasUI ? ui.notifications.map((n) => n.message) : d.lines).join("\n");
        assert.equal(w.requests.length, 0, `${where}: nothing is dispatched`);
        assert.equal(w.pi.entries.length, 0, `${where}: no run is recorded`);
        assert.equal(w.pi.userMessages.length, 0, `${where}: no kickoff is sent`);
        assert.ok(shown.includes(range), `${where}: the notice names the range ${range}: ${shown}`);
        assert.ok(shown.includes(typeof version === "string" && version !== "" ? version : "unreadable"),
          `${where}: the notice names the observed version: ${shown}`);
        if (!hasUI) assert.equal(d.failures, 1, `${where}: a failing exit`);
        else assert.deepEqual(ui.notifications.map((n) => n.type), ["error"], where);
      }
    }
  }
});

test("a Pi whose version reader throws is refused as unreadable", async () => {
  const d = diagnostics();
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined,
    { diagnostics: d, hostVersion: () => { throw new Error("no package"); } });
  await w.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode: "print", hasUI: false });
  assert.match(d.lines.join("\n"), /unreadable/);
  assert.equal(d.failures, 1);
  assert.equal(w.requests.length, 0);
});

test("without a UI the core's refusal and a missing subagents tool are seen too", async () => {
  const d = diagnostics();
  const refused = await wire(() => envelope({ type: "Block", reasons: [
    { code: "run.goal_required", message: "A non-empty goal is required" }] }), true, undefined, { diagnostics: d });
  await refused.pi.command("empirica").handler("", { ui: new FakeUi(), mode: "print", hasUI: false });
  assert.match(d.lines.join("\n"), /non-empty goal is required/);
  assert.equal(d.failures, 1);
  const none = diagnostics();
  const noTool = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined, { diagnostics: none });
  noTool.pi.activeTools = [];
  await noTool.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode: "json", hasUI: false });
  assert.ok(none.lines.length >= 1 && none.failures === 1, "a refusal before any run is seen and fails the exit");
  assert.equal(noTool.requests.length, 0);
});

// Pi's print and JSON mode end when the command's promise settles (`print-mode.js`: `await session.prompt(...)`, then
// `runtimeHost.dispose()`), and `pi.sendUserMessage` does not wait for the run it starts (`agent-session.js`). Without a
// wait the run is torn down at once: ctx stale, `turn_end could not resolve the persisted assistant entry ID`, no work
// (F-Q6P-2). So with no UI the command waits until the kickoff's run is idle.
test("without a UI /empirica does not return until the run it started is idle (delegated print mode)", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined, { diagnostics: diagnostics() });
  let release: () => void = () => undefined;
  const idle = new Promise<void>((resolve) => { release = resolve; });
  let waited = 0;
  const ctx = { ui: new FakeUi(), mode: "print" as const, hasUI: false, waitForIdle: () => { waited += 1; return idle; } };
  let returned = false;
  const pending = w.pi.command("empirica").handler("goal", ctx).then(() => { returned = true; });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(w.pi.userMessages.length, 1, "the kickoff was sent");
  assert.equal(waited, 1);
  assert.equal(returned, false, "the command waits for the run");
  release();
  await pending;
  assert.equal(returned, true);
});

test("with a UI /empirica does not wait for the run", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  const ctx = { ui: new FakeUi(), mode: "tui" as const, hasUI: true, waitForIdle: () => new Promise<void>(() => undefined) };
  await w.pi.command("empirica").handler("goal", ctx);
  assert.equal(w.pi.userMessages.length, 1);
});

test("a kickoff that never starts a run does not hold a no-UI command forever", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined,
    { diagnostics: diagnostics(), kickoffStartMs: 30 });
  w.pi.startsRunOnUserMessage = false;
  let waited = 0;
  const ctx = { ui: new FakeUi(), mode: "json" as const, hasUI: false, waitForIdle: async () => { waited += 1; } };
  await w.pi.command("empirica").handler("goal", ctx);
  assert.equal(waited, 0, "no run began, so there is nothing to wait for");
});

test("G16 H-03: a kickoff that never starts is a visible failure that names the retained run, not a success", async () => {
  const d = diagnostics();
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined,
    { diagnostics: d, kickoffStartMs: 25 });
  w.pi.startsRunOnUserMessage = false;
  await w.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode: "print" as const, hasUI: false, waitForIdle: async () => undefined });
  assert.equal(d.failures, 1, "a nonzero exit is requested");
  const said = d.lines.join("\n");
  assert.match(said, /did not start/);
  assert.match(said, /run run-handle-1 is still open/);
  assert.match(said, /report_convergence intent=stop/);
  assert.doesNotMatch(said, /\/empirica started/, "never reported as started");
});

test("with a UI a notice goes to the UI and not to stderr or the exit code", async () => {
  const d = diagnostics();
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined, { diagnostics: d });
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("--cli-exec goal", { ui, mode: "tui", hasUI: true });
  assert.match(ui.notifications[0].message, /unknown flags/);
  assert.deepEqual([d.lines, d.failures], [[], 0]);
});

test("/empirica empty goal reports the core refusal without starting", async () => {
  const w = await wire(() => envelope({ type: "Block", reasons: [
    { code: "run.goal_required", message: "A non-empty goal is required" },
  ] }));
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("", { ui, mode: "tui", hasUI: true });
  assert.equal(w.pi.userMessages.length, 0);
  assert.equal(w.pi.entries.length, 0);
  assert.match(ui.notifications[0].message, /non-empty goal is required/);
  if (w.requests[0].command.type === "StartRun")
    assert.equal(w.requests[0].command.invocation?.interactive, true);
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
    assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_tool_inactive")),
      ui.notifications[0].message);
    assert.match(ui.notifications[0].message, /tool-inactive: the `subagent` tool is registered but not active/);
    assert.match(ui.notifications[0].message, /Call `subagents_enable` and retry, or set pi-subagents `toolActivation: eager` and reload the host/);
    assert.match(ui.notifications[0].message, /a restart alone does not change this/);
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
  createEmpiricaExtension({ ownerEnv: {}, hostVersion: () => "1.1.0", dispatch: () => { throw new Error("no dispatch expected"); } })(pi);
  await pi.sessionStart();
  await pi.command("empirica").handler("goal", { ui });
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_owner_unverified")));
  assert.match(ui.notifications[0].message, /could not be observed: inventory down/);
});

test("an unavailable command inventory is an unobservable owner, not a crash", async () => {
  const pi = new FakePi();
  pi.commandsError = new Error("commands down");
  const ui = new FakeUi();
  createEmpiricaExtension({ ownerEnv: {}, hostVersion: () => "1.1.0", dispatch: () => { throw new Error("no dispatch expected"); } })(pi);
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
  createEmpiricaExtension({ ownerEnv: {}, hostVersion: () => "1.1.0", dispatch: () => { throw new Error("no dispatch expected"); } })(pi);
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
  // Refused at classification: not even the investigation witness is recorded, let alone a reservation.
  assert.deepEqual(w.requests.slice(before), []);
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
  assert.ok(refused?.reason?.includes(reasonMessage("host.subagents_tool_inactive")), refused?.reason);
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
  assert.ok(decision?.reason?.includes(reasonMessage("host.subagents_tool_inactive")), decision?.reason);
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

const BATCH = (promptError: string | null = null) => ({ key: `sha256:${"d".repeat(64)}`,
  record_ids: [`sha256:${"a".repeat(64)}`], claim_ids: [], prompt_error: promptError,
  records: [{ claim_id: "C1", source_kind: "web", source_ref: "https://example.test/x" }] });
const EVIDENCE_WAITING = (route = "human", promptError: string | null = route === "human" ? null : "unavailable",
                          code = "evidence.confirmation_required") => ({ type: "Block" as const,
  run: { id: HANDLE, status: "active" as never, governance: null, confirmation_batch: BATCH(promptError) },
  reasons: [{ code, message: code === "evidence.confirmation_required" ? "Research waits on a human."
    : code.startsWith("claim.spike") ? `Spike problem: ${code}.` : "Record research for C1.",
    parameters: { route, record_ids: [`sha256:${"a".repeat(64)}`] } }] });

function uiCtx(selects: string[], inputs: string[] = [""]) {
  const ctx = fakeCtx();
  ctx.hasUI = true;
  ctx.ui.select = async () => selects.shift();
  ctx.ui.input = async () => inputs.shift();
  return ctx;
}

/** The core's answers around one presentation: the batch is unspent until presented, then spent. */
function spendingResponder(w: () => { privateRequests: Array<{ operation: string }> }) {
  return (req: { command: { type: string } }) => req.command.type === "StartRun"
    ? envelope({ type: "Allow", converged: false, run: run() })
    : envelope(EVIDENCE_WAITING("human", w().privateRequests.some((r) => r.operation === "evidence_presentation")
      ? "already_presented" : null) as never);
}

test("gate: the end of a pass presents the waiting evidence once, then re-evaluates", async () => {
  let evaluations = 0;
  const w = await wire((req) => {
    if (req.command.type === "StartRun") return envelope({ type: "Allow", converged: false, run: run() });
    evaluations += 1;
    return envelope(evaluations === 1 ? EVIDENCE_WAITING() as never : { type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), uiCtx(["Confirm"]));
  assert.equal(decision, undefined, "confirmed evidence lets the second evaluation permit");
  assert.equal(evaluations, 2);
  const ops = w.privateRequests.map((request) => request.operation).filter((op) => op.startsWith("evidence_") || op === "human_surface");
  assert.deepEqual(ops, ["human_surface", "evidence_presentation", "evidence_confirmation"]);
  assert.deepEqual(w.privateRequests.find((r) => r.operation === "human_surface")?.payload, { surface: "human" });
});

test("gate: Later or a cancelled batch grants nothing, blocks with the real reason, and the next explicit report asks again (G14)", async () => {
  for (const selects of [["Later"], []]) {
    // eslint-disable-next-line prefer-const
    let holder: Awaited<ReturnType<typeof wire>>;
    holder = await wire(spendingResponder(() => holder));
    const w = holder;
    await startRun(w);
    const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), uiCtx(selects));
    assert.deepEqual(decision, { block: true, reason: `Research waits on a human.\nhandle: ${HANDLE}` });
    assert.ok(!w.privateRequests.some((request) => request.operation === "evidence_confirmation"));
    // and the next explicit report shows the dialog again (the core's ceiling, not the key, ends it)
    const again = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), uiCtx(["Later"]));
    assert.equal(again?.block, true);
    assert.equal(w.privateRequests.filter((request) => request.operation === "evidence_presentation").length, 2);
  }
});

test("gate: only an unspent sole confirmation pauses; an open claim, a spent key or no interactions block", async () => {
  const cases: Array<[string, ReturnType<typeof EVIDENCE_WAITING>, string]> = [
    ["an open claim leads", EVIDENCE_WAITING("human", null, "claim.research_missing"), "Record research for C1."],
    ["the key was shown already", EVIDENCE_WAITING("human", "already_presented"), "Research waits on a human."],
    ["the interactions are spent", EVIDENCE_WAITING("human", "interaction_limit"), "Research waits on a human."]];
  for (const [label, response, message] of cases) {
    const w = await wire((req) => req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(response as never));
    await startRun(w);
    const ctx = label === "an open claim leads" ? uiCtx([]) : fakeCtx();
    const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), ctx);
    assert.deepEqual(decision, { block: true, reason: `${message}\nhandle: ${HANDLE}` }, label);
  }
});

test("gate: with route unavailable the requirement stays a denial and nothing is presented", async () => {
  // (Route `human` with no UI is the failure to ask of "PI-04" below, never a pause.)
  const w = await wire((req) => req.command.type === "StartRun"
    ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(EVIDENCE_WAITING("unavailable") as never));
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), uiCtx(["Confirm"]));
  assert.equal(decision?.block, true);
  assert.ok(!w.privateRequests.some((request) => request.operation === "evidence_presentation"));
});

function waitingRun(overrides: Record<string, unknown> = {}, context: Record<string, unknown> = {},
                    controlMode = "deliberative") {
  return { id: HANDLE, status: "active" as never, governance: {
    state: "pending", control_mode: controlMode, first_approval: false, prompt_error: null, human_wait: true,
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
      assert.equal(w.privateRequests.filter((r) => r.operation !== "human_surface").length, privateBefore,
        "no governance decision or audit ingress call");
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
    // the core says nobody waits on a human (core.governance.human_wait: exhausted, delegated, post-approval auto...);
    // its every state is a shared case (fixtures/turn-end-predicates.json)
    ["the core's answer is no", { run: waitingRun({ human_wait: false, prompt_error: "governance.interaction_limit" }) }],
    ["ui unavailable", { run: waitingRun({}, { ingress: "unavailable" }) }],
    ["another host's dialog", { run: waitingRun({}, { ingress: "mcp_elicitation" }) }],
    ["not a configuration's reason", { reasons: [{ code: "governance.interaction_limit", message: "limit" }] }],
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
      assert.equal(w.privateRequests.filter((r) => r.operation !== "human_surface").length, privateBefore,
        "no governance decision or audit ingress call");
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
    "../../../.agents/skills/native-qualification/references/pi.md"]) {
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
  // The bound is the contract's audit_launch_policy (host-profiles.json); pi-subagents ≥0.59 has no
  // turn budget, so a turnBudget key would be ignored rather than enforced and must not be sent.
  assert.equal(input.timeoutMs, 900_000);
  assert.deepEqual(input.toolBudget, { soft: 20, hard: 30, block: ["read", "grep", "find", "ls"] });
  assert.equal("turnBudget" in input, false);
  assert.deepEqual(Object.keys(input).sort(),
    ["acceptance", "agent", "agentScope", "async", "context", "model", "task", "timeoutMs", "toolBudget"]);
  assert.equal(input.context, "fresh", "EHB-AUDLAUNCH-02: the real subagent call carries context:fresh");
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
  // The refusal says the run is recoverable, so the author does not end it over a setting.
  assert.match(decision?.reason ?? "", /still active and nothing was spent.*Do not stop the run/);
  assert.deepEqual(w.privateRequests.map((item) => item.operation),
    ["classify_identity", "classify_identity"]);
});

// ---- G14 item 3: Pi picks a different-class auditor from `enabledModels` (ADR-0060 amendment) ----------------------------
// Claude does the same: `adapters/claude/lifecycle.py` `_auditor_alias` picks a reviewer family other than the main one.

/** Classes by the part of the model id before the first dash: sol-1 and sol-2 are one class, opus-1 another. */
const byFamily = (_provider: unknown, model: unknown): string | null => String(model).split("-")[0];

/** A resolver that answers by configuration; `preferredModel` is the adapter asking for a specific candidate. */
function selecting(options: { model: string; source: "override" | "default" | "main"; candidates: string[]; unavailable?: string[] }) {
  const asked: Array<string | undefined> = [];
  const resolveAudit = async (input: Record<string, unknown>) => {
    const preferred = typeof input.preferredModel === "string" ? input.preferredModel : undefined;
    asked.push(preferred);
    if (preferred && options.unavailable?.includes(preferred)) throw new Error(`configured auditor model is unavailable: ${preferred}`);
    return { agentFilePath: resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md"),
      model: preferred ?? options.model, agentScope: "user" as const, auditRetrieval: ["local", "workspace"] as const,
      source: preferred ? "enabledModels" as const : options.source, candidates: options.candidates };
  };
  return { asked, resolveAudit };
}

async function startSelecting(options: Parameters<typeof selecting>[0], author = "p/sol-1") {
  const selection = selecting(options);
  const diag = diagnostics();
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, byFamily,
    { diagnostics: diag, resolveAudit: selection.resolveAudit });
  const ui = new FakeUi();
  const [provider, id] = author.split("/");
  await w.pi.command("empirica").handler("goal", { ...fakeCtx(), ui, mode: "tui", hasUI: true, model: { provider, id } });
  return { w, selection, ui, diag };
}

test("an author on Sol with a Sol default is given the first enabled model of another class, and the choice is shown", async () => {
  const { w, selection, ui } = await startSelecting({ model: "p/sol-2", source: "default",
    candidates: ["p/sol-2", "p/sol-3", "q/opus-1", "q/opus-2"] });
  assert.deepEqual(selection.asked, [undefined, "p/sol-3", "q/opus-1"], "sol-3 is the author's class too; the first other class is opus-1");
  assert.equal(w.requests.length > 0, true, "the run started");
  const shown = ui.notifications.map((item) => item.message).join("\n");
  assert.match(shown, /auditor: q\/opus-1 .*picked from enabledModels.*p\/sol-2 is in your model's class/);
});

test("an unavailable enabled model is skipped", async () => {
  const { selection, w } = await startSelecting({ model: "p/sol-2", source: "default",
    candidates: ["q/opus-1", "q/opus-2"], unavailable: ["q/opus-1"] });
  assert.deepEqual(selection.asked, [undefined, "q/opus-1", "q/opus-2"]);
  assert.equal(w.requests.length > 0, true);
});

test("an explicit auditor override is honoured and never replaced, even when it is the author's class", async () => {
  const same = await startSelecting({ model: "p/sol-2", source: "override", candidates: ["q/opus-1"] });
  assert.deepEqual(same.selection.asked, [undefined], "no other model was tried");
  assert.deepEqual(same.w.requests, [], "no run was created");
  assert.match(same.ui.notifications[0].message, /would run on your own model/);
  const other = await startSelecting({ model: "q/opus-1", source: "override", candidates: ["q/opus-2"] });
  assert.deepEqual(other.selection.asked, [undefined]);
  assert.equal(other.w.requests.length > 0, true);
  assert.doesNotMatch(other.ui.notifications.map((item) => item.message).join("\n"), /picked from enabledModels/);
});

test("with no enabled model of another class the start is refused as before", async () => {
  const { w, ui, selection } = await startSelecting({ model: "p/sol-2", source: "default", candidates: ["p/sol-3", "p/sol-4"] });
  assert.deepEqual(w.requests, []);
  assert.deepEqual(selection.asked, [undefined, "p/sol-3", "p/sol-4"]);
  assert.match(ui.notifications[0].message, /would run on your own model.*no model in enabledModels/);
});

// ---- G15 F-P2-1: the auditor differs from every producer of the covered evidence, not only the current author ----------

/** pi-2: research by Sol, `/model` to Opus, a Sol default auditor. The core's set is {opus, sol}; the adapter compared only the author. */
async function auditAfterModelSwitch(options: Parameters<typeof selecting>[0], excluded: string[]) {
  const selection = selecting(options);
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, byFamily,
    { resolveAudit: selection.resolveAudit, excluded });
  await w.pi.command("empirica").handler("goal", { ...fakeCtx(), ui: new FakeUi(), mode: "tui", hasUI: true, model: { provider: "p", id: "opus-1" } });
  const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-excl",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, { ...fakeCtx(), model: { provider: "p", id: "opus-1" } });
  const prepares = w.privateRequests.filter((request) => request.operation === "audit_prepare");
  return { w, decision, prepares, selection };
}

test("an earlier producer's class is skipped at audit launch: the adapter picks another enabled model, spending no spawn", async () => {
  const { decision, prepares, w } = await auditAfterModelSwitch({ model: "p/sol-2", source: "default",
    candidates: ["p/sol-2", "p/sol-3", "p/gpt-1", "q/haiku-1"] }, ["opus", "sol"]);
  assert.equal(decision, undefined, "the audit launches");
  assert.deepEqual(prepares.map((request) => request.payload?.auditor_identity), ["sol", "gpt"],
    "the core refused sol before any spawn; gpt is the first enabled model outside {opus, sol}");
  assert.equal(w.pi.entries.some((entry) => entry.customType === "empirica.audit"), true);
});

test("an explicit auditor override in the excluded set is refused with the core's reason, never replaced", async () => {
  const { decision, prepares, w } = await auditAfterModelSwitch({ model: "p/sol-2", source: "override",
    candidates: ["p/gpt-1"] }, ["opus", "sol"]);
  assert.equal(decision?.block, true);
  assert.match(decision?.reason ?? "", /produced or is authoring the covered evidence \(opus, sol\)/);
  assert.equal(prepares.length, 1, "no other model was tried");
  assert.equal(w.privateRequests.some((request) => request.operation === "audit_reject"), false, "nothing was reserved");
});

test("with no enabled model outside the excluded set the launch is refused and says what to do", async () => {
  const { decision, prepares } = await auditAfterModelSwitch({ model: "p/sol-2", source: "default",
    candidates: ["p/sol-2", "p/sol-3", "q/opus-2"] }, ["opus", "sol"]);
  assert.equal(decision?.block, true);
  assert.match(decision?.reason ?? "", /no model in enabledModels is available in another class either/);
  assert.equal(prepares.length, 1);
});

test("/empirica does not start a run whose auditor would run on the author's own model", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, () => "same-class");
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("goal", { ...fakeCtx(), ui, mode: "tui" });
  assert.deepEqual(w.requests.map((request) => request.command.type), [], "no run was created");
  assert.equal(ui.notifications.length, 1);
  assert.equal(ui.notifications[0].type, "error");
  assert.match(ui.notifications[0].message,
    /^\/empirica not started: the auditor would run on your own model .*agentOverrides\["empirica\.empirica-auditor"\]/);
  assert.equal(w.pi.userMessages.length, 0, "the workflow was not kicked off");
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

test("an audit under the runtime recorded at StartRun carries that exact runtime to the private admission", async () => {
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await w.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode: "tui" });
  await w.pi.sessionStart(fakeCtx("/work", w.pi.entries));
  const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-same",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(decision, undefined);
  const prepare = w.privateRequests.find((request) => request.operation === "audit_prepare");
  assert.deepEqual(prepare?.payload,
    { host_runtime: ownerHostRuntime(), audit_retrieval: ["local", "workspace"], auditor_identity: "bedrock/auditor-model" });
});

test("an audit is refused, sticky, when the pi-subagents runtime is not the one recorded at StartRun", async () => {
  // Run under owner A (0.75.0); the session is then reloaded with owner B (0.74.0) behind the tool: the
  // runtime recorded for the run would otherwise be the one a 4.1 receipt names.
  const ownerA = makeSubagentsPackage(tempParent("empirica-owner-a-"), { version: "0.75.0" });
  const ownerB = makeSubagentsPackage(tempParent("empirica-owner-b-"), { version: "0.74.0" });
  const w = await wire(() => envelope({ type: "Allow", converged: false, run: run() }), true, undefined,
    { owners: [ownerA.tool()] });
  await w.pi.command("empirica").handler("goal", { ui: new FakeUi(), mode: "tui" });
  const started = w.requests[0]?.command;
  assert.equal(started?.type, "StartRun");
  if (started?.type === "StartRun") assert.equal(started.invocation?.host_runtime?.subagents.version, "0.75.0");

  w.pi.subagentOwners = [ownerB.tool()];
  await w.pi.sessionStart(fakeCtx("/work", w.pi.entries));

  const requests = w.requests.length, entries = w.pi.entries.length;
  const privateAtLaunch = w.privateRequests.length;  // /empirica checked the auditor's independence itself
  const input = { agent: "empirica.empirica-auditor", task: "audit" };
  const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-reloaded", input }, fakeCtx());
  assert.equal(decision?.block, true);
  assert.ok(decision?.reason?.includes(PUBLIC_TOOLS.recovery["host.subagents_owner_unverified"]!.message), decision?.reason);
  assert.match(decision?.reason ?? "", /owner-changed: this run was started under a different pi-subagents runtime/);
  assert.match(decision?.reason ?? "", /@0\.74\.0/);
  // The core refused before reserving anything: no child was reserved, no plan was stored or rejected,
  // and nothing was launched or correlated.
  assert.deepEqual(w.privateRequests.slice(privateAtLaunch).map((request) => request.operation),
    ["classify_identity", "classify_identity", "audit_prepare"]);
  assert.deepEqual(w.requests.slice(requests).map((request) => request.command.type === "ObserveAction"
    ? request.command.action.kind : request.command.type), ["investigate", "GetRun"]);
  assert.equal(w.pi.entries.length, entries);
  assert.equal(Object.hasOwn(input, "timeoutMs"), false);

  // Sticky: no further audit_prepare, and no other subagent call either, until the session restarts.
  const privateBefore = w.privateRequests.length;
  for (const call of [input, { agent: "scout", task: "look" }]) {
    const again = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: `tc-${call.agent}`, input: call }, fakeCtx());
    assert.equal(again?.block, true);
    assert.match(again?.reason ?? "", /owner-changed/);
  }
  assert.equal(w.privateRequests.length, privateBefore);
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

// EHB-AUDRES-04 / EHB-AUDLAUNCH-03 / EHB-INT-03: the result shapes pi-subagents 0.76.1 really returns for
// an audit that did not run to a verdict. Each is checked against the source (execution.js / the executor):
// a timed-out child, a forced-async start, and an interrupted run. None of them may admit a verdict.
const FENCE = "```empirica-verdict\n{\"verdict\":\"pass\"}\n```";

async function auditResultFor(toolCallId: string, event: Omit<ToolResultEvent, "toolCallId">) {
  const child = { child_id: "ch-" + toolCallId, purpose: "audit", resource_class: "audit", state: "reserved" };
  const w = await wire((req) => {
    if (req.command.type === "ObserveAction")
      return envelope({ type: "Allow", converged: false, run: { ...run(), children: [child] } as never });
    if (req.command.type === "GetArgument")
      return envelope({ type: "Allow", converged: false, run: run(),
        argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } } as never);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId,
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  const full: ToolResultEvent = { toolCallId, ...event };
  const replacement = await (w.pi.handlers.get("tool_result") as
    (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>)(full, fakeCtx()) as
    { content?: unknown; details?: unknown } | undefined;
  const operations = w.privateRequests.map((item) => item.operation);
  return { w, full, replacement, operations };
}

test("EHB-AUDRES-04: a timed-out child (isError, the fence inside details.results[0].finalOutput after the partial-output marker) is an audit failure and no verdict", async () => {
  const partial = "Subagent timed out after 900000ms.\n\nThe run was stopped.\n\nPartial output before timeout:\n" + FENCE;
  const { w, full, operations } = await auditResultFor("tc-timeout", {
    isError: true,
    content: [{ type: "text", text: partial }],
    details: { mode: "single", results: [{ agent: "empirica.empirica-auditor", exitCode: 1, timedOut: true,
      error: "Subagent timed out after 900000ms.", finalOutput: partial, outputPartial: true }] },
  });
  assert.equal(w.privateRequests.at(-1)?.operation, "audit_failure");
  assert.equal(w.privateRequests.at(-1)?.state, "failed");
  assert.equal(operations.includes("audit_verdict"), false);
  assert.equal(operations.includes("audit_identity"), false);
  assert.doesNotMatch(JSON.stringify(full.details), /```empirica-verdict/);
  assert.doesNotMatch(JSON.stringify(full.content), /```empirica-verdict/);
});

test("EHB-AUDLAUNCH-03: a forced-async start (started-async content, details.results empty, asyncId) is an audit failure and no verdict", async () => {
  const { w, full, operations } = await auditResultFor("tc-async", {
    content: [{ type: "text", text: "Async: empirica.empirica-auditor [run-1]\nThe async run is detached and running in the background. " + FENCE }],
    details: { mode: "single", runId: "run-1", results: [], asyncId: "run-1", asyncDir: "/tmp/async/run-1" },
  });
  assert.equal(w.privateRequests.at(-1)?.operation, "audit_failure");
  assert.equal(w.privateRequests.at(-1)?.state, "failed");
  assert.equal(operations.includes("audit_verdict"), false);
  assert.doesNotMatch(JSON.stringify(full.content), /```empirica-verdict/);
});

test("EHB-INT-03: an interrupted run (exit 0, 'Run paused after interrupt', no isError, no fence) is an audit failure and no verdict", async () => {
  const { w, operations } = await auditResultFor("tc-interrupt", {
    content: [{ type: "text", text: "Run paused after interrupt (empirica.empirica-auditor). Waiting for explicit next action." }],
    details: { mode: "single", results: [{ agent: "empirica.empirica-auditor", exitCode: 0, interrupted: true,
      finalOutput: "Interrupted. Waiting for explicit next action." }] },
  });
  assert.equal(w.privateRequests.at(-1)?.operation, "audit_failure");
  assert.equal(w.privateRequests.at(-1)?.state, "failed");
  assert.equal(operations.includes("audit_verdict"), false);
});

test("EHB-INT-04: an interrupted (paused) run that carries one complete verdict fence is an audit failure and no verdict", async () => {
  // pi-subagents marks a resumable, non-terminal child with `interrupted` (or `detached`/`stopped`) on its result
  // row; exit 0 and no isError. A verdict is admitted only from a completed run.
  for (const flag of ["interrupted", "detached", "stopped"] as const) {
    const finalOutput = "Interrupted. Waiting for explicit next action.\n" + FENCE;
    const { w, full, operations } = await auditResultFor("tc-int-b-" + flag, {
      content: [{ type: "text", text: "Run paused after interrupt (empirica.empirica-auditor). " + FENCE }],
      details: { mode: "single", results: [{ agent: "empirica.empirica-auditor", exitCode: 0, [flag]: true, finalOutput }] },
    });
    assert.equal(w.privateRequests.at(-1)?.operation, "audit_failure", flag);
    assert.equal(w.privateRequests.at(-1)?.state, "failed", flag);
    assert.equal(operations.includes("audit_verdict"), false, flag);
    assert.equal(operations.includes("audit_identity"), false, flag);
    assert.doesNotMatch(JSON.stringify(full.details), /```empirica-verdict/, flag);
    assert.doesNotMatch(JSON.stringify(full.content), /```empirica-verdict/, flag);
  }
});

test("EHB-INT-04 (positive control): the same fence from a completed run (no interrupted/detached/stopped) is a verdict", async () => {
  const { operations } = await auditResultFor("tc-int-b-ok", {
    content: [{ type: "text", text: FENCE }],
    details: { mode: "single", results: [{ agent: "empirica.empirica-auditor", exitCode: 0, finalOutput: FENCE }] },
  });
  assert.equal(operations.includes("audit_verdict"), true);
});

async function ordinaryChildStates(toolCallId: string, event: Omit<ToolResultEvent, "toolCallId">) {
  const child = { child_id: "ch-" + toolCallId, purpose: "look around", resource_class: "investigation", state: "reserved" };
  const w = await wire((req) => envelope({ type: "Allow", converged: false,
    run: { ...run(), children: req.command.type === "ObserveAction" ? [child] : [] } as never }));
  await startRun(w);
  const before = w.privateRequests.length;
  const gate = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId,
    input: { agent: "worker", task: "look around" } }, fakeCtx());
  assert.equal(gate, undefined, "the ordinary child is admitted");
  await (w.pi.handlers.get("tool_result") as (event: ToolResultEvent, ctx: ReturnType<typeof fakeCtx>) => Promise<unknown>)(
    { toolCallId, ...event }, fakeCtx());
  return w.privateRequests.slice(before).filter((item) => item.operation === "child_event")
    .map((item) => (item.payload as { state: string }).state);
}

test("EHB-INT-05 (PI-01): a paused, detached or stopped ordinary child is failed, never completed (one predicate with the audit path)", async () => {
  for (const flag of ["interrupted", "detached", "stopped"] as const) {
    const states = await ordinaryChildStates("tc-ord-" + flag, {
      content: [{ type: "text", text: "Run paused. Waiting for explicit next action." }],
      details: { mode: "single", results: [{ agent: "worker", exitCode: 0, [flag]: true, finalOutput: "partial" }] },
    });
    assert.deepEqual(states, ["launching", "failed"], flag);
  }
  const done = await ordinaryChildStates("tc-ord-ok", {
    content: [{ type: "text", text: "done" }],
    details: { mode: "single", results: [{ agent: "worker", exitCode: 0, finalOutput: "done" }] },
  });
  assert.deepEqual(done, ["launching", "pending", "completed"]);
});

test("EHB-TOOL-04: structuredContent is replaced together with content, so a fence in it never survives the redaction", async () => {
  // Pi (1.0.0 and later) declares `structuredContent` on tool_result and says handlers that redact `content` must also
  // replace it. The fence is a verdict whichever door it comes through: the admitted case and the refused case.
  for (const [name, event] of [
    ["a verdict", { content: [{ type: "text", text: FENCE }], structuredContent: { text: FENCE, nested: [FENCE] },
      details: { mode: "single", results: [{ agent: "empirica.empirica-auditor", exitCode: 0, finalOutput: FENCE }] } }],
    ["an errored result", { isError: true, content: [{ type: "text", text: "failed" }], structuredContent: { text: FENCE },
      details: { mode: "single", results: [{ agent: "empirica.empirica-auditor", exitCode: 1, finalOutput: "failed" }] } }],
  ] as const) {
    const { full, replacement } = await auditResultFor("tc-sc-" + name.replace(/\W/g, ""), event as never);
    assert.doesNotMatch(JSON.stringify(full), /```empirica-verdict/, name + ": the event itself");
    assert.doesNotMatch(JSON.stringify(replacement), /```empirica-verdict/, name + ": the replacement Pi applies");
    assert.equal((full as { structuredContent?: unknown }).structuredContent, undefined, name);
    assert.equal((replacement as { structuredContent?: unknown } | undefined)?.structuredContent, undefined, name);
  }
});

test("EHB-AUDRES-04 (isError): isError is the only failure signal; the tool_result mirror declares no `error`", () => {
  const types = readFileSync(new URL("../src/pi-types.ts", import.meta.url), "utf8");
  const body = /export interface ToolResultEvent \{([\s\S]*?)\n\}/.exec(types)?.[1] ?? "";
  assert.match(body, /isError\?: boolean/);
  assert.doesNotMatch(body, /\berror\??:/);
  for (const file of ["index.ts", "audit.ts"])
    assert.doesNotMatch(readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8"), /\bevent\.error\b/, file);
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
                                         model: "bedrock/auditor-model", agentScope: "project",
                                         auditRetrieval: ["local", "workspace"] }),
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

// --- subagent call classification at the gate (Empirica 4.1, D4) -------------------------------
// The owner fixture is pi-subagents 0.74.0: launch forms are agent and workflow only.

const refusalReason = reasonMessage("host.subagents_launch_unsupported");
const allowRun = () => envelope({ type: "Allow", converged: false, run: run() });
/** Allows everything and answers every child reservation with a matching reserved author child. */
const allowWithChild = (req: Request): Response => req.command.type === "ObserveAction"
  ? envelope({ type: "Allow", converged: false, run: { ...run(), children: [
      { child_id: "ch-1", purpose: "author child", resource_class: "investigation", state: "reserved" }] } as never })
  : allowRun();

test("subagent: a call with two launch forms is refused as unsupported before anything is recorded", async () => {
  const w = await wire(allowRun);
  await startRun(w);
  const before = w.requests.length;
  const decision = await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-multi", input: { agent: "x", workflow: true } }, fakeCtx());
  assert.equal(decision?.block, true);
  assert.ok(decision?.reason?.includes(refusalReason), decision?.reason);
  assert.match(decision?.reason ?? "", /malformed: more than one launch form: agent, workflow/);
  // The guidance tells the author what IS allowed (one launch form, a reviewed read-only action), not just that something is not.
  assert.match(refusalReason, /accepts exactly one launch form \(for example `agent` or `workflow`\) or a read-only action that the runtime's reviewed versions know/);
  assert.match(refusalReason, /control, mutation, unknown-action, and ambiguous calls are refused/);
  assert.equal(w.requests.length, before, "no investigation witness, no reservation");
  assert.deepEqual(w.privateRequests, []);
});

test("subagent: a workflow launch is an executable launch, recorded as investigation", async () => {
  for (const [id, input] of [["tc-wf", { workflow: true }], ["tc-wf-path", { workflow: "./w/review.js" }]] as const) {
    const w = await wire(allowWithChild);
    await startRun(w);
    const before = w.requests.length;
    assert.equal(await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: id, input: { ...input } }, fakeCtx()), undefined);
    assert.deepEqual(w.requests.slice(before).map((request) => request.command.type === "ObserveAction"
      ? request.command.action.kind : request.command.type), ["investigate", "child_reserve"], id);
  }
});

test("subagent: a launch form the owner's version removed is refused, not silently inert", async () => {
  const w = await wire(allowRun);
  await startRun(w);
  const before = w.requests.length;
  for (const input of [{ workflowScript: "return 1" }, { workflowScriptPath: "w.js" }, { resume: "child-1" }, {}]) {
    const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-removed", input: { ...input } }, fakeCtx());
    assert.equal(decision?.block, true, JSON.stringify(input));
    assert.ok(decision?.reason?.includes(refusalReason), decision?.reason);
  }
  assert.equal(w.requests.length, before);
});

test("subagent: control and mutation actions are refused as unsupported during an active run", async () => {
  const w = await wire(allowRun);
  await startRun(w);
  const before = w.requests.length;
  for (const action of ["steer", "stop", "resume", "schedule.create", "worktree.discard", "create", "frobnicate"]) {
    const decision = await w.pi.toolCall()(
      { toolName: SUBAGENT_TOOL, toolCallId: `tc-${action}`, input: { action, id: "x" } }, fakeCtx());
    assert.equal(decision?.block, true, action);
    assert.ok(decision?.reason?.includes(refusalReason), decision?.reason);
    assert.match(decision?.reason ?? "", new RegExp(`unsupported: action "${action}"`), action);
  }
  assert.equal(w.requests.length, before, "none of them is an investigation witness");
});

test("subagent: every reviewed management action is inert", async () => {
  const w = await wire(allowRun);
  await startRun(w);
  const before = w.requests.length;
  for (const action of ["list", "status", "models", "guide", "doctor", "children.list", "project.status", "lane.status",
    "watchdog.status", "inspector.status", "refine.show", "validate"])
    assert.equal(await w.pi.toolCall()(
      { toolName: SUBAGENT_TOOL, toolCallId: `tc-${action}`, input: { action } }, fakeCtx()), undefined, action);
  assert.equal(w.requests.length, before);
});

test("subagent: without a run, an unsupported call is not refused (nothing to protect)", async () => {
  const w = await wire(() => { throw new Error("should not dispatch without a handle"); });
  assert.equal(await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-norun", input: { action: "stop", id: "x" } }, fakeCtx()), undefined);
  assert.equal(w.requests.length, 0);
});

test("subagent: the classification uses the inventory of the owner's exact version", async () => {
  // 0.64.0: workflowScriptPath is a launch form (executable); at the 0.74.0 default it is refused.
  const w = await wire(allowWithChild, true, undefined, { owners: [otherOwner("0.64.0")] });
  await startRun(w);
  const before = w.requests.length;
  assert.equal(await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-064",
    input: { workflowScriptPath: "w.js" } }, fakeCtx()), undefined);
  assert.equal(w.requests.length, before + 2, "investigate + child_reserve");
});

test("subagent: an owner version outside the supported range is refused at binding (host.subagents_version_unsupported)", async () => {
  for (const version of ["9.9.9", "1.0.0"]) {
    const w = await wire(allowRun, true, undefined,
      { owners: [makeSubagentsPackage(tempParent(), { version }).tool()] });
    const ui = new FakeUi();
    await w.pi.command("empirica").handler("goal", { ui });
    assert.equal(w.requests.length, 0);
    assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_version_unsupported")), ui.notifications[0].message);
    assert.match(ui.notifications[0].message,
      new RegExp(`version-unsupported: pi-subagents ${version.replaceAll(".", "\\.")} is outside the supported range >=0\\.50\\.0 <1\\.0\\.0`));
  }
});

test("subagent: a version in range with no compat record is refused unless its own schema declares the audit launch (ADR-0069)", async () => {
  const w = await wire(allowRun, true, undefined,
    { owners: [makeSubagentsPackage(tempParent(), { version: "0.99.0" }).tool()] });
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("goal", { ui });
  assert.equal(w.requests.length, 0);
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_version_unsupported")), ui.notifications[0].message);
  assert.match(ui.notifications[0].message, /version-unsupported: pi-subagents 0\.99\.0 has no compat record and the subagent tool declares no parameter schema/);
});

test("subagent: a version in range with no compat record starts a run, records reviewed:false, and allows only the reviewed read-only actions", async () => {
  const owner = makeSubagentsPackage(tempParent(), { version: "0.99.0", parameters: schemaDeclaring([...AUDIT_LAUNCH_PARAMETERS]) }).tool();
  const w = await wire(allowRun, true, undefined, { owners: [owner] });
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("goal", { ui });
  const start = w.requests.find((request) => request.command.type === "StartRun");
  assert.ok(start, "the run starts");
  const subagents = (start!.command as { invocation: { host_runtime: { subagents: Record<string, unknown> } } }).invocation.host_runtime.subagents;
  assert.equal(subagents.version, "0.99.0");
  assert.equal(subagents.reviewed, false);
  const before = w.requests.length;
  const call = (input: Record<string, unknown>) => w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "tc-x", input }, fakeCtx());
  // A name some reviewed version has, and the classifier calls read-only management: allowed.
  for (const action of ["list", "status", "models", "children.list", "doctor"])
    assert.equal(await call({ action }), undefined, `${action} is a reviewed read-only action`);
  // A reviewed name the classifier does not call read-only, a name no reviewed version has, and validate (not
  // documented offline for an unreviewed version) are refused with the existing reason.
  for (const action of ["create", "delete", "interrupt", "mission.invented", "validate"]) {
    const decision = await call({ action });
    assert.equal((decision as { block?: boolean } | undefined)?.block, true, `${action} is refused`);
    assert.ok(String((decision as { reason?: string }).reason).includes(refusalReason), action);
  }
  assert.equal(w.requests.length, before, "no management call reaches a core operation");
});

test("subagent: an unreadable inventory is an unobservable owner, a mismatched one an unreviewed version", async () => {
  const cases: Array<[(version: string) => SubagentInventory | undefined, string, RegExp]> = [
    [() => { throw new Error("inventory corrupt"); }, "host.subagents_owner_unverified", /owner-unobservable: .*inventory corrupt/],
    [(version) => ({ ...loadInventory("0.74.0")!, version: `${version}-other` }), "host.subagents_version_unsupported",
      /version-unsupported: the inventory selected for 0\.74\.0 describes 0\.74\.0-other/],
  ];
  for (const [inventoryFor, reason, pattern] of cases) {
    const w = await wire(allowRun, true, undefined, { inventoryFor });
    const ui = new FakeUi();
    await w.pi.command("empirica").handler("goal", { ui });
    assert.equal(w.requests.length, 0);
    assert.ok(ui.notifications[0].message.includes(reasonMessage(reason)), ui.notifications[0].message);
    assert.match(ui.notifications[0].message, pattern);
  }
});

test("subagent: a version whose inventory cannot carry the audit bound is refused at binding", async () => {
  const noBudget = { ...loadInventory("0.74.0")!, supports: { ...loadInventory("0.74.0")!.supports, toolBudget: false } };
  const w = await wire(allowRun, true, undefined, { inventoryFor: () => noBudget });
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("goal", { ui });
  assert.equal(w.requests.length, 0);
  assert.ok(ui.notifications[0].message.includes(reasonMessage("host.subagents_version_unsupported")), ui.notifications[0].message);
  assert.match(ui.notifications[0].message, /cannot carry the audit bound/);
});

test("subagent: with a differently named subagent tool, the default name is an unrecognised surface", async () => {
  const w = await wire(allowRun, true, undefined, { subagentToolName: "run_agent" });
  await startRun(w);
  const before = w.requests.length;
  const decision = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "x", input: { agent: "a" } }, fakeCtx());
  assert.deepEqual(decision, { block: true, reason: "empirica: unrecognized child execution surface" });
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

test("gate: a missing or stale spike on the same claim leads the block, and the waiting evidence is still presented", async () => {
  for (const code of ["claim.spike_missing", "claim.spike_stale"]) {
    const w = await wire((req) => req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() }) : envelope(EVIDENCE_WAITING("human", null, code) as never));
    await startRun(w);
    const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), uiCtx(["Later"]));
    assert.deepEqual(decision, { block: true, reason: `Spike problem: ${code}.\nhandle: ${HANDLE}` }, code);
    assert.equal(w.privateRequests.filter((r) => r.operation === "evidence_presentation").length, 1, code);
  }
});

/** A core that projects the failure the host reported with the evaluation as the batch's `prompt_error`. */
const coreEchoingHostFailure = (req: Request): Response => req.command.type === "StartRun"
    || (req.command as { intent?: string }).intent === "stop"  // the real core ends a stop with an Allow
  ? envelope({ type: "Allow", converged: false, run: run() })
  : envelope(EVIDENCE_WAITING("human", (req.command as { host_failure?: string }).host_failure ?? null) as never);

const HOST_FAILURE_CASES: Array<[string, WireOptions, "surface_unrecorded" | "presentation_failed"]> = [
  ["surface throws", { surface: "throw" }, "surface_unrecorded"],
  ["surface faults", { surface: "fault" }, "surface_unrecorded"],
  ["presentation faults", { presentation: "fault" }, "presentation_failed"],
  ["presentation throws", { presentation: "throw" }, "presentation_failed"],
  ["both fail", { surface: "fault", presentation: "fault" }, "surface_unrecorded"],
];

test("gate: a host failure shows nobody a dialog, says so with the stop next, and a literal agent ends in two calls", async () => {
  for (const [label, options, reason] of HOST_FAILURE_CASES) {
    const w = await wire(coreEchoingHostFailure, true, undefined, options);
    await startRun(w);
    let shown = 0;
    const ctx = uiCtx(["Confirm"]);
    ctx.ui.select = async () => { shown += 1; return "Confirm"; };
    // Call 1: the report the view led with. The gate never settles as an evidence wait for a batch nobody was shown.
    const first = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), ctx);
    assert.equal(first?.block, true, `${label}: must not permit, which would emit the pause notice`);
    const text = String(first?.reason);
    assert.ok(text.includes(AUTHOR_LABELS[`waiting_${reason}`]), `${label}: names the failure in the registry's words: ${text}`);
    assert.match(text, /next: report_convergence intent=stop/, `${label}: stop is the next action`);
    assert.ok(!text.includes("paused"), label);
    assert.equal(shown, 0, label);
    assert.ok(!w.privateRequests.some((r) => r.operation === "evidence_confirmation"), label);
    // The evaluation that produced it carried the failure; a presentation failure is evaluated again with it.
    const evaluations = w.requests.filter((r) => r.command.type === "EvaluateRun")
      .map((r) => (r.command as { host_failure?: string }).host_failure);
    assert.equal(evaluations.at(-1), reason, label);
    // Call 2: the stop the notice named is evaluated as a stop.
    const stop = await w.pi.toolCall()({ toolName: REPORT_CONVERGENCE_TOOL, toolCallId: "tc-2", input: { intent: "stop" } }, ctx);
    assert.equal(stop, undefined, `${label}: the stop is permitted`);
    const last = w.requests.at(-1)!.command as { type: string; intent?: string; host_failure?: string };
    // A stop looks at the surface only when the core says it waits on the human's consent to end (ADR-0063); an auto run's
    // stop carries no host failure.
    assert.deepEqual([last.type, last.intent, last.host_failure], ["EvaluateRun", "stop", undefined], label);
  }
});

test("gate: a failed presentation is never reported as a wait the human was asked about", async () => {
  const w = await wire(coreEchoingHostFailure, true, undefined, { presentation: "fault" });
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), uiCtx(["Confirm"]));
  assert.equal(decision?.block, true, "returning undefined permits execution, which emits the pause notice");
  assert.equal(decision?.reason, `${AUTHOR_LABELS.waiting_presentation_failed}\nnext: report_convergence intent=stop\nhandle: ${HANDLE}`);
});

test("PI-04: with no dialog available a presentable batch is the same failure to ask, never a pause", async () => {
  // The core answers an unspent sole confirmation whatever the surface said; the host has no dialog to show it in.
  const w = await wire((req) => req.command.type === "StartRun" ? envelope({ type: "Allow", converged: false, run: run() })
    : envelope(EVIDENCE_WAITING() as never));
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), fakeCtx());
  assert.equal(decision?.block, true, "undefined would permit and emit the pause notice");
  assert.equal(decision?.reason, `${AUTHOR_LABELS.waiting_presentation_failed}\nnext: report_convergence intent=stop\nhandle: ${HANDLE}`);
  assert.ok(!w.privateRequests.some((r) => r.operation === "evidence_presentation"));
});

test("gate: a core that did not echo the failure is not trusted to: a presentable batch nobody was shown never settles as a wait", async () => {
  // The A6 probe: the core answers an unspent sole confirmation whatever the host reported.
  for (const [label, options, reason] of HOST_FAILURE_CASES) {
    const w = await wire((req) => req.command.type === "StartRun" ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope(EVIDENCE_WAITING() as never), true, undefined, options);
    await startRun(w);
    let shown = 0;
    const ctx = uiCtx(["Confirm"]);
    ctx.ui.select = async () => { shown += 1; return "Confirm"; };
    const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), ctx);
    assert.equal(decision?.block, true, label);
    assert.ok(String(decision?.reason).includes(AUTHOR_LABELS[`waiting_${reason}`]), `${label}: ${String(decision?.reason)}`);
    assert.equal(shown, 0, label);
  }
});

test("gate: a presentation the core refuses for a reason it knows is worded by the core, not as a host failure", async () => {
  // A raced presentation: the core answers the second evaluation with its own refusal, which outranks the host's report.
  const w = await wire((req) => req.command.type === "StartRun" ? envelope({ type: "Allow", converged: false, run: run() })
    : envelope(EVIDENCE_WAITING("human", (req.command as { host_failure?: string }).host_failure ? "already_presented" : null) as never),
    true, undefined, { presentation: "fault" });
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), uiCtx(["Confirm"]));
  assert.deepEqual(decision, { block: true, reason: `Research waits on a human.\nhandle: ${HANDLE}` });
});

// --- G17b item 4: one owner of ending a run (`onTerminal`) ------------------------------------------------------------

test("INVARIANT: every path that sees a terminal result ends the run once: tombstone, a working read, idempotent", async () => {
  const paths: Record<string, (w: Wired) => unknown> = {
    gate: (w) => w.pi.toolCall()({ toolName: "report_convergence", toolCallId: "tc-g", input: {} }, { ui: new FakeUi() } as never),
    execute: (w) => execTool(w, "report_convergence"),
    settle: (w) => (w.pi.handlers.get("agent_before_settle") as (e: unknown, c: unknown) => Promise<unknown>)(
      { type: "agent_before_settle", entries: [], continue: false }, fakeCtx("/work")),
  };
  const sequences: string[][] = [];
  for (const first of Object.keys(paths)) {
    sequences.push([first]);
    for (const second of Object.keys(paths)) sequences.push([first, second]);
  }
  for (const sequence of sequences) {
    for (const [status, converged] of [["converged", true], ["stopped_residual", false], ["stopped_budget", false]] as const) {
      const w = await wire((req) => req.command.type === "StartRun"
        ? envelope({ type: "Allow", converged: false, run: run() })
        : envelope({ type: "Allow", converged, run: run(status) }));
      await startRun(w);
      for (const name of sequence) {
        try { await paths[name](w); } catch { /* execute after a gate that already ended the run reports "no active run" */ }
        const tombstones = w.pi.entries.filter((entry) => entry.customType === "empirica.run.done");
        assert.equal(tombstones.length, 1, `${sequence.join("+")}/${status}: after ${name}`);
      }
      const read = await execTool(w, "empirica_read", { operation: "GetRun" }) as { content: Array<{ text: string }> };
      assert.doesNotMatch(read.content[0].text, /No active Empirica run/, `${sequence.join("+")}: a read after the end works`);
    }
  }
});
