// End-to-end through the registered handlers: the /empirica command and the
// tool_call convergence gate. Dispatch is a fake that records requests and
// returns scripted decisions — no core, no bridge, no Pi runtime. Proves the
// central guard rejects malformed responses so they cannot permit the hard gate.

import { test } from "node:test";
import assert from "node:assert/strict";

import { PROTOCOL, type Request, type Response, type Result } from "../src/contract.ts";
import { REPORT_CONVERGENCE_TOOL, SUBAGENT_TOOL, subagentUnsupportedReason } from "../src/translate.ts";
import { createEmpiricaExtension } from "../src/index.ts";
import { FakePi, FakeUi, fakeCtx } from "./fakes.ts";
import type { ToolCallEvent } from "../src/pi-types.ts";

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
}

function wire(
  responder: (req: Request) => Response,
  echoRequestId = true,
): Wired {
  const requests: Request[] = [];
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
  })(pi);
  return { pi, requests };
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

test("/empirica refuses before StartRun because the profile cannot progress", async () => {
  const w = wire(() => {
    throw new Error("the unsupported preflight must not dispatch");
  });
  const ui = new FakeUi();

  await w.pi.command("empirica").handler("build the thing", { ui });

  assert.equal(w.requests.length, 0);
  assert.equal(w.pi.entries.length, 0);
  assert.equal(ui.last()!.type, "error");
  assert.match(ui.last()!.message, /author actions and the bound audit lifecycle are unavailable/);
  assert.match(ui.last()!.message, /No run was created/);
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

test("gate: a non-gated tool passes without any dispatch", async () => {
  const w = wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  await startRun(w);
  const before = w.requests.length;
  const decision = await w.pi.toolCall()(toolEvent("bash"), { ui: new FakeUi() });
  assert.equal(decision, undefined);
  assert.equal(w.requests.length, before); // no round-trip for un-gated tools
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

// --- subagent local fail-closed (D8-owned, no dispatch) --------------------

test("subagent: executable launch with a real handle is denied with D8 reason and no dispatch", async () => {
  const w = wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Allow", converged: true, run: run("converged") }),
  );
  await startRun(w);
  const before = w.requests.length;
  const decision = await w.pi.toolCall()(
    { toolName: SUBAGENT_TOOL, toolCallId: "tc-sub", input: { agent: "empirica:empirica-auditor" } },
    { ui: new FakeUi() },
  );
  assert.equal(decision?.block, true);
  assert.equal(decision!.reason, subagentUnsupportedReason());
  assert.match(decision!.reason!, /D8/);
  // No dispatch occurred for the subagent call.
  assert.equal(w.requests.length, before);
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

async function execTool(w: Wired, name: string) {
  return w.pi.tools.get(name)!.execute("x", {}, new AbortController().signal, () => {}, { ui: new FakeUi() });
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

test("status uses the restored opaque handle across selector changes", async () => {
  const w = wire((req) => {
    assert.equal(req.command.type, "RestoreRun");
    assert.equal(req.command.type === "RestoreRun" ? req.command.run_id : null, HANDLE);
    return envelope({ type: "Allow", converged: false, run: run() });
  });
  await startRun(w);
  const result = await execTool(w, "empirica_status");
  assert.match(result.content[0].text, /empirica run run-handle-1: status=active/);
  assert.equal(w.requests.length, 1);
});

test("status without a restored handle resolves the current selector", async () => {
  const w = wire((req) => {
    assert.equal(req.command.type, "ResolveRun");
    assert.deepEqual(
      req.command.type === "ResolveRun" ? req.command.selector : null,
      { project: "p", session: "s" },
    );
    return envelope({ type: "Inert", reason: "no_run" });
  });

  const result = await execTool(w, "empirica_status");

  assert.match(result.content[0].text, /no active run/);
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
