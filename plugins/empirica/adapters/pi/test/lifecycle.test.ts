// agent_settled is observational (ADR-32): it may enqueue a best-effort follow-up
// but never blocks and never throws. These tests pin that honesty.

import { test } from "node:test";
import assert from "node:assert/strict";

import { PROTOCOL, type Request, type Response, type Result } from "../src/contract.ts";
import { createEmpiricaExtension } from "../src/index.ts";
import { FakePi, FakeUi } from "./fakes.ts";

const HANDLE = "run-handle-1";

function envelope(result: Result): Response {
  return { protocol: PROTOCOL, request_id: "x", result };
}
function run(status = "active", revision = 1) {
  return { id: HANDLE, status: status as never, revision };
}

function wire(responder: (req: Request) => Response) {
  const requests: Request[] = [];
  const pi = new FakePi();
  createEmpiricaExtension({
    dispatch: (req: Request): Response => {
      requests.push(req);
      return responder(req);
    },
    deriveSelector: () => ({ project: "p", session: "s" }),
  })(pi);
  return { pi, requests };
}

async function startRun(pi: FakePi): Promise<void> {
  await pi.command("empirica").handler("goal", { ui: new FakeUi() });
}

test("agent_settled on an active-but-blocked run enqueues a best-effort follow-up", async () => {
  const w = wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Block", reason: "root claim unproven", run: run() }),
  );
  await startRun(w.pi);
  await w.pi.agentSettled()({}, { ui: new FakeUi() });

  // It evaluated with the advisory intent, not the gate intent.
  const evalReq = w.requests.at(-1)!;
  assert.equal(evalReq.command.type, "EvaluateRun");
  assert.equal(evalReq.command.type === "EvaluateRun" ? evalReq.command.intent : null, "continue");

  assert.equal(w.pi.sentMessages.length, 1);
  const msg = w.pi.sentMessages[0];
  assert.equal(msg.deliverAs, "followUp");
  assert.match(msg.text, /reminder, not a gate/);
  assert.match(msg.text, /root claim unproven/);
});

test("agent_settled on a converged/allowed run enqueues nothing", async () => {
  const w = wire((req) =>
    req.command.type === "StartRun"
      ? envelope({ type: "Allow", converged: false, run: run() })
      : envelope({ type: "Allow", converged: true, run: run("converged", 4) }),
  );
  await startRun(w.pi);
  await w.pi.agentSettled()({}, { ui: new FakeUi() });
  assert.equal(w.pi.sentMessages.length, 0);
});

test("agent_settled with no active run dispatches nothing and stays silent", async () => {
  const w = wire(() => {
    throw new Error("should not dispatch");
  });
  await w.pi.agentSettled()({}, { ui: new FakeUi() });
  assert.equal(w.requests.length, 0);
  assert.equal(w.pi.sentMessages.length, 0);
});

test("agent_settled swallows a transport failure — never a gate, never a throw", async () => {
  const w = wire((req) => {
    if (req.command.type === "StartRun") {
      return envelope({ type: "Allow", converged: false, run: run() });
    }
    throw new Error("core unreachable");
  });
  await startRun(w.pi);
  // Must not reject.
  await w.pi.agentSettled()({}, { ui: new FakeUi() });
  assert.equal(w.pi.sentMessages.length, 0);
});
