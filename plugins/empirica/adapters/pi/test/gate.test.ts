// End-to-end through the registered handlers: the /empirica commands and the
// tool_call convergence gate. Dispatch is a fake that records requests and
// returns scripted decisions — no core, no bridge, no Pi runtime.

import { test } from "node:test";
import assert from "node:assert/strict";

import { PROTOCOL, type Request, type Response, type Result } from "../src/contract.ts";
import { REPORT_CONVERGENCE_TOOL } from "../src/translate.ts";
import { createEmpiricaExtension } from "../src/index.ts";
import { FakePi, FakeUi } from "./fakes.ts";
import type { ToolCallEvent } from "../src/pi-types.ts";

const HANDLE = "run-handle-1";

function envelope(result: Result): Response {
  return { protocol: PROTOCOL, request_id: "x", result };
}
function run(status = "active", revision = 1) {
  return { id: HANDLE, status: status as never, revision };
}

interface Wired {
  pi: FakePi;
  requests: Request[];
}

function wire(responder: (req: Request) => Response): Wired {
  const requests: Request[] = [];
  const pi = new FakePi();
  const dispatch = (req: Request): Response => {
    requests.push(req);
    return responder(req);
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

async function startRun(w: Wired): Promise<FakeUi> {
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("build the thing", { ui });
  return ui;
}

// --- /empirica ---------------------------------------------------------------

test("/empirica dispatches StartRun with the derived selector and stores the handle", async () => {
  const w = wire(() => envelope({ type: "Allow", converged: false, run: run() }));
  const ui = await startRun(w);

  const start = w.requests[0];
  assert.equal(start.command.type, "StartRun");
  assert.deepEqual(
    start.command.type === "StartRun" ? start.command.selector : null,
    { project: "p", session: "s" },
  );
  assert.match(ui.last()!.message, new RegExp(HANDLE));

  // The stored handle is used by a later status read.
  const statusUi = new FakeUi();
  await w.pi.command("empirica-status").handler("", { ui: statusUi });
  const get = w.requests.at(-1)!;
  assert.equal(get.command.type, "GetRun");
  assert.equal(get.command.type === "GetRun" ? get.command.run_id : null, HANDLE);
});

test("/empirica reports a start failure rather than throwing", async () => {
  const w = wire(() => {
    throw new Error("bridge offline");
  });
  const ui = new FakeUi();
  await w.pi.command("empirica").handler("g", { ui });
  assert.equal(ui.last()!.type, "error");
  assert.match(ui.last()!.message, /bridge offline/);
});

test("/empirica-status with no run reports no active run and dispatches nothing", async () => {
  const w = wire(() => {
    throw new Error("should not dispatch");
  });
  const ui = new FakeUi();
  await w.pi.command("empirica-status").handler("", { ui });
  assert.equal(w.requests.length, 0);
  assert.match(ui.last()!.message, /no active run/);
});

// --- tool_call gate ----------------------------------------------------------

test("gate: report_convergence tool is blocked with the reason on Block", async () => {
  const w = wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Block", reason: "3 claims lack evidence", run: run() }),
  );
  await startRun(w);

  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.deepEqual(decision, { block: true, reason: "3 claims lack evidence" });

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
      : envelope({ type: "Allow", converged: true, run: run("converged", 9) }),
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
  let started = false;
  const w = wire((req) => {
    if (req.command.type === "StartRun") {
      started = true;
      return envelope({ type: "Allow", converged: false, run: run() });
    }
    throw new Error("core unreachable");
  });
  await startRun(w);
  assert.ok(started);
  const decision = await w.pi.toolCall()(toolEvent(REPORT_CONVERGENCE_TOOL), { ui: new FakeUi() });
  assert.equal(decision?.block, true);
  assert.match(decision!.reason!, /failing closed/);
});

test("gate: a closed Fault blocks; an open Fault permits", async () => {
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
  assert.equal(open, undefined);
});

// --- /report-convergence command --------------------------------------------

test("/report-convergence surfaces a Block as an error notice", async () => {
  const w = wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Block", reason: "audit owed", run: run() }),
  );
  await startRun(w);
  const ui = new FakeUi();
  await w.pi.command("report-convergence").handler("", { ui });
  assert.equal(ui.last()!.type, "error");
  assert.match(ui.last()!.message, /audit owed/);
});
