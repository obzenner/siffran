// EHB-STOP-29…34 (ADR-0068): the Pi turn end is the registry's turn-end table, acted on at `agent_before_settle` only.
// The pure decision (`turnEnd`) is exercised for every state x control mode x chain position, with the expected
// outcome READ from `contracts/empirica/v2/host-boundaries.json`; the registered handler is exercised through the same
// fake dispatch the gate tests use.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { readStoredText } from "./stored.ts";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { PROTOCOL, type Request, type Response, type Result } from "../src/contract.ts";
import { createEmpiricaExtension, DEFAULT_SKILLS_DIR } from "../src/index.ts";
import { PUBLIC_TOOLS } from "../src/public-tools.ts";
import { auditPending, evidenceAskable, fill, turnEnd, type Basis } from "../src/turn-end.ts";
import { humanApprovalWait } from "../src/governance-ui.ts";
import { FakePi, fakeCtx } from "./fakes.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TABLE = JSON.parse(readFileSync(path.resolve(HERE, "..", "..", "..", "..", "..", "contracts", "empirica", "v2",
  "host-boundaries.json"), "utf8")).turn_end as {
  states: Array<{ id: string; outcomes: Record<string, { outcome: string; notices?: string[] }> }>;
};
const NOTICES = PUBLIC_TOOLS.settlement_notices as unknown as Record<string, string>;
const MODES = ["deliberative", "auto-interactive-pre", "auto-interactive-post", "auto-delegated"] as const;
type Mode = (typeof MODES)[number];
const HANDLE = "run-handle-1";
const REASON = "Record research for this claim.";

function governance(mode: Mode, pending = false) {
  const auto = mode !== "deliberative";
  return { state: pending ? "pending" : "approved", control_mode: auto ? "auto" : "deliberative",
    first_approval: mode === "auto-interactive-post" || mode === "auto-delegated", prompt_error: null,
    interactions_remaining: { proposal: 3, total: 128 }, proposal: { budgets: {} },
    context: { ingress: "pi_ui", interactive: mode !== "auto-delegated", delegation: mode === "auto-delegated", author: null },
    // the core's answer (core.governance.human_wait): a pending configuration this phase's human approves
    human_wait: pending && (mode === "deliberative" || mode === "auto-interactive-pre") };
}

function block(mode: Mode, kind: "real" | "budget" | "evidence" | "audit" | "human_wait" = "real"): Result {
  const run: Record<string, unknown> = { id: HANDLE, status: "active", basis: "sha256:0", governance: governance(mode, kind === "human_wait"),
    confirmation_batch: null, children: [] };
  let reason: Record<string, unknown> = { code: "claim.research_missing", message: REASON };
  if (kind === "budget") reason = { code: "budget.exhausted", parameters: { resource: "pass" }, message: "pass budget exhausted." };
  if (kind === "evidence") {
    run.confirmation_batch = { key: "sha256:b", record_ids: [], claim_ids: [], records: [],
      prompt_error: mode === "auto-delegated" ? "unavailable" : null };
    reason = { code: "evidence.confirmation_required", parameters: { route: "human" }, message: "Research waits on a human." };
  }
  if (kind === "audit") {
    run.children = [{ child_id: "ch-1", resource_class: "audit", state: "pending" }];
    reason = { code: "audit.pending", message: "An audit is pending." };
  }
  if (kind === "human_wait") reason = { code: "governance.approval_required", message: "approval required" };
  return { type: "Block", run, reasons: [reason] } as never;
}

const CONVERGED = { type: "Allow", converged: true, run: { id: HANDLE, status: "converged" } } as unknown as Result;
const basis = (n: number): Basis => `sha256:${n}`;

/** The Result for one state of the registry table in one mode, or null where the state cannot arise there. */
function cell(state: string, mode: Mode, notice?: string): Result | null {
  if (state === "converged") return CONVERGED;
  if (state === "async-audit-pending") return block(mode, "audit");
  if (state === "settlement") {
    if (notice === "human_wait") return mode === "deliberative" || mode === "auto-interactive-pre" ? block(mode, "human_wait") : null;
    return block(mode, "budget");
  }
  if (state === "evidence-wait") return block(mode, "evidence");
  return block(mode);
}

test("EHB-STOP-34: every state x control mode x chain position of the registry turn-end table", () => {
  let checked = 0;
  for (const state of TABLE.states) {
    for (const mode of MODES) {
      const expected = state.outcomes[mode];
      for (const name of state.id === "settlement" ? expected.notices! : [expected.notices?.[0]]) {
        const result = cell(state.id, mode, name);
        if (result === null) continue;
        // chain position: no previous continuation, a previous one on an older basis, and one on the same basis
        for (const [label, previous] of [["first", null], ["later", basis(99)], ["stalled", (result as { run: { basis: string } }).run.basis]] as const) {
          const full = turnEnd(result, previous);
          const got = full.kind === "continue" ? { kind: full.kind, message: full.message } : full;  // the basis is the adapter's own
          const reason = ((result as { reasons?: Array<{ message?: string }> }).reasons ?? []).map((r) => r.message).join(" ");
          const where = `${state.id} / ${mode} / ${name} / ${label}`;
          checked += 1;
          if (expected.outcome === "allow" || expected.outcome === "settle") {
            assert.deepEqual(got, { kind: "nothing" }, where);
          } else if (expected.outcome === "settle-notice" || expected.outcome === "pause") {
            const template = NOTICES[name!];
            assert.deepEqual(got, { kind: "notice", notice: fill(template, template.includes("{resource}")
              ? { resource: "pass" } : template.includes("{reason}") ? { reason } : {}) }, where);
          } else {
            assert.equal(expected.outcome, "continue", where);
            const directive = name === "evidence_report";
            const later = fill(NOTICES.unattended_continue, { reason });
            if (label === "first") assert.deepEqual(got, { kind: "continue", message: directive ? NOTICES[name!] : reason }, where);
            else if (label === "later") assert.deepEqual(got, { kind: "continue", message: directive ? NOTICES[name!] : later }, where);
            else assert.deepEqual(got, { kind: "no-progress", notice: later }, where);  // the adapter's own loop bound
          }
        }
      }
    }
  }
  assert.ok(checked > 60, `a table that read as empty would pass vacuously (${checked})`);
});

test("a run that is not active, a fault and an inert answer end the turn with nothing appended", () => {
  const terminal = block("deliberative") as { run: { status: string } };
  terminal.run = { ...terminal.run, status: "stopped_residual" };
  assert.deepEqual(turnEnd(terminal as never, null), { kind: "nothing" });
  assert.deepEqual(turnEnd({ type: "Inert", reason: "no_run" }, null), { kind: "nothing" });
  assert.deepEqual(turnEnd({ type: "Fault", code: "unavailable", fail_direction: "closed" }, null), { kind: "nothing" });
});

test("a blocker that changes is progress", () => {
  const first = turnEnd(block("auto-interactive-post"), null);
  assert.equal(first.kind, "continue");
  const other = block("auto-interactive-post") as { reasons: Array<Record<string, unknown>> };
  (other as unknown as { run: { basis: string } }).run.basis = "sha256:other";  // the core's basis moves with the blocker
  assert.equal(turnEnd(other as never, basisOfFirst(block("auto-interactive-post"))).kind, "continue");
  assert.equal(turnEnd(block("auto-interactive-post"), basisOfFirst(block("auto-interactive-post"))).kind, "no-progress");
});

function basisOfFirst(result: Result): Basis {
  const got = turnEnd(result, null);
  assert.equal(got.kind, "continue");
  return (got as { basis: Basis }).basis;
}

// --- the registered handler ------------------------------------------------------------------------------------

function envelope(result: Result): Response {
  return { protocol: PROTOCOL, request_id: "x", result };
}

async function wired(answer: (revision: number) => Result) {
  let revision = 0;  // a change of what the core projects of the run's work (research recorded, a claim approved)
  const pi = new FakePi();
  const requests: Request[] = [];
  createEmpiricaExtension({
    dispatch: (request) => { requests.push(request); return { ...envelope(request.command.type === "StartRun" ? ({ type: "Allow", converged: false,
      run: { id: HANDLE, status: "active" } } as never) : answer(revision)), request_id: request.request_id }; },
    deriveSelector: () => ({ project: "p", session: "s" }),
    privateIngress: async () => ({ protocol: PROTOCOL, request_id: "trusted-governance",
      result: { type: "Inert", reason: "unsupported_host_event", run: { id: HANDLE, status: "active" } } }) as never,
    ownerEnv: {},
    resolveAuditContract: async () => ({ agentFilePath: path.resolve(DEFAULT_SKILLS_DIR, "..", "agents", "pi", "empirica-auditor.md"),
      model: "bedrock/auditor-model", agentScope: "project", auditRetrieval: ["local", "workspace"] }),
  })(pi);
  await pi.sessionStart();
  await (pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, fakeCtx("/work", [
    { customType: "empirica.run", data: { runHandle: HANDLE } }]));
  const settle = (event: { entries: unknown[]; continue: boolean } = { entries: [], continue: false }) =>
    (pi.handlers.get("agent_before_settle") as (e: unknown, c: unknown) => Promise<{ entries?: unknown[]; continue?: boolean } | undefined>)(
      { type: "agent_before_settle", ...event }, fakeCtx("/work"));
  const toolResult = () => (pi.handlers.get("tool_result") as (e: unknown) => Promise<unknown>)({ toolCallId: "t", toolName: "read", content: [] });
  return { pi, requests, settle, toolResult, change: () => { revision += 1; } };
}

test("EHB-STOP-31: a deliberative turn end appends one visible notice and requests no continuation", async () => {
  const w = await wired(() => block("deliberative"));
  const out = await w.settle();
  assert.equal(out?.continue, undefined);
  assert.deepEqual(out?.entries, [{ type: "custom_message", customType: "empirica.turn_end", display: true,
    content: fill(NOTICES.paused_for_human, { reason: REASON }) }]);
  assert.equal(w.pi.handlers.has("turn_end"), false, "one notice per settle: turn_end is never used");
});

test("EHB-STOP-35: a pending audit appends nothing and requests no continuation, in every mode", async () => {
  for (const mode of MODES) assert.equal(await (await wired(() => block(mode, "audit"))).settle(), undefined, mode);
});

test("EHB-STOP-36: a settlement appends its notice and requests no continuation", async () => {
  const budget = await (await wired(() => block("auto-interactive-post", "budget"))).settle();
  assert.equal(budget?.continue, undefined);
  assert.equal((budget?.entries?.[0] as { content: string }).content, fill(NOTICES.budget_exhausted, { resource: "pass" }));
});

test("EHB-STOP-29: a converged run and no run append nothing", async () => {
  assert.equal(await (await wired(() => CONVERGED)).settle(), undefined);
  const none = new FakePi();
  createEmpiricaExtension({ dispatch: () => { throw new Error("no run: no evaluation"); }, deriveSelector: () => ({ project: "p", session: "s" }),
    privateIngress: async () => ({}) as never, ownerEnv: {}, resolveAuditContract: async () => { throw new Error("n/a"); } })(none);
  assert.equal(await (none.handlers.get("agent_before_settle") as (e: unknown, c: unknown) => Promise<unknown>)({ entries: [], continue: false }, fakeCtx("/work")), undefined);
});

test("G16 H-02: a run that converges at the turn end is retired like an explicit report (tombstone, then read-only)", async () => {
  const w = await wired(() => CONVERGED);
  assert.equal(await w.settle(), undefined);
  assert.equal(w.pi.entries.some((entry) => entry.customType === "empirica.run.done"
    && (entry.data as { runHandle?: string }).runHandle === HANDLE), true, "the journal tombstone is written");
  const before = w.requests.length;
  await w.settle();
  assert.equal(w.requests.length, before, "no further evaluation: the handle is retired");
  const read = await (w.pi.tools.get("empirica_read") as { execute: (...a: unknown[]) => Promise<{ content: Array<{ text: string }> }> })
    .execute("t", { operation: "GetRun" }, undefined, undefined, fakeCtx("/work"));
  assert.doesNotMatch(read.content[0].text, /No active Empirica run/, "a retained handle still answers reads");
});

// --- one rule per question, the same answers as Claude (HQ-G9-08, HQ-G9-11) ----------------------------------------

test("the turn-end predicates give the answers Claude's give on the shared cases", () => {
  const shared = JSON.parse(readFileSync(path.resolve(HERE, "fixtures", "turn-end-predicates.json"), "utf8")) as {
    cases: Array<{ name: string; golden: string; evidence_askable?: boolean; audit_pending?: boolean;
      approval_wait: boolean; foreign_ingress?: boolean;
      patch?: Array<{ path: Array<string | number>; value?: unknown; append?: unknown }> }>;
  };
  assert.ok(shared.cases.length > 20);
  for (const item of shared.cases) {
    const golden = JSON.parse(readStoredText(path.resolve(HERE, "author-view-golden", `${item.golden}.json`), "utf8")).result;
    for (const change of item.patch ?? []) {
      const parents = change.path.slice(0, -1);
      const last = change.path[change.path.length - 1];
      const target = parents.reduce((node: any, key) => node[key], golden);
      if ("append" in change) target[last].push(change.append); else target[last] = change.value;
    }
    assert.equal(evidenceAskable(golden), item.evidence_askable, `${item.name}: evidenceAskable`);
    assert.equal(auditPending(golden), item.audit_pending, `${item.name}: auditPending`);
    // mediated through Pi's own dialog unless the case says another host's was
    const governance = golden.run?.governance;
    if (governance) governance.context.ingress = item.foreign_ingress ? "mcp_elicitation" : "pi_ui";
    assert.equal(humanApprovalWait(golden), item.approval_wait, `${item.name}: humanApprovalWait`);
  }
});

test("HQ-G9-11: a pending audit child without an id is not the audit to wait for (Claude's twin test says the same)", () => {
  const pending = block("deliberative", "audit") as unknown as { run: { children: Array<Record<string, unknown>> } };
  assert.equal(auditPending(pending as never), true);
  pending.run.children[0].child_id = "";
  assert.equal(auditPending(pending as never), false);
});

test("EHB-STOP-32/33: an auto turn end continues once per change of basis and then ends with no-progress", async () => {
  const w = await wired((revision) => {
    const view = block("auto-interactive-post") as unknown as { run: Record<string, unknown> };
    view.run.basis = `sha256:${revision}`;
    return view as never;
  });
  const first = await w.settle();
  assert.equal(first?.continue, true);
  assert.equal((first?.entries?.[0] as { content: string }).content, REASON);
  const stalled = await w.settle();  // the agent answered with text only: no tool result, same blocker
  assert.equal(stalled?.continue, undefined);
  assert.equal((stalled?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_continue, { reason: REASON }));
  assert.deepEqual(w.pi.entries.at(-1), { customType: "empirica.turn_end", data: { outcome: "no-progress" } });
  const afterStall = await w.settle();  // the chain was ended: a later settle may continue once more
  assert.equal(afterStall?.continue, true);
  w.change();  // progress: the run's observation basis changed since the continuation
  const later = await w.settle();
  assert.equal(later?.continue, true);
  assert.equal((later?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_continue, { reason: REASON }));
});

test("INVARIANT (G17b item 2): across turn ends, any run of read-only calls earns at most one continuation; a changed basis earns one more", async () => {
  // Every sequence of up to 6 events between turn ends, each a read-only tool result ("read") or a recorded observation
  // ("record", which moves the core's basis). The oracle: the first turn end continues, a later one continues iff the
  // basis differs from the one at the last continuation.
  const events = ["read", "record"] as const;
  const sequences: Array<Array<(typeof events)[number]>> = [[]];
  for (let length = 1; length <= 6; length += 1)
    for (let n = 0; n < 2 ** length; n += 1) sequences.push(Array.from({ length }, (_, i) => events[(n >> i) & 1]));
  for (const sequence of sequences) {
    const w = await wired((revision) => {
      const view = block("auto-delegated") as unknown as { run: Record<string, unknown> };
      view.run.basis = `sha256:${revision}`;
      return view as never;
    });
    let revision = 0;
    let lastContinued: number | null = null;
    for (const event of sequence) {
      if (event === "read") await w.toolResult(); else { w.change(); revision += 1; }
      const out = await w.settle();
      const expected: boolean = lastContinued === null || lastContinued !== revision;
      assert.equal(out?.continue === true, expected, `${sequence.join(",")}`);
      lastContinued = out?.continue === true ? revision : null;  // a notice ends the chain: the next settle starts a new one
    }
  }
});

test("EHB-STOP-32: interactive auto hands the evidence directive and a delegated run does not", async () => {
  const interactive = await (await wired(() => block("auto-interactive-post", "evidence"))).settle();
  assert.equal(interactive?.continue, true);
  assert.equal((interactive?.entries?.[0] as { content: string }).content, NOTICES.evidence_report);
  const delegated = await (await wired(() => block("auto-delegated", "evidence"))).settle();
  assert.equal((delegated?.entries?.[0] as { content: string }).content, "Research waits on a human.");
});

test("an unreadable run ends the turn silently; the gate at tool_call still holds", async () => {
  const w = await wired(() => { throw new Error("the core went away"); });
  assert.equal(await w.settle(), undefined);
});
