import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createEmpiricaExtension } from "../src/index.ts";
import { FakePi, FakeUi } from "./fakes.ts";

const allowStart = { protocol: "empirica/v1", request_id: "start", result: { type: "Allow", converged: false, run: { id: "run-1", status: "active", revision: 1 } } };
const allow = (extra: Record<string, unknown> = {}) => ({ protocol: "empirica/v1", request_id: "x", result: { type: "Allow", run: { id: "run-1", status: "active", revision: 1 }, ...extra } });
const contract = { contract_id: "c", revision: 1, obligations: [], provenance: [], supersedes: [], retired: [], verdict: { satisfied: [], holds: [], violated: [], residual: [], unwitnessed: [], held: [] } };
const block = (reason = "blocked") => ({ protocol: "empirica/v1", request_id: "x", result: { type: "Block", reason, run: { id: "run-1", status: "active", revision: 1, contract } } });
const fault = (message = "bad") => ({ protocol: "empirica/v1", request_id: "x", result: { type: "Fault", code: "internal", message, fail_direction: "closed" } });

type Req = any;
async function setup(respond: (r: Req) => any = () => allow()) {
  const pi = new FakePi(); const requests: Req[] = [];
  createEmpiricaExtension({
    dispatch: (r: Req) => { requests.push(r); return r.command.type === "StartRun" ? allowStart : respond(r); },
    deriveSelector: () => ({ project: "p", session: "s" }),
    skillsDir: "/tmp/no-skills",
  })(pi);
  await pi.command("empirica").handler("goal", { ui: new FakeUi() });
  return { pi, requests, gate: pi.toolCall(), result: pi.handlers.get("tool_result") as any };
}
function action(r: Req) { return r.command.action; }

// Every executable launch shape is deliberately tested: only one of these keys is executable.
test("spawn shape: action-only and multi-key calls pass untouched; each xor shape reserves", async () => {
  const { gate, requests } = await setup();
  for (const input of [
    { action: "list" },
    { agent: "worker", workflowScript: "flow" },
    { agent: "worker", resume: "old" },
    { workflowScript: "flow", resume: "old" },
  ]) assert.equal(await gate({ toolName: "subagent", toolCallId: Math.random().toString(), input }, {} as any), undefined);
  for (const [i, input] of [
    { agent: "worker" }, { workflowScript: "flow" }, { resume: "old" },
  ].entries()) assert.equal(await gate({ toolName: "subagent", toolCallId: `x${i}`, input }, {} as any), undefined);
  assert.deepEqual(requests.slice(1).map((r) => [r.command.type, action(r)]), [
    ["ObserveAction", { kind: "reserve_spawn" }],
    ["ObserveAction", { kind: "reserve_spawn" }],
    ["ObserveAction", { kind: "reserve_spawn" }],
  ]);
});

test("non-auditor executable spawn reserves but never tickets", async () => {
  const { gate, requests } = await setup();
  await gate({ toolName: "subagent", toolCallId: "x", input: { agent: "worker", task: "do it" } }, {} as any);
  assert.deepEqual(requests.slice(1).map((r) => action(r)), [{ kind: "reserve_spawn" }]);
});

test("agent aliases resolve their concrete definition model and never become model ids", async () => {
  const oldHome = process.env.HOME; const home = mkdtempSync(join(tmpdir(), "empirica-agent-"));
  process.env.HOME = home;
  const agents = join(home, ".pi", "agent", "agents"); mkdirSync(agents, { recursive: true });
  writeFileSync(join(agents, "empirica-auditor.md"), "---\nmodel: provider/concrete-auditor-v2\n---\nAudit.");
  try {
    const { gate, requests } = await setup((r) => action(r)?.kind === "audit_ticket" ? block("ticket denied") : allow());
    await gate({ toolName: "subagent", toolCallId: "resolved", input: { agent: "empirica-auditor" } }, {} as any);
    const ticket = requests.find((r) => action(r)?.kind === "audit_ticket");
    assert.equal(action(ticket).actor.model, "concrete-auditor-v2");
  } finally { if (oldHome === undefined) delete process.env.HOME; else process.env.HOME = oldHome; }
});

test("auditor ticket Block denies with rendered contract", async () => {
  const { gate, requests } = await setup((r) => r.command.type === "ObserveAction" && action(r).kind === "audit_ticket" ? block("ticket denied") : allow());
  const out = await gate({ toolName: "subagent", toolCallId: "x", input: { agent: "empirica:empirica-auditor", model: "anthropic/claude-auditor", task: "audit" } }, {} as any);
  assert.equal(out?.block, true); assert.match(out?.reason ?? "", /ticket denied/); assert.match(out?.reason ?? "", /Obligation contract/);
  assert.deepEqual(requests.slice(1).map((r) => action(r)), [{ kind: "reserve_spawn" }, { kind: "audit_ticket", actor: { model: "claude-auditor", harness: "pi", provider: "pi", source_type: "LLM_JUDGE", attribution: "declared" }, witnessed: false }]);
});

test("auditor GetArgument Fault voids nonce and denies", async () => {
  const { gate, requests } = await setup((r) => r.command.type === "GetArgument" ? fault("argument unavailable") : allow({ run: { ticket: { nonce: "n-1" } } }));
  const out = await gate({ toolName: "subagent", toolCallId: "x", input: { agent: "empirica:empirica-auditor", model: "anthropic/claude-auditor", task: "audit" } }, {} as any);
  assert.equal(out?.block, true); assert.match(out?.reason ?? "", /argument unavailable/);
  assert.deepEqual(requests.slice(1).map((r) => [r.command.type, action(r)]), [
    ["ObserveAction", { kind: "reserve_spawn" }], ["ObserveAction", { kind: "audit_ticket", actor: { model: "claude-auditor", harness: "pi", provider: "pi", source_type: "LLM_JUDGE", attribution: "declared" }, witnessed: false }], ["GetArgument", undefined], ["ObserveAction", { kind: "void_spawn", nonce: "n-1" }],
  ]);
});

test("ticket Block/Fault and GetArgument transport failure refund the exact reservation", async () => {
  for (const mode of ["block", "fault", "argument-throw"] as const) {
    const { gate, requests } = await setup((r) => {
      if (action(r)?.kind === "reserve_spawn") return allow({ run: { spawn: { reservation_id: "reservation-7" } } });
      if (action(r)?.kind === "audit_ticket") {
        if (mode === "block") return block("ticket denied");
        if (mode === "fault") return fault("ticket unavailable");
        return allow({ run: { ticket: { nonce: "n-refund" } } });
      }
      if (r.command.type === "GetArgument") throw new Error("argument transport failed");
      return allow();
    });
    const out = await gate({ toolName: "subagent", toolCallId: mode,
      input: { agent: "empirica:empirica-auditor", model: "provider/auditor" } }, {} as any);
    assert.equal(out?.block, true);
    const refunds = requests.map(action).filter((a) => a?.kind === "void_spawn");
    assert.deepEqual(refunds, mode === "argument-throw"
      ? [{ kind: "void_spawn", nonce: "n-refund" }]
      : [{ kind: "void_spawn", reservation_id: "reservation-7" }]);
  }
});

test("successful auditor launch injects dossier, rubric, nonce and foreground contract without author nonce", async () => {
  const { gate, pi, requests } = await setup((r) => r.command.type === "GetArgument" ? allow({ run: { argument: { text: "DOSSIER claim_digest=abc" } } }) : allow({ run: { ticket: { nonce: "n-2" } } }));
  const input: any = { agent: "empirica:empirica-auditor", task: "audit" };
  await gate({ toolName: "subagent", toolCallId: "x", input }, {} as any);
  assert.match(input.task, /DOSSIER|claim_digest=abc/); assert.match(input.task, /empirica-verdict/); assert.match(input.task, /Your nonce: n-2/); assert.equal(input.async, false);
  assert.ok(pi.modelMessages.every((m) => !m.content.includes("n-2")));
  assert.deepEqual(requests.slice(1).map((r) => r.command.type), ["ObserveAction", "ObserveAction", "GetArgument"]);
});

test("tool_result error dispatches void_spawn", async () => {
  const { gate, result, requests } = await setup((r) => r.command.type === "GetArgument" ? allow({ run: { argument: { text: "dossier" } } }) : allow({ run: { ticket: { nonce: "n-3" } } }));
  await gate({ toolName: "subagent", toolCallId: "x", input: { agent: "empirica:empirica-auditor" } }, {} as any);
  // The exact pi-subagents rejection observed live (dogfood-pi.md P-8): the child never launched.
  const event: any = { toolCallId: "x", isError: true, content: "Structured single-child execution cannot be combined with workflowScript." }; await result(event, {} as any);
  assert.deepEqual(action(requests.at(-1)), { kind: "void_spawn", nonce: "n-3" }); assert.match(event.content, /spawn reservation released/);
});

test("generic child errors keep the spent reservation", async () => {
  const { gate, result, requests } = await setup((r) => r.command.type === "GetArgument"
    ? allow({ run: { argument: { text: "dossier" } } }) : allow({ run: { ticket: { nonce: "n-spent" } } }));
  await gate({ toolName: "subagent", toolCallId: "spent", input: { agent: "empirica:empirica-auditor" } }, {} as any);
  const event: any = { toolCallId: "spent", isError: true, content: "child timed out after launch" };
  await result(event, {} as any);
  assert.equal(requests.filter((r) => action(r)?.kind === "void_spawn").length, 0);
  assert.match(JSON.stringify(event), /audit obligation remains open/);
});

test("missing or mismatched nonce appends open-obligation line and never dispatches verdict", async () => {
  for (const value of ["no block", "```empirica-verdict\n{\"nonce\":\"wrong\"}\n```"]) {
    const { gate, result, requests } = await setup((r) => r.command.type === "GetArgument" ? allow({ run: { argument: { text: "dossier" } } }) : allow({ run: { ticket: { nonce: "n-4" } } }));
    await gate({ toolName: "subagent", toolCallId: "x", input: { agent: "empirica:empirica-auditor" } }, {} as any);
    const event: any = { toolCallId: "x", content: value }; await result(event, {} as any);
    assert.match(JSON.stringify(event.content), /audit obligation remains open/); assert.equal(requests.filter((r) => action(r)?.kind === "audit_verdict").length, 0);
  }
});

test("event.result and malformed/error/transport paths redact before dispatch", async () => {
  for (const mode of ["result", "malformed", "transport"] as const) {
    const { gate, result, requests } = await setup((r) => {
      if (r.command.type === "GetArgument") return allow({ run: { argument: { text: "dossier" } } });
      if (action(r)?.kind === "audit_verdict" && mode === "transport") throw new Error("ingest failed");
      return allow({ run: { ticket: { nonce: "n-secret" } } });
    });
    await gate({ toolName: "subagent", toolCallId: mode, input: { agent: "empirica:empirica-auditor" } }, {} as any);
    const body = mode === "malformed" ? "{bad json n-secret}" : JSON.stringify({ verdict: "pass", nonce: "n-secret", argument_digest: "a", claims_reviewed: [], findings: [] });
    const event: any = { toolCallId: mode, result: "before ```empirica-verdict\n" + body + "\n``` after" };
    if (mode === "transport") await assert.rejects(() => result(event, {} as any), /ingest failed/);
    else await result(event, {} as any);
    assert.doesNotMatch(JSON.stringify(event), /n-secret/);
    if (mode === "result") assert.equal(requests.filter((r) => action(r)?.kind === "audit_verdict").length, 1);
  }
});

test("persisted ticket correlation restores on reload and stale-run results are ignored", async () => {
  const first = await setup((r) => r.command.type === "GetArgument" ? allow({ run: { argument: { text: "dossier" } } }) : allow({ run: { ticket: { nonce: "n-reload" } } }));
  await first.gate({ toolName: "subagent", toolCallId: "reload", input: { agent: "empirica:empirica-auditor" } }, {} as any);
  assert.ok(first.pi.entries.some((entry) => entry.customType === "empirica.ticket"));

  const secondPi = new FakePi(); const reloadedRequests: Req[] = [];
  createEmpiricaExtension({ dispatch: (r: Req) => { reloadedRequests.push(r); return allow() as any; },
    deriveSelector: () => ({ project: "p", session: "s" }) })(secondPi);
  await (secondPi.handlers.get("session_start") as any)({}, { ui: new FakeUi(), sessionManager: { getEntries: () => first.pi.entries } });
  const verdict = JSON.stringify({ verdict: "pass", nonce: "n-reload", argument_digest: "a", claims_reviewed: [], findings: [] });
  const event: any = { toolCallId: "reload", content: `\`\`\`empirica-verdict\n${verdict}\n\`\`\`` };
  await (secondPi.handlers.get("tool_result") as any)(event, {} as any);
  assert.equal(reloadedRequests.filter((r) => action(r)?.kind === "audit_verdict").length, 1);
  assert.doesNotMatch(JSON.stringify(event), /n-reload/);

  const stalePi = new FakePi(); const staleRequests: Req[] = [];
  createEmpiricaExtension({ dispatch: (r: Req) => { staleRequests.push(r); return allow() as any; } })(stalePi);
  const entries = [...first.pi.entries, { customType: "empirica.run", data: { runHandle: "new-run" } }];
  await (stalePi.handlers.get("session_start") as any)({}, { ui: new FakeUi(), sessionManager: { getEntries: () => entries } });
  const staleEvent: any = { toolCallId: "reload", content: `\`\`\`empirica-verdict\n${verdict}\n\`\`\`` };
  await (stalePi.handlers.get("tool_result") as any)(staleEvent, {} as any);
  assert.equal(staleRequests.length, 0);
  assert.doesNotMatch(JSON.stringify(staleEvent), /n-reload/);
});

test("valid block dispatches exactly parsed verdict fields, redacts string and array content, and appends contract", async () => {
  for (const initial of ["prefix\n```empirica-verdict\nBODY\n```", [{ type: "text", text: "prefix\n```empirica-verdict\nBODY\n```" }]]) {
    const { gate, result, requests } = await setup((r) => r.command.type === "GetArgument" ? allow({ run: { argument: { text: "dossier" } } }) : r.command.type === "ObserveAction" && action(r).kind === "audit_verdict" ? block("rejected") : allow({ run: { ticket: { nonce: "n-5" } } }));
    await gate({ toolName: "subagent", toolCallId: "x", input: { agent: "empirica:empirica-auditor" } }, {} as any);
    const verdict = { verdict: "pass", nonce: "n-5", argument_digest: "ad", claims_reviewed: [{ claim_id: "C", claim_digest: "cd", evidence_digest: "ed" }], findings: ["finding"], ts: "now", ignored: "no" };
    const event: any = { toolCallId: "x", content: typeof initial === "string" ? initial.replace("BODY", JSON.stringify(verdict)) : [{ type: "text", text: initial[0].text.replace("BODY", JSON.stringify(verdict)) }] };
    await result(event, {} as any);
    const vr = requests.find((r) => action(r)?.kind === "audit_verdict"); assert.deepEqual(action(vr), { kind: "audit_verdict", ...verdict });
    assert.doesNotMatch(JSON.stringify(event.content), /n-5|argument_digest/); assert.match(JSON.stringify(event.content), /not accepted/);
  }
});
