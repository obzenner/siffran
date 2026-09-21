// End-to-end through the registered handlers: the /empirica command and the
// tool_call convergence gate. Dispatch is a fake that records requests and
// returns scripted decisions — no core, no bridge, no Pi runtime. Proves the
// central guard rejects malformed responses so they cannot permit the hard gate.

import { test } from "node:test";
import assert from "node:assert/strict";

import { PROTOCOL, type Request, type Response, type Result } from "../src/contract.ts";
import { REPORT_CONVERGENCE_TOOL, SUBAGENT_TOOL } from "../src/translate.ts";
import { createEmpiricaExtension, DEFAULT_SKILLS_DIR } from "../src/index.ts";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { FakePi, FakeUi, fakeCtx } from "./fakes.ts";
import type { ToolCallEvent, ToolResultEvent } from "../src/pi-types.ts";
import type { PrivateIngressRequest } from "../src/private-transport.ts";

const HANDLE = "run-handle-1";

function envelope(result: Result, requestId = "x"): Response {
  return { protocol: PROTOCOL, request_id: requestId, result };
}
function run(status = "active") {
  return { id: HANDLE, status: status as never };
}

interface Wired {
  pi: FakePi;
  requests: Request[];
  privateRequests: PrivateIngressRequest[];
}

function wire(
  responder: (req: Request) => Response,
  echoRequestId = true,
): Wired {
  const requests: Request[] = [];
  const privateRequests: PrivateIngressRequest[] = [];
  const pi = new FakePi();
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
      privateRequests.push(request);
      if (request.operation === "audit_prepare") return {
        type: "audit_plan",
        plan: { child_id: "ch-1", role_profile: "empirica.empirica-auditor",
          operation_id: `sha256:${"b".repeat(64)}`,
          argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } },
      };
      if (request.operation === "audit_verdict")
        return { type: "audit_verdict", admitted: true };
      return { type: "ok" };
    },
    resolveAuditContract: async () => ({
      agentFilePath: resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md"),
      model: "bedrock/auditor-model",
    }),
  })(pi);
  return { pi, requests, privateRequests };
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
  const w = wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  const ui = new FakeUi();

  await w.pi.command("empirica").handler("build the thing", { ui });

  assert.equal(w.requests.length, 1);
  assert.equal(w.requests[0].command.type, "StartRun");
  assert.equal(w.pi.entries.length, 1);
  assert.equal(w.pi.entries[0].customType, "empirica.run");
  assert.match(w.pi.modelMessages[0].content, /empirica_observe/);
});

// --- tool_call gate ----------------------------------------------------------

test("gate: report_convergence tool is blocked with the reason on Block", async () => {
  const w = wire((req) =>
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

test("gate: report_convergence tool is permitted on Allow", async () => {
  const w = wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Allow", converged: true, run: run("converged") }),
  );
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision, undefined); // permit
});

test("gate: honest stop intent reaches pre-tool evaluation and execute exactly once", async () => {
  const w = wire((req) => req.command.type === "StartRun"
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
  const w = wire((req) => req.command.type === "ObserveAction"
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
  const w = wire((req) => req.command.type === "ObserveAction"
    ? envelope({ type: "Block", run: run(), reasons: [{ code: "route.required",
        message: "route first" }] })
    : envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent("read"), { ui: new FakeUi() });
  assert.deepEqual(decision, { block: true, reason: "empirica investigation denied: route first" });
});

test("gate: with no active run the gated tool passes (nothing to gate)", async () => {
  const w = wire(() => {
    throw new Error("should not dispatch without a run");
  });
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, 0);
});

test("gate: an unavailable transport fails CLOSED (blocks the report)", async () => {
  const w = wire(() => {
    throw new Error("core unreachable");
  });
  await startRun(w);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /failing closed/);
});

test("gate: closed and open Fault both block the report", async () => {
  const wClosed = wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Fault", code: "corrupt_run", fail_direction: "closed" }),
  );
  await startRun(wClosed);
  const closed = await wClosed.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), {
    ui: new FakeUi(),
  });
  assert.equal(closed?.block, true);

  const wOpen = wire((req) =>
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
  const w = wire((req) =>
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
  const w = wire((req) =>
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
  const w = wire((req) =>
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

test("subagent: canonical auditor is reserved, bound, attributed, and prompt-injected", async () => {
  const child = { child_id: "ch-1", purpose: "audit", state: "reserved" };
  const w = wire((req) => {
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
    agent: "empirica.empirica-auditor", task: "audit",
  };
  const decision = await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-sub", input }, fakeCtx(),
  );
  assert.equal(decision, undefined);
  assert.equal(input.async, false);
  assert.equal(input.timeoutMs, 900_000);
  assert.deepEqual(input.turnBudget, { maxTurns: 8, graceTurns: 1 });
  assert.deepEqual(input.toolBudget, { soft: 20, hard: 30, block: ["write", "edit"] });
  assert.match(String(input.task), /AUDIT DOSSIER/);
  assert.deepEqual(w.privateRequests.map((item) => item.operation), ["audit_prepare"]);
  assert.equal(w.pi.entries.at(-1)?.customType, "empirica.audit");
});

test("tool_result redacts before privately admitting the correlated verdict", async () => {
  const child = { child_id: "ch-1", purpose: "audit", state: "reserved" };
  const w = wire((req) => {
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
  const child = { child_id: "ch-unverified", purpose: "audit", state: "reserved" };
  const w = wire((req) => {
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
    argument: { argument_digest: `sha256:${"a".repeat(64)}`, claims: [] } };
  const correlation = { toolCallId: "tc-restored", runHandle: HANDLE, nativeId: "tc-restored",
    plan,
    author: { provider_id: "bedrock", model_id: "author-model", observed_by: "host", source: "pi" },
    auditor: { provider_id: "bedrock", model_id: "auditor-model",
      observed_by: "configuration", source: "preflight" } };
  const unresolved = wire(() => envelope({ type: "Allow", converged: false, run: run() }));
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

  const completed = wire(() => envelope({ type: "Allow", converged: false, run: run() }));
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
  const child = { child_id: "ch-orphan", purpose: "audit", state: "reserved" };
  const w = wire((req) => {
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
  const child = { child_id: "ch-malformed", purpose: "audit", state: "reserved" };
  const w = wire((req) => {
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
  const child = { child_id: "ch-error", purpose: "audit", state: "reserved" };
  const w = wire((req) => {
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
  const w = wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const before = w.privateRequests.length;
  const evil = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "evil",
    input: { agent: "evil-empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(evil?.block, true);
  const overridden = await w.pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "override",
    input: { agent: "empirica.empirica-auditor", task: "audit", model: "author-model" } }, fakeCtx());
  assert.equal(overridden?.block, true);
  assert.equal(w.privateRequests.length, before);
});

test("shadowed packaged auditor identity is blocked before reservation", async () => {
  const requests: Request[] = [];
  const pi = new FakePi();
  createEmpiricaExtension({
    dispatch: (request) => { requests.push(request); return {
      ...envelope({ type: "Allow", converged: false, run: run() }),
      request_id: request.request_id,
    }; },
    deriveSelector: () => ({ project: "p", session: "s" }),
    privateIngress: async () => ({ type: "ok" }),
    resolveAuditContract: async () => ({ agentFilePath: "/project/.pi/agents/shadow.md",
                                         model: "bedrock/auditor-model" }),
  })(pi);
  await (pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)(
    {}, fakeCtx("/work", [{ customType: "empirica.run", data: { runHandle: HANDLE } }]));
  const decision = await pi.toolCall()({ toolName: SUBAGENT_TOOL, toolCallId: "shadow",
    input: { agent: "empirica.empirica-auditor", task: "audit" } }, fakeCtx());
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /shadowed/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].command.type, "ObserveAction");
  assert.equal(requests[0].command.type === "ObserveAction"
    ? requests[0].command.action.kind : null, "investigate");
});

test("subagent: management list with a real handle is inert (no denial)", async () => {
  const w = wire((req) =>
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
  const w = wire(() => {
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
  const w = wire((req) =>
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
  const w = wire(deny({ type: "Block", run: run(), reasons: [{ code: "audit.required", message: "independent audit required" }] }));
  await startRun(w);
  await assert.rejects(() => execTool(w, "report_convergence"), /independent audit required/);
});

test("direct tool: report_convergence execute rejects on Inert(no_run) with active handle", async () => {
  const w = wire(deny({ type: "Inert", reason: "no_run" }));
  await startRun(w);
  await assert.rejects(() => execTool(w, "report_convergence"), /no active run to report/);
});

test("direct tool: report_convergence execute rejects on open Fault with active handle", async () => {
  const w = wire(deny({ type: "Fault", code: "unavailable", fail_direction: "open" }));
  await startRun(w);
  await assert.rejects(() => execTool(w, "report_convergence"), /unavailable/);
});

test("direct tool: report_convergence forwards an honest stop intent", async () => {
  const w = wire((req) => req.command.type === "StartRun"
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
  const w = wire((req) => req.command.type === "StartRun"
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
  const w = wire(() => { throw new Error("terminal handle must not dispatch"); });
  const ctx = fakeCtx("/work", [
    { customType: "empirica.run", data: { runHandle: HANDLE } },
    { customType: "empirica.run.done", data: { runHandle: HANDLE } },
  ]);
  await (w.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, ctx);
  assert.equal(await w.pi.toolCall()(toolEvent("read"), { ui: new FakeUi() }), undefined);
  assert.equal(w.requests.length, 0);
});

test("empirica_read uses the restored opaque handle", async () => {
  const w = wire((req) => {
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
  const w = wire((req) => {
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
  const w = wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const ctx = fakeCtx("/work", [{ customType: "empirica.run", data: { runHandle: "restored" } }]);
  await (w.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, ctx);
  const out = await (w.pi.handlers.get("session_before_compact") as (e: unknown, c: unknown) => unknown)({ preparation: { firstKeptEntryId: "e", tokensBefore: 4 } }, ctx);
  assert.match((out as { compaction: { summary: string } }).compaction.summary, /restored/);
  assert.equal(w.pi.entries.length, 0);
});
