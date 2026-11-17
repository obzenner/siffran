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
import { auditPending, CONTINUATION_K, CONTINUATION_T, evidenceAskable, fill, turnEnd, type Basis, type Memory } from "../src/turn-end.ts";
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
/** What the core says in auto when a claim rests only on sources that need a human (`claim.research_missing`, `support: unconfirmed`). */
const UNCONFIRMED = "Claim C0 rests only on research that a human would have to confirm, and an auto run asks no one, so none of it counts.";

function governance(mode: Mode, pending = false) {
  const auto = mode !== "deliberative";
  return { state: pending ? "pending" : "approved", control_mode: auto ? "auto" : "deliberative",
    first_approval: mode === "auto-interactive-post" || mode === "auto-delegated", prompt_error: null,
    interactions_remaining: { proposal: 3, total: 128 }, proposal: { budgets: {} },
    context: { ingress: "pi_ui", interactive: mode !== "auto-delegated", delegation: mode === "auto-delegated", author: null },
    // the core's answer (core.governance.human_wait): a pending configuration this phase's human approves
    // (after its first approval, interactive auto asks a human only about a budget raise, so a pending view there is that dialog)
    human_wait: pending && mode !== "auto-delegated" };
}

/** Who the turn end waits on, as the core projects it for each kind of view (`core.evaluation.turn_end_actor`). */
const ACTORS: Record<string, "author" | "auditor" | "human" | "none"> = { human_wait: "human", audit: "auditor", no_author_step: "none" };
/** What the core says in every mode when the root claim is refuted: no step of the author's approves it. */
const REFUTED = "Claim C0 is refuted by the research recorded for it.";

function block(mode: Mode, kind: "real" | "budget" | "evidence" | "audit" | "human_wait" | "no_author_step" = "real"): Result {
  const run: Record<string, unknown> = { id: HANDLE, status: "active", basis: "sha256:0",
    turn_end_actor: ACTORS[kind] ?? (kind === "evidence" && mode === "deliberative" ? "human" : "author"),
    spike_backend: { kind: "spikes_unavailable" }, governance: governance(mode, kind === "human_wait"),
    confirmation_batch: null, children: [] };
  let reason: Record<string, unknown> = { code: "claim.research_missing", message: REASON };
  if (kind === "budget") reason = { code: "budget.exhausted", parameters: { resource: "pass" }, message: "pass budget exhausted." };
  if (kind === "evidence" && mode !== "deliberative") {
    // an auto run asks no one: the claim's own reason, with no batch and no human reason
    reason = { code: "claim.research_missing", parameters: { claim_id: "C0", support: "unconfirmed" }, message: UNCONFIRMED };
  } else if (kind === "evidence") {
    run.confirmation_batch = { key: "sha256:b", record_ids: [], claim_ids: [], records: [], prompt_error: null };
    reason = { code: "evidence.confirmation_required", parameters: { route: "human" }, message: "Research waits on a human." };
  }
  if (kind === "audit") {
    run.children = [{ child_id: "ch-1", resource_class: "audit", state: "pending" }];
    reason = { code: "audit.pending", message: "An audit is pending." };
  }
  if (kind === "human_wait") reason = { code: "governance.approval_required", message: "approval required" };
  if (kind === "no_author_step") reason = { code: "claim.refuted", parameters: { claim_id: "C0" }, message: REFUTED };
  return { type: "Block", run, reasons: [reason] } as never;
}

const CONVERGED = { type: "Allow", converged: true,
  run: { id: HANDLE, status: "converged", spike_backend: { kind: "spikes_unavailable" } } } as unknown as Result;
const basis = (n: number): Basis => `sha256:${n}`;
/** What the adapter remembers after `count` continuations on `at` (and `total` in the chain). */
const memory = (at: Basis, count = 1, total = count): Memory => ({ basis: at, count, total });
const runBasis = (result: Result): Basis => (result as { run: { basis: string } }).run.basis;

/** The Result for one state of the registry table in one mode, or null where the state cannot arise there. */
function cell(state: string, mode: Mode, outcome: string): Result | null {
  if (outcome === "unreachable") return null;
  if (state === "converged") return CONVERGED;
  if (state === "async-audit-pending") return block(mode, "audit");
  if (state === "human-wait") return block(mode, "human_wait");
  if (state === "budget-exhausted") return block(mode, "budget");
  if (state === "budget-raise-offered") {
    const view = block(mode, "budget") as unknown as { reasons: Array<Record<string, unknown>> };
    view.reasons[0].next_actions = ["budget.raise", "residual.accept"];
    return view as never;
  }
  if (state === "evidence-wait") return block(mode, "evidence");
  if (state === "no-author-step") return block(mode, "no_author_step");
  return block(mode);
}

test("EHB-STOP-34: every state x control mode x chain position of the registry turn-end table", () => {
  let checked = 0;
  for (const state of TABLE.states) {
    for (const mode of MODES) {
      const expected = state.outcomes[mode];
      const name = expected.notices?.[0];
      const result = cell(state.id, mode, expected.outcome);
      if (result === null) continue;
      // chain position: nothing remembered; a continuation on an older basis; the same basis once (count 1); and the same
      // basis after K - 1 continuations, where the next one would be the K-th
      const positions = [["first", null], ["later", memory(basis(99))], ["repeat", memory(runBasis(result))],
        ["stalled", memory(runBasis(result), CONTINUATION_K - 1)]] as const;
      for (const [label, previous] of positions) {
        const full = turnEnd(result, previous);
        const got = full.kind === "continue" ? { kind: full.kind, message: full.message } : full;  // the memory is the adapter's own
        const reason = ((result as { reasons?: Array<{ message?: string }> }).reasons ?? []).map((r) => r.message).join(" ");
        const where = `${state.id} / ${mode} / ${name} / ${label}`;
        checked += 1;
        if (expected.outcome === "allow" || expected.outcome === "settle") {
          assert.deepEqual(got, { kind: "nothing" }, where);
        } else if (expected.outcome === "settle-notice" || expected.outcome === "pause") {
          const template = NOTICES[name!];
          assert.deepEqual(got, { kind: "notice", notice: fill(template, template.includes("{resource}")
            ? { resource: "pass" } : template.includes("{reason}") ? { reason } : {}) }, where);
        } else if (expected.outcome === "continue-with-notice") {
          // the cell's notice is the directive on every turn end of the chain, once per basis: the same basis again ends with
          // the way out, and the K-th turn end on a basis ends with the residual, as for any continuation
          if (label === "stalled") assert.deepEqual(got, { kind: "no-progress", notice: fill(NOTICES.unattended_residual, { reason }) }, where);
          else if (label === "repeat") assert.deepEqual(got, { kind: "no-progress", notice: fill(NOTICES.unattended_continue, { reason }) }, where);
          else assert.deepEqual(got, { kind: "continue", message: fill(NOTICES[name!], { reason }) }, where);
        } else {
          assert.equal(expected.outcome, "continue", where);
          const later = fill(NOTICES.unattended_continue, { reason });
          if (label === "first") assert.deepEqual(got, { kind: "continue", message: reason }, where);
          else if (label === "stalled") assert.deepEqual(got, { kind: "no-progress", notice: fill(NOTICES.unattended_residual, { reason }) }, where);
          else assert.deepEqual(got, { kind: "continue", message: later }, where);
        }
      }
    }
  }
  assert.ok(checked > 60, `a table that read as empty would pass vacuously (${checked})`);
});

test("A7: an auto run whose author has no step left continues with the residual as its directive; deliberative still pauses", () => {
  const directive = fill(NOTICES.unattended_residual, { reason: REFUTED });
  for (const mode of ["auto-interactive-pre", "auto-interactive-post", "auto-delegated"] as const) {
    for (const previous of [null, memory(basis(99))])  // the first turn end of a chain, and a later one on a changed basis
      assert.deepEqual(turnEnd(block(mode, "no_author_step"), previous),
        { kind: "continue", message: directive, memory: { basis: "sha256:0", count: 1, total: (previous?.total ?? 0) + 1 } },
        `${mode} / ${JSON.stringify(previous)}`);
    // bounded like every auto continuation: the same basis again ends the turn with the way out
    assert.deepEqual(turnEnd(block(mode, "no_author_step"), memory("sha256:0")),
      { kind: "no-progress", notice: fill(NOTICES.unattended_continue, { reason: REFUTED }) }, mode);
  }
  assert.match(directive, /report_convergence intent=stop/);
  assert.match(directive, /revise the claim graph/);
  assert.doesNotMatch(directive, /hand the turn back/);
  assert.deepEqual(turnEnd(block("deliberative", "no_author_step"), null),
    { kind: "notice", notice: fill(NOTICES.paused_for_human, { reason: REFUTED }) });
});

test("A9: the none continuation is counted, and the bound check comes ahead of its directive", () => {
  const residual = { kind: "no-progress", notice: fill(NOTICES.unattended_residual, { reason: REFUTED }) };
  for (const mode of ["auto-interactive-pre", "auto-interactive-post", "auto-delegated"] as const) {
    const view = block(mode, "no_author_step");
    // a none continuation adds one to the total, exactly like an author's
    const got = turnEnd(view, memory(basis(7), 1, 5));
    assert.equal(got.kind === "continue" ? got.memory.total : null, 6, mode);
    // the T-th turn end of a chain ends it, whatever the basis did and whoever acts
    assert.deepEqual(turnEnd(view, memory(basis(7), 1, CONTINUATION_T - 1)), residual, `${mode} / T`);
    assert.equal(turnEnd(view, memory(basis(7), 1, CONTINUATION_T - 2)).kind, "continue", `${mode} / T - 1`);
    // the K-th on one basis ends with the residual (not the way out); the second on one basis, short of K, ends with the way out
    assert.deepEqual(turnEnd(view, memory("sha256:0", CONTINUATION_K - 1)), residual, `${mode} / K`);
    assert.deepEqual(turnEnd(view, memory("sha256:0", 1)),
      { kind: "no-progress", notice: fill(NOTICES.unattended_continue, { reason: REFUTED }) }, `${mode} / once per basis`);
  }
});

test("A7: a none actor reaches the agent through the handler: it continues once, and the same basis again settles", async () => {
  const w = await wired(() => block("auto-delegated", "no_author_step"));
  const first = await w.settle();
  assert.equal(first?.continue, true);
  assert.equal((first?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_residual, { reason: REFUTED }));
  const stalled = await w.settle();  // nothing moved
  assert.equal(stalled?.continue, undefined);
  assert.equal((stalled?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_continue, { reason: REFUTED }));
  assert.deepEqual(w.pi.entries.at(-1), { customType: "empirica.turn_end", data: { outcome: "no-progress" } });
});

test("A7: the four actors in auto: the author and none continue, the auditor and a human settle", () => {
  for (const mode of ["auto-interactive-pre", "auto-interactive-post", "auto-delegated"] as const) {
    assert.equal(turnEnd(block(mode), null).kind, "continue", `${mode}: author`);
    assert.deepEqual(turnEnd(block(mode, "audit"), null), { kind: "nothing" }, `${mode}: auditor`);
    assert.equal(turnEnd(block(mode, "no_author_step"), null).kind, "continue", `${mode}: none`);
  }
  for (const mode of ["auto-interactive-pre", "auto-interactive-post"] as const)
    assert.deepEqual(turnEnd(block(mode, "human_wait"), null), { kind: "notice", notice: NOTICES.human_wait }, `${mode}: human`);
});

test("F4: a view without a turn_end_actor takes the path it took before the field and is never the residual or the author's step", () => {
  // The schema requires the field; a host that meets a view without it (or with a value outside the enum) does not repair it:
  // it behaves as it did before the field existed. Only the literal `none` hands an auto turn the residual directive.
  for (const absent of [undefined, "someone", 7, null])
    for (const mode of MODES) for (const kind of ["real", "no_author_step"] as const) for (const previous of [null, memory(basis(99)), memory("sha256:0")]) {
      const view = block(mode, kind) as unknown as { run: Record<string, unknown> };
      if (absent === undefined) delete view.run.turn_end_actor; else view.run.turn_end_actor = absent;
      const reason = kind === "real" ? REASON : REFUTED;
      const got = turnEnd(view as never, previous);
      const where = `${String(absent)} / ${mode} / ${kind} / ${previous}`;
      if (mode === "deliberative") assert.deepEqual(got, { kind: "notice", notice: fill(NOTICES.paused_for_human, { reason }) }, where);
      else if (previous === null) assert.deepEqual(got, { kind: "continue", message: reason, memory: memory("sha256:0") }, where);
      else assert.deepEqual(got, { kind: "continue", message: fill(NOTICES.unattended_continue, { reason }),
        memory: previous.basis === "sha256:0" ? memory("sha256:0", 2) : memory("sha256:0", 1, 2) }, where);
    }
});

test("A7: an auto run with a stale evidence batch is handed the core's reason and never a directive to report", () => {
  // The core gives an auto run no batch and no human reason; a view that says otherwise (built by hand) still gets the reason.
  for (const mode of ["auto-interactive-pre", "auto-interactive-post", "auto-delegated"] as const) {
    const view = block("deliberative", "evidence") as unknown as { run: Record<string, unknown> };
    view.run.governance = governance(mode);
    view.run.turn_end_actor = "author";
    assert.equal(evidenceAskable(view as never), true, "the predicate still reads the batch (deliberative uses it)");
    assert.deepEqual(turnEnd(view as never, null), { kind: "continue", message: "Research waits on a human.", memory: memory("sha256:0") }, mode);
  }
});

test("A7: the notices the host reads are the contract's: the tools file lists exactly the contract's settlement notices", () => {
  const contract = JSON.parse(readFileSync(path.resolve(HERE, "..", "..", "..", "..", "..", "contracts", "empirica", "v2",
    "public-contract.json"), "utf8")).settlement_notices as Record<string, string>;
  assert.deepEqual(Object.keys(PUBLIC_TOOLS.settlement_notices).sort(), Object.keys(contract).sort());
  assert.equal(PUBLIC_TOOLS.settlement_notices.unattended_residual, contract.unattended_residual);
});

test("a run that is not active, a fault and an inert answer end the turn with nothing appended", () => {
  const terminal = block("deliberative") as { run: { status: string } };
  terminal.run = { ...terminal.run, status: "stopped_residual" };
  assert.deepEqual(turnEnd(terminal as never, null), { kind: "nothing" });
  assert.deepEqual(turnEnd({ type: "Inert", reason: "no_run" }, null), { kind: "nothing" });
  assert.deepEqual(turnEnd({ type: "Fault", code: "unavailable", fail_direction: "closed" }, null), { kind: "nothing" });
});

test("a blocker that changes is progress: the count starts again at 1", () => {
  const first = turnEnd(block("auto-interactive-post"), null);
  assert.equal(first.kind, "continue");
  const other = block("auto-interactive-post") as { reasons: Array<Record<string, unknown>> };
  (other as unknown as { run: { basis: string } }).run.basis = "sha256:other";  // the core's basis moves with the blocker
  assert.deepEqual((turnEnd(other as never, memoryOfFirst(block("auto-interactive-post"))) as { memory: Memory }).memory,
    memory("sha256:other", 1, 2));
  assert.equal(turnEnd(block("auto-interactive-post"), memoryOfFirst(block("auto-interactive-post"))).kind, "continue");  // the 2nd on one basis
});

function memoryOfFirst(result: Result): Memory {
  const got = turnEnd(result, null);
  assert.equal(got.kind, "continue");
  return (got as { memory: Memory }).memory;
}

/** Drive `turnEnd` as the adapter does: remember what a continuation says, forget on anything else. */
function chain(views: Array<() => Result>): Array<ReturnType<typeof turnEnd>> {
  let remembered: Memory | null = null;
  return views.map((view) => {
    const action = turnEnd(view(), remembered);
    remembered = action.kind === "continue" ? action.memory : null;
    return action;
  });
}

test("A9: K and T are the registry's: one place in the adapter, read from the registry's `turn_end.bounds`", () => {
  const { bounds } = JSON.parse(readFileSync(path.resolve(HERE, "..", "..", "..", "..", "..", "contracts", "empirica", "v2",
    "host-boundaries.json"), "utf8")).turn_end as { bounds: { K: number; T: number; definition: string; enforced_by: string[] } };
  assert.deepEqual([CONTINUATION_K, CONTINUATION_T], [bounds.K, bounds.T]);
  assert.deepEqual([CONTINUATION_K, CONTINUATION_T], [3, 16]);
  // the definition states the same numbers in the same words the adapter implements: the K-th turn end on a basis ends the chain
  assert.equal(Number(bounds.definition.match(/\bK = (\d+)/)?.[1]), CONTINUATION_K);
  assert.equal(Number(bounds.definition.match(/\bT = (\d+)/)?.[1]), CONTINUATION_T);
  assert.match(bounds.definition, /K-th turn end of a chain on one observation basis, or the T-th turn end in a chain, ends the chain/);
  assert.ok(bounds.enforced_by.includes("pi"));
});

test("A9: on an unchanged basis continuations 1 and 2 continue and the third end is the residual", () => {
  const same = () => block("auto-interactive-post");
  const [one, two, three, fresh] = chain([same, same, same, same]);
  assert.deepEqual(one, { kind: "continue", message: REASON, memory: memory("sha256:0", 1, 1) });
  assert.deepEqual(two, { kind: "continue", message: fill(NOTICES.unattended_continue, { reason: REASON }), memory: memory("sha256:0", 2, 2) });
  assert.deepEqual(three, { kind: "no-progress", notice: fill(NOTICES.unattended_residual, { reason: REASON }) });
  assert.match((three as { notice: string }).notice, /report_convergence intent=stop/);
  assert.deepEqual(fresh, one, "the residual ended the chain: the next one starts at 1");
});

test("A9: a changed basis resets the count, and the chain still ends at T with the residual", () => {
  const moving = (n: number) => () => { const v = block("auto-delegated") as unknown as { run: Record<string, unknown> }; v.run.basis = basis(n); return v as never; };
  const out = chain(Array.from({ length: CONTINUATION_T + 3 }, (_, i) => moving(i)));
  for (let i = 0; i < CONTINUATION_T - 1; i += 1) assert.equal(out[i].kind, "continue", `continuation ${i + 1}`);  // past K = 3 by changing basis
  assert.deepEqual(out[CONTINUATION_T - 1], { kind: "no-progress", notice: fill(NOTICES.unattended_residual, { reason: REASON }) });
  assert.equal((out[CONTINUATION_T - 2] as { memory: Memory }).memory.total, CONTINUATION_T - 1);
  assert.equal((out[CONTINUATION_T - 2] as { memory: Memory }).memory.count, 1);
  assert.equal(out[CONTINUATION_T].kind, "continue", "after the residual a new chain starts");
  assert.equal((out[CONTINUATION_T] as { memory: Memory }).memory.total, 1);
});

test("A9: a changed basis between repeats holds the count below K", () => {
  const at = (n: number) => () => { const v = block("auto-interactive-pre") as unknown as { run: Record<string, unknown> }; v.run.basis = basis(n); return v as never; };
  const out = chain([at(1), at(1), at(2), at(2), at(3), at(3), at(4)]);  // never three on one basis
  assert.deepEqual(out.map((a) => a.kind), Array(7).fill("continue"));
  assert.deepEqual((out[3] as { memory: Memory }).memory, memory(basis(2), 2, 4));
});

test("A9: a memory from 7.0.x (a bare basis) and an unreadable one read as a changed basis with zeroed counts", () => {
  const view = block("auto-interactive-post");
  for (const old of ["sha256:0", "", "sha256:other", 7, true, [], {}, { basis: 5, count: 1, total: 1 },
    { basis: "sha256:0", count: "2", total: 2 }, { basis: "sha256:0", count: -1, total: 1 }, { basis: "sha256:0", count: 1.5, total: 2 },
    { basis: "sha256:0", count: Number.NaN, total: 2 }, { basis: "sha256:0", count: 1 }, undefined] as unknown[])
    assert.deepEqual(turnEnd(view, old as never), { kind: "continue", message: REASON, memory: memory("sha256:0") }, JSON.stringify(old));
  // a readable memory is not mistaken for old: the same basis counts on
  assert.deepEqual(turnEnd(view, memory("sha256:0", 1, 1)),
    { kind: "continue", message: fill(NOTICES.unattended_continue, { reason: REASON }), memory: memory("sha256:0", 2, 2) });
});

test("A9: the deliberative run and the human and auditor actors do not depend on the counts", () => {
  // (the none actor is counted like any continuation: see "the none continuation is counted")
  const spent = memory("sha256:0", CONTINUATION_K - 1, CONTINUATION_T - 1);
  for (const previous of [null, spent, "sha256:0"] as unknown[]) {
    assert.deepEqual(turnEnd(block("deliberative"), previous as never), { kind: "notice", notice: fill(NOTICES.paused_for_human, { reason: REASON }) });
    for (const mode of ["auto-interactive-pre", "auto-interactive-post"] as const)
      assert.deepEqual(turnEnd(block(mode, "human_wait"), previous as never), { kind: "notice", notice: NOTICES.human_wait }, mode);
    for (const mode of MODES) assert.deepEqual(turnEnd(block(mode, "audit"), previous as never), { kind: "nothing" }, mode);
  }
});

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
      run: { id: HANDLE, status: "active", spike_backend: { kind: "spikes_unavailable" } } } as never) : answer(revision)), request_id: request.request_id }; },
    deriveSelector: () => ({ project: "p", session: "s" }),
    privateIngress: async () => ({ protocol: PROTOCOL, request_id: "trusted-governance",
      result: { type: "Inert", reason: "unsupported_host_event",
        run: { id: HANDLE, status: "active", spike_backend: { kind: "spikes_unavailable" } } } }) as never,
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

test("A11d: an exhausted budget the core offers to raise continues interactive auto and settles deliberative", async () => {
  // The core lists budget.raise only where a raise can be proposed: its human approves it (interactive auto), or the
  // deliberative human decides. The turn goes on only where the agent is the one to propose it.
  const offered = (mode: Mode) => {
    const view = block(mode, "budget") as unknown as { reasons: Array<Record<string, unknown>> };
    view.reasons[0].next_actions = ["budget.raise", "residual.accept"];
    return view as never;
  };
  const continued = await (await wired(() => offered("auto-interactive-post"))).settle();
  assert.equal(continued?.continue, true);
  assert.equal((continued?.entries?.[0] as { content: string }).content, "pass budget exhausted.");
  const settled = await (await wired(() => offered("deliberative"))).settle();
  assert.equal(settled?.continue, undefined);
  assert.equal((settled?.entries?.[0] as { content: string }).content, fill(NOTICES.budget_exhausted, { resource: "pass" }));
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

test("EHB-STOP-32/33: an auto turn end continues twice on one basis, then ends with the residual and no-progress", async () => {
  const w = await wired((revision) => {
    const view = block("auto-interactive-post") as unknown as { run: Record<string, unknown> };
    view.run.basis = `sha256:${revision}`;
    return view as never;
  });
  const first = await w.settle();
  assert.equal(first?.continue, true);
  assert.equal((first?.entries?.[0] as { content: string }).content, REASON);
  const second = await w.settle();  // the agent answered with text only: no tool result, same blocker; the 2nd continuation
  assert.equal(second?.continue, true);
  assert.equal((second?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_continue, { reason: REASON }));
  const stalled = await w.settle();  // the 3rd would be the K-th on this basis
  assert.equal(stalled?.continue, undefined);
  assert.equal((stalled?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_residual, { reason: REASON }));
  assert.deepEqual(w.pi.entries.at(-1), { customType: "empirica.turn_end", data: { outcome: "no-progress" } });
  const afterStall = await w.settle();  // the chain was ended: the memory is cleared and the next chain starts at 1
  assert.equal(afterStall?.continue, true);
  assert.equal((afterStall?.entries?.[0] as { content: string }).content, REASON);
  w.change();  // progress: the run's observation basis changed since the continuation
  const later = await w.settle();
  assert.equal(later?.continue, true);
  assert.equal((later?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_continue, { reason: REASON }));
});

test("A9: any non-continue outcome clears the memory, so the next chain starts at 1", async () => {
  let kind: "real" | "budget" | "audit" = "real";
  const w = await wired(() => block("auto-interactive-post", kind));
  assert.equal((await w.settle())?.continue, true);   // 1
  assert.equal((await w.settle())?.continue, true);   // 2
  kind = "audit";                                      // a pending audit ends the chain with nothing appended
  assert.equal(await w.settle(), undefined);
  kind = "real";
  const fresh = await w.settle();                      // would have been the residual had the memory survived
  assert.equal(fresh?.continue, true);
  assert.equal((fresh?.entries?.[0] as { content: string }).content, REASON);
  assert.equal((await w.settle())?.continue, true);   // 2
  kind = "budget";                                     // a settlement notice clears it too
  assert.equal((await w.settle())?.continue, undefined);
  kind = "real";
  assert.equal((await w.settle())?.continue, true);   // 1
  assert.equal((await w.settle())?.continue, true);   // 2
  assert.equal((await w.settle())?.continue, undefined);  // the residual
});

/** A wired extension whose view the test changes between turn ends, with the host events a chain can cross. */
async function chained(view: () => Result) {
  let current = view;
  const w = await wired(() => current());
  const event = (name: string, payload: unknown = {}) =>
    (w.pi.handlers.get(name) as (e: unknown, c: unknown) => Promise<unknown>)(payload, fakeCtx("/work"));
  const restore = async (handle: string) => {  // a session start that restores `handle` as the active run
    await (w.pi.handlers.get("session_start") as (e: unknown, c: unknown) => unknown)({}, fakeCtx("/work", [
      { customType: "empirica.run", data: { runHandle: handle } }]));
  };
  const report = () => (w.pi.tools.get("report_convergence") as { execute: (...a: unknown[]) => Promise<unknown> })
    .execute("t-report", {}, undefined, undefined, fakeCtx("/work"));
  return { ...w, show: (next: () => Result) => { current = next; }, event, restore, report };
}
const real = () => block("auto-interactive-post");
const reasonOf = (out: { entries?: unknown[] } | undefined) => (out?.entries?.[0] as { content: string }).content;
const WAY_OUT = fill(NOTICES.unattended_continue, { reason: REASON });

test("A9 F4: a dispatch that throws during a chain clears the memory, so the next chain starts at 1", async () => {
  const w = await chained(real);
  assert.equal(reasonOf(await w.settle()), REASON);       // 1
  assert.equal(reasonOf(await w.settle()), WAY_OUT);      // 2
  w.show(() => { throw new Error("the core went away"); });
  assert.equal(await w.settle(), undefined);              // the throw ends the chain
  w.show(real);
  assert.equal(reasonOf(await w.settle()), REASON);       // a fresh chain: 1 (the memory would have made this the residual)
  assert.equal(reasonOf(await w.settle()), WAY_OUT);      // 2
});

test("A9 F2: the memory belongs to one run: another run in the same session starts its chain at 1", async () => {
  const w = await chained(real);
  assert.equal(reasonOf(await w.settle()), REASON);       // 1 on HANDLE
  await w.restore("run-handle-2");                         // the run handle changes (a new run, or a restored one)
  assert.equal(reasonOf(await w.settle()), REASON);       // 1 on the new run, not 2
  assert.equal(reasonOf(await w.settle()), WAY_OUT);      // 2
});

test("A9 F2: a run that ends between continuations leaves no memory for the next run", async () => {
  const w = await chained(real);
  assert.equal(reasonOf(await w.settle()), REASON);       // 1
  assert.equal(reasonOf(await w.settle()), WAY_OUT);      // 2: the next turn end on this basis would be the K-th
  w.show(() => CONVERGED);
  await w.report();                                        // the run ends outside a turn end
  assert.equal(await w.settle(), undefined);               // no active run: nothing appended
  w.show(real);
  await w.restore(HANDLE);                                 // a run with the same handle is active again
  assert.equal(reasonOf(await w.settle()), REASON);       // 1, not the residual
});

test("A9 F2: a new prompt starts a new chain: the host's `before_agent_start` clears the memory, a continuation does not", async () => {
  const w = await chained(real);
  assert.equal(reasonOf(await w.settle()), REASON);       // 1
  assert.equal(reasonOf(await w.settle()), WAY_OUT);      // 2
  await w.event("agent_start");                            // a continuation starts a run too, and is the same chain
  assert.equal((await w.settle())?.continue, undefined);  // the 3rd is the residual
  assert.equal(reasonOf(await w.settle()), REASON);       // 1 (the residual ended that chain)
  assert.equal(reasonOf(await w.settle()), WAY_OUT);      // 2
  await w.event("before_agent_start", { type: "before_agent_start", messageType: "user", prompt: "go on" });
  assert.equal(reasonOf(await w.settle()), REASON);       // 1: the person's prompt started a new chain
});

test("A9: a chain whose basis keeps changing ends at T with the residual, through the handler", async () => {
  const w = await wired((revision) => {
    const view = block("auto-delegated") as unknown as { run: Record<string, unknown> };
    view.run.basis = `sha256:${revision}`;
    return view as never;
  });
  for (let i = 1; i < CONTINUATION_T; i += 1) { w.change(); assert.equal((await w.settle())?.continue, true, `continuation ${i}`); }
  w.change();
  const last = await w.settle();
  assert.equal(last?.continue, undefined);
  assert.equal((last?.entries?.[0] as { content: string }).content, fill(NOTICES.unattended_residual, { reason: REASON }));
  assert.deepEqual(w.pi.entries.at(-1), { customType: "empirica.turn_end", data: { outcome: "no-progress" } });
});

test("INVARIANT (G17b item 2): across turn ends, read-only calls earn continuations only up to K on one basis, and T in a chain; a changed basis restarts the count", async () => {
  // Every sequence of up to 6 events between turn ends, each a read-only tool result ("read") or a recorded observation
  // ("record", which moves the core's basis). The oracle: a turn end continues iff it would be neither the K-th
  // continuation on one basis nor the T-th in the chain; any other outcome clears the memory.
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
    let count = 0;
    let total = 0;
    for (const event of sequence) {
      if (event === "read") await w.toolResult(); else { w.change(); revision += 1; }
      const out = await w.settle();
      const nextCount: number = lastContinued === revision ? count + 1 : 1;
      const expected: boolean = nextCount < CONTINUATION_K && total + 1 < CONTINUATION_T;
      assert.equal(out?.continue === true, expected, `${sequence.join(",")}`);
      if (expected) { lastContinued = revision; count = nextCount; total += 1; } else { lastContinued = null; count = 0; total = 0; }  // a notice ends the chain
    }
  }
});

test("EHB-STOP-32: an auto run asks no one: every auto mode is handed the core's reason, never the evidence directive", async () => {
  for (const mode of ["auto-interactive-pre", "auto-interactive-post", "auto-delegated"] as const) {
    const out = await (await wired(() => block(mode, "evidence"))).settle();
    assert.equal(out?.continue, true, mode);
    assert.equal((out?.entries?.[0] as { content: string }).content, UNCONFIRMED, mode);
  }
});

test("an unreadable run ends the turn silently; the gate at tool_call still holds", async () => {
  const w = await wired(() => { throw new Error("the core went away"); });
  assert.equal(await w.settle(), undefined);
});
