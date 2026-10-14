// The end-of-pass evidence dialog on Pi (ADR-0067): standard ctx.ui.select/input dialogs, nothing custom,
// no dialog without a UI, and nothing granted on cancel, timeout or error.

import { test } from "node:test";
import assert from "node:assert/strict";

import { askItems, confirmEvidence, evidenceConfirmationWait, evidenceHostFailure, itemTitle, observeSurface } from "../src/evidence-ui.ts";
import { PUBLIC_TOOLS } from "../src/public-tools.ts";
import type { EvidenceItem, PrivateIngressRequest } from "../src/private-transport.ts";
import type { ExtensionContext } from "../src/pi-types.ts";
import type { Response } from "../src/contract.ts";

const AUTHOR_LABELS = PUBLIC_TOOLS.author_view.labels as Readonly<Record<string, string>>;

const RECORD: EvidenceItem = { subject: "research", id: "sha256:" + "a".repeat(64), record_ids: ["sha256:" + "a".repeat(64)],
  claims: [{ id: "C0", text: "The API is stable." }, { id: "C2", text: "The API is documented." }],
  source_kind: "web", source_ref: "https://example.test/spec", quote: "stable since v2", class: "retrievable",
  title: "example.test/spec", note_title: "Note (optional)",
  description: 'For C0, C2 · Quote: "stable since v2" · Does the page say this?' };
const OBSERVED: EvidenceItem = { ...RECORD, id: "sha256:" + "b".repeat(64), record_ids: ["sha256:" + "b".repeat(64)],
  claims: [{ id: "C0", text: "The API is stable." }], source_kind: "runtime",
  source_ref: "deploy output", class: "observed", reason: "Needs production credentials.",
  title: "deploy output", note_title: "Note (optional)",
  description: 'For C0 · Quote: "stable since v2" · Is the observation right?' };
const DECISION: EvidenceItem = { subject: "claim", id: "C1", digest: "sha256:" + "c".repeat(64), text: "Ship it?",
  title: "Your decision on C1", note_title: "Note (optional)", description: "C1 · Ship it?" };

const BATCH = (promptError: string | null = null) => ({ key: "sha256:" + "d".repeat(64), record_ids: [RECORD.id],
  claim_ids: [], prompt_error: promptError });
const blocked = (route = "human", promptError: string | null = route === "human" ? null : "unavailable",
                 code = "evidence.confirmation_required"): Response => ({
  protocol: "empirica/v2", request_id: "r",
  result: { type: "Block", run: { id: "run-1", status: "active", confirmation_batch: BATCH(promptError) }, reasons: [{
    code, message: "m", sections: [], next_actions: [],
    parameters: { route, record_ids: [RECORD.id] } }] } } as unknown as Response);
const refreshed: Response = { protocol: "empirica/v2", request_id: "r2",
  result: { type: "Allow", converged: false, run: { id: "run-1", status: "active" } } } as unknown as Response;

interface Script { selects: Array<string | undefined>; inputs: Array<string | undefined> }

function ctxFor(script: Script, options: { hasUI?: boolean; select?: boolean; input?: boolean; custom?: boolean } = {}) {
  const shown: Array<{ kind: string; title: string; options?: string[]; opts?: unknown }> = [];
  const ui: Record<string, unknown> = { notify() {} };
  if (options.select !== false) ui.select = async (title: string, choices: string[], opts?: unknown) => {
    shown.push({ kind: "select", title, options: choices, opts }); return script.selects.shift(); };
  if (options.input !== false) ui.input = async (title: string, _p?: string, opts?: unknown) => {
    shown.push({ kind: "input", title, opts }); return script.inputs.shift(); };
  ui.custom = async () => { throw new Error("the evidence dialog must not use ctx.ui.custom"); };
  return { ctx: { ui, hasUI: options.hasUI ?? true, mode: "tui" } as unknown as ExtensionContext, shown };
}

function bridge(items: EvidenceItem[], receipt: unknown = { type: "evidence_receipt", result: { type: "Allow" } }) {
  const calls: PrivateIngressRequest[] = [];
  const trusted = async (request: PrivateIngressRequest) => {
    calls.push(request);
    return request.operation === "evidence_presentation"
      ? { type: "evidence_presentation", receipt_id: "p-1", items } : receipt;
  };
  return { trusted, calls };
}

const confirm = (ctx: ExtensionContext, trusted: (r: PrivateIngressRequest) => Promise<unknown>, response = blocked(),
                 deadline = Date.now() + 60_000) =>
  confirmEvidence(response, "run-1", ctx, trusted as never, async () => refreshed, deadline);
const run = async (...args: Parameters<typeof confirm>) => (await confirm(...args)).response;

test("TUI: each item gets a select and, for a decision, a note; the answers are one receipt", async () => {
  // the first input is for the confirmed record: an empty note is simply no note
  const script = { selects: ["Confirm", "Reject", "Approve"], inputs: ["", "Wrong system.", "Ship it."] as Array<string | undefined> };
  const live = ctxFor(script);
  const { trusted, calls } = bridge([RECORD, OBSERVED, DECISION]);
  assert.equal(await run(live.ctx, trusted), refreshed);
  assert.deepEqual(calls.map((c) => c.operation), ["evidence_presentation", "evidence_confirmation"]);
  assert.deepEqual(calls[0].payload, {}, "the core's batch is presented; the host names nothing");
  const sent = calls[1].payload as { receipt_id: string; answers: unknown[] };
  assert.equal(sent.receipt_id, "p-1");
  assert.deepEqual(sent.answers, [
    { subject: "research", id: RECORD.id, choice: "confirm", note: null },
    { subject: "research", id: OBSERVED.id, choice: "reject", note: "Wrong system." },
    { subject: "claim", id: "C1", choice: "approve", note: "Ship it." }]);
  assert.deepEqual(live.shown.filter((s) => s.kind === "select").map((s) => s.options),
    [["Confirm", "Reject", "Later"], ["Confirm", "Reject", "Later"], ["Approve", "Reject", "Later"]]);
});

test("the dialog shows the source, the verbatim quote, the claim and the reason no spike could decide it", () => {
  const title = itemTitle(OBSERVED, 2, 3);
  for (const text of ["2 of 3", "runtime source: deploy output", 'Quote: "stable since v2"', "For claim C0: The API is stable.",
    "Why no deterministic check: Needs production credentials."]) assert.ok(title.includes(text), text);
  const grouped = itemTitle(RECORD, 1, 1);
  assert.ok(grouped.includes("For claim C0: The API is stable.") && grouped.includes("For claim C2: The API is documented."),
    "one source quoted for two claims is one item that lists both");
  assert.ok(itemTitle(DECISION, 1, 1).includes("Ship it?"));
  const long = itemTitle({ ...RECORD, quote: `${"x".repeat(900)} THE QUALIFICATION AT THE END` }, 1, 1);
  assert.ok(long.includes("THE QUALIFICATION AT THE END") && !long.includes("…"), "the human reads the whole quote");
});

test("F-Q4-2: the dialog names its subject on the first line, the note dialog names it too, and the end has its own heading", async () => {
  const live = ctxFor({ selects: ["Confirm", "Approve"], inputs: ["", ""] });
  const { trusted } = bridge([RECORD, DECISION]);
  await run(live.ctx, trusted);
  const [first, second] = live.shown.filter((s) => s.kind === "select");
  assert.equal(first.title.split("\n")[0], "Empirica · evidence that needs you (1 of 2)");
  assert.equal(first.title.split("\n")[1], "example.test/spec");
  assert.equal(first.title.split("\n")[2], 'For C0, C2 · Quote: "stable since v2" · Does the page say this?',
    "the description is the third line: the same text Claude shows under the field");
  assert.equal(second.title.split("\n")[1], "Your decision on C1");
  assert.equal(second.title.split("\n")[2], "C1 · Ship it?");
  assert.deepEqual(live.shown.filter((s) => s.kind === "input").map((s) => s.title), ["Note (optional)", "Note (optional)"]);
  const ending = itemTitle(TERMINAL_ITEM, 1, 1).split("\n");
  assert.equal(ending[0], "Empirica · end this run without converging?");
  assert.equal(ending[1], "End the run as stopped_residual?");
  assert.equal(ending[2], TERMINAL_ITEM.subject === "claim" ? TERMINAL_ITEM.description : "");
  assert.ok(!ending.join("\n").includes("evidence that needs you"));
});

test("RPC-like: standard select/input work without a custom component", async () => {
  const { ctx } = ctxFor({ selects: ["Confirm"], inputs: ["fine"] });
  const { trusted, calls } = bridge([RECORD]);
  assert.equal(await run(ctx, trusted), refreshed);
  assert.equal((calls[1].payload as { answers: Array<{ note: string }> }).answers[0].note, "fine");
});

test("print and JSON (no UI), or a UI without select/input: nothing is presented, nothing spent", async () => {
  for (const options of [{ hasUI: false }, { select: false }, { input: false }]) {
    const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] }, options);
    const { trusted, calls } = bridge([RECORD]);
    const original = blocked();
    assert.equal(await run(ctx, trusted, original), original, JSON.stringify(options));
    assert.deepEqual(calls, [], JSON.stringify(options));
  }
});

test("G14, U4: a batch left on Later is shown again by an explicit report, under the same prompt errors as the Python mediator (core.confirmation.REPRESENTABLE), until the core's ceiling says otherwise", async () => {
  for (const [promptError, asks] of [[null, 1], ["already_presented", 1], ["interaction_limit", 0], ["unavailable", 0], ["unobserved", 0]] as const) {
    const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] });
    const { trusted, calls } = bridge([RECORD]);
    const result = await run(ctx, trusted, blocked("human", promptError));
    assert.equal(calls.filter((call) => call.operation === "evidence_presentation").length, asks, String(promptError));
    assert.equal(result === refreshed, asks === 1, String(promptError));
  }
});

test("nothing is presented without a batch the core says can be shown; another blocker does not stop it", async () => {
  const noBatch = { ...blocked(), result: { ...blocked().result, run: { id: "run-1", status: "active", confirmation_batch: null } } } as unknown as Response;
  for (const response of [blocked("unavailable"), blocked("human", "interaction_limit"),
    noBatch, refreshed]) {
    const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] });
    const { trusted, calls } = bridge([RECORD]);
    assert.equal(await run(ctx, trusted, response), response);
    assert.deepEqual(calls, []);
  }
  // An open claim leads the reason, and the human is still asked.
  const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] });
  const { trusted, calls } = bridge([RECORD]);
  assert.equal(await run(ctx, trusted, blocked("human", null, "claim.research_missing")), refreshed);
  assert.deepEqual(calls.map((c) => c.operation), ["evidence_presentation", "evidence_confirmation"]);
});

test("cancelling any dialog, or running past the deadline, submits nothing and the batch is evaluated again", async () => {
  const cases: Array<[string, Script, number]> = [
    ["cancel first select", { selects: [undefined], inputs: [] }, 60_000],
    ["cancel second select", { selects: ["Confirm", undefined], inputs: [""] }, 60_000],
    ["cancel the note", { selects: ["Confirm"], inputs: [undefined] }, 60_000],
    ["a label that was not offered", { selects: ["Approve"], inputs: [""] }, 60_000],
    ["deadline already past", { selects: ["Confirm"], inputs: [""] }, -1]];
  for (const [label, script, ms] of cases) {
    const { ctx } = ctxFor(script);
    const { trusted, calls } = bridge([RECORD, OBSERVED]);
    assert.equal(await run(ctx, trusted, blocked(), Date.now() + ms), refreshed, label);  // the shown batch is spent
    assert.ok(!calls.some((c) => c.operation === "evidence_confirmation"), label);
  }
});

test("the dialogs are given the time that is left, so a stuck dialog times out", async () => {
  const { ctx, shown } = ctxFor({ selects: ["Later"], inputs: [] });
  const deadline = Date.now() + 5_000;
  assert.deepEqual(await askItems([RECORD], ctx, deadline), [{ subject: "research", id: RECORD.id, choice: "later", note: null }]);
  const timeout = (shown[0].opts as { timeout: number }).timeout;
  assert.ok(timeout > 0 && timeout <= 5_000, String(timeout));
});

test("Later asks for no note, and an all-Later batch submits no receipt at all", async () => {
  const { ctx, shown } = ctxFor({ selects: ["Later", "Later"], inputs: [] });
  const { trusted, calls } = bridge([RECORD, OBSERVED]);
  assert.equal(await run(ctx, trusted, blocked()), refreshed);
  assert.equal(shown.filter((s) => s.kind === "input").length, 0);
  assert.deepEqual(calls.map((c) => c.operation), ["evidence_presentation"]);
});

test("a refused presentation or an error is evaluated again; a refused receipt is evaluated again as shown", async () => {
  const original = blocked();
  const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] });
  const refused = async () => ({ type: "evidence_refused", result: { type: "Fault" } });
  assert.equal(await run(ctx, refused, original), refreshed);
  const notAllowed = bridge([RECORD], { type: "evidence_receipt", result: { type: "Fault" } });
  assert.equal(await run(ctxFor({ selects: ["Confirm"], inputs: [""] }).ctx, notAllowed.trusted, original), refreshed);
  const throwing = async () => { throw new Error("bridge down"); };
  assert.equal(await run(ctxFor({ selects: ["Confirm"], inputs: [""] }).ctx, throwing, original), refreshed);
  // ...and when even the evaluation fails there is nothing fresher than the response in hand.
  const down = (ctxFor({ selects: ["Confirm"], inputs: [""] }).ctx);
  assert.equal((await confirmEvidence(original, "run-1", down, throwing as never, async () => { throw new Error("down"); },
    Date.now() + 60_000)).response, original);
});

test("the wait notice applies only to the sole, unspent, human-answerable evidence blocker of an active run", () => {
  assert.equal(evidenceConfirmationWait(blocked().result), true);
  assert.equal(evidenceConfirmationWait(blocked("unavailable").result), false);
  for (const error of ["already_presented", "interaction_limit"])
    assert.equal(evidenceConfirmationWait(blocked("human", error).result), false, error);  // nothing will ask: block
  assert.equal(evidenceConfirmationWait(blocked("human", null, "claim.research_missing").result), false);  // the open claim leads
  const terminal = blocked(); (terminal.result as { run: { status: string } }).run.status = "converged";
  assert.equal(evidenceConfirmationWait(terminal.result), false);
  const mixed = blocked(); (mixed.result as { reasons: unknown[] }).reasons.push({ code: "run.corrupt", parameters: {} });
  assert.equal(evidenceConfirmationWait(mixed.result), false);
});

test("the host tells the core whether it can show a dialog, at every report", async () => {
  const sent: unknown[] = [];
  const trusted = async (request: PrivateIngressRequest) => { sent.push(request.payload); return {}; };
  for (const [options, surface] of [[{}, "human"], [{ hasUI: false }, "unavailable"], [{ select: false }, "unavailable"]] as const)
    await observeSurface("run-1", ctxFor({ selects: [], inputs: [] }, options).ctx, trusted as never);
  assert.deepEqual(sent, [{ surface: "human" }, { surface: "unavailable" }, { surface: "unavailable" }]);
  await observeSurface("run-1", ctxFor({ selects: [], inputs: [] }).ctx, (async () => { throw new Error("down"); }) as never);
});

test("a surface that could not be recorded as present is recorded as unavailable (fail closed)", async () => {
  const sent: string[] = [];
  const failsForHuman = async (request: PrivateIngressRequest) => {
    const surface = (request.payload as { surface: string }).surface;
    sent.push(surface);
    if (surface === "human") throw new Error("store unavailable");
    return {};
  };
  await observeSurface("run-1", ctxFor({ selects: [], inputs: [] }).ctx, failsForHuman as never);
  assert.deepEqual(sent, ["human", "unavailable"]);
  // a host that never had a surface says unavailable once and does not retry; a recorded human is not followed by a second call
  sent.length = 0;
  await observeSurface("run-1", ctxFor({ selects: [], inputs: [] }, { hasUI: false }).ctx, failsForHuman as never);
  await observeSurface("run-1", ctxFor({ selects: [], inputs: [] }).ctx, (async (r: PrivateIngressRequest) => { sent.push("ok:" + (r.payload as { surface: string }).surface); return {}; }) as never);
  assert.deepEqual(sent, ["unavailable", "ok:human"]);
});

test("observeSurface says whether the core holds the observation: a typed fault or a throw is a failure", async () => {
  const ctx = ctxFor({ selects: [], inputs: [] }).ctx;
  const reply = (value: unknown) => (async () => value) as never;
  assert.equal(await observeSurface("run-1", ctx, reply({ type: "Allow" })), true);
  assert.equal(await observeSurface("run-1", ctx, reply({ result: { type: "Allow" } })), true);
  assert.equal(await observeSurface("run-1", ctx, reply({ result: { type: "Fault", code: "unavailable" } })), false);
  assert.equal(await observeSurface("run-1", ctx, reply({ type: "Fault", code: "unavailable" })), false);
  assert.equal(await observeSurface("run-1", ctx, (async () => { throw new Error("down"); }) as never), false);
  // no UI: one attempt as unavailable, no second
  const sent: string[] = [];
  const noUi = ctxFor({ selects: [], inputs: [] }, { hasUI: false }).ctx;
  assert.equal(await observeSurface("run-1", noUi, (async (r: PrivateIngressRequest) => { sent.push((r.payload as { surface: string }).surface); throw new Error("x"); }) as never), false);
  assert.deepEqual(sent, ["unavailable"]);
});

test("a presentation that faults, throws or is refused is evaluated again as presentation_failed and shows nothing", async () => {
  const refusals: Array<[string, () => Promise<unknown>]> = [
    ["typed fault", async () => ({ type: "Fault", code: "conflict", fail_direction: "closed" })],
    ["thrown", async () => { throw new Error("the core went away"); }],
    ["not a presentation", async () => ({ type: "Block" })],
    ["no items", async () => ({ type: "evidence_presentation", receipt_id: "p-1" })]];
  for (const [label, answer] of refusals) {
    const { ctx, shown } = ctxFor({ selects: ["Confirm"], inputs: [""] });
    const failures: unknown[] = [];
    const out = (await confirmEvidence(blocked(), "run-1", ctx, answer as never, async (failure) => {
      failures.push(failure); return refreshed; }, Date.now() + 60_000));
    assert.equal(out.response, refreshed, label);
    assert.equal(out.failure, "presentation_failed", label);
    assert.deepEqual(failures, ["presentation_failed"], label);
    assert.deepEqual(shown, [], label);
  }
});

test("a batch that was shown is evaluated again without a failure, whatever the human did (the no-UI case is PI-04)", async () => {
  for (const selects of [["Confirm"], ["Later"], [undefined]]) {
    const { ctx } = ctxFor({ selects, inputs: [""] });
    const failures: unknown[] = [];
    await confirmEvidence(blocked(), "run-1", ctx, bridge([RECORD]).trusted as never, async (failure) => {
      failures.push(failure); return refreshed; }, Date.now() + 60_000);
    assert.deepEqual(failures, [undefined], String(selects));
  }
});

test("PI-04: a presentable batch with no dialog to show it in reports presentation_failed, spending and evaluating nothing", async () => {
  for (const options of [{ hasUI: false }, { select: false }, { input: false }]) {
    const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] }, options);
    const { trusted, calls } = bridge([RECORD]);
    const original = blocked();
    const out = await confirm(ctx, trusted, original);
    assert.equal(out.response, original, JSON.stringify(options));
    assert.equal(out.failure, "presentation_failed", JSON.stringify(options));
    assert.deepEqual(calls, [], JSON.stringify(options));
  }
  const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] }, { hasUI: false });
  const quiet = await confirm(ctx, bridge([RECORD]).trusted, blocked("unavailable"));
  assert.equal(quiet.failure, undefined, "no presentable batch: nothing failed to be asked");
});

test("the gate's notice for a host failure is the sole confirmation blocker's, in the registry's words, with the stop next", () => {
  for (const reason of ["surface_unrecorded", "presentation_failed"] as const) {
    const notice = evidenceHostFailure(blocked("human", reason).result);
    assert.equal(notice, `${AUTHOR_LABELS[`waiting_${reason}`]}\nnext: report_convergence intent=stop`);
    assert.ok(String(notice).includes("nobody was asked"));
    assert.equal(evidenceConfirmationWait(blocked("human", reason).result), false, "and it is never a wait");
    assert.equal(evidenceHostFailure(blocked("human", reason, "claim.research_missing").result), null, "another blocker leads");
  }
  for (const error of [null, "unavailable", "unobserved", "already_presented", "interaction_limit"])
    assert.equal(evidenceHostFailure(blocked("human", error).result), null, String(error));
});

// ---- the end of a deliberative run (ADR-0063 amendment, EHB-TERM-01/02): the same dialog, one item ----------------

const TERMINAL = { status: "stopped_residual", digest: "sha256:" + "e".repeat(64) } as const;
const TERMINAL_ITEM: EvidenceItem = { subject: "claim", id: TERMINAL.status, digest: TERMINAL.digest, terminal: true,
  text: "End this run as stopped_residual, without converging?", title: "End the run as stopped_residual?",
  note_title: "Note (optional)",
  description: "Not converged. Approve ends the run now as stopped_residual; Reject or Later keeps it active." };
const terminalBlock = (): Response => ({
  protocol: "empirica/v2", request_id: "r",
  result: { type: "Block", run: { id: "run-1", status: "active", confirmation_batch: {
    key: "sha256:" + "f".repeat(64), record_ids: [], claim_ids: [TERMINAL.status], terminal: TERMINAL, records: [], prompt_error: null } },
    reasons: [{ code: "governance.terminal_stop_pending", message: "m", sections: [], next_actions: [],
      parameters: { status: TERMINAL.status, route: "human" } }] } } as unknown as Response);

test("EHB-TERM-01: the terminal batch travels with the presentation, and the human's approval is one receipt", async () => {
  const live = ctxFor({ selects: ["Approve"], inputs: [""] });
  const { trusted, calls } = bridge([TERMINAL_ITEM]);
  assert.equal(await run(live.ctx, trusted, terminalBlock()), refreshed);
  assert.deepEqual(calls.map((c) => c.operation), ["evidence_presentation", "evidence_confirmation"]);
  assert.deepEqual((calls[0].payload as { batch: { terminal: unknown } }).batch.terminal, TERMINAL,
    "the evaluation that reached the end asked it: no later read of the run shows it, so the batch is handed over");
  assert.deepEqual((calls[1].payload as { answers: unknown[] }).answers,
    [{ subject: "claim", id: TERMINAL.status, choice: "approve", note: null }]);
});

test("EHB-TERM-02: reject, later, cancel and timeout submit nothing that grants the end", async () => {
  for (const [label, selects] of [["reject", ["Reject"]], ["later", ["Later"]], ["cancel", [undefined]]] as const) {
    const live = ctxFor({ selects: [...selects], inputs: [""] });
    const { trusted, calls } = bridge([TERMINAL_ITEM]);
    await run(live.ctx, trusted, terminalBlock());
    const granted = calls.filter((c) => c.operation === "evidence_confirmation")
      .flatMap((c) => (c.payload as { answers: Array<{ choice: string }> }).answers).some((a) => a.choice === "approve");
    assert.equal(granted, false, label);
  }
  const expired = ctxFor({ selects: ["Approve"], inputs: [""] });
  const { trusted, calls } = bridge([TERMINAL_ITEM]);
  await run(expired.ctx, trusted, terminalBlock(), Date.now() - 1);
  assert.equal(calls.some((c) => c.operation === "evidence_confirmation"), false, "an expired deadline asks nothing and submits nothing");
});

test("EHB-TERM-03: without a UI the end is not asked and the failure is the usual one", async () => {
  const noUi = ctxFor({ selects: [], inputs: [] }, { hasUI: false });
  const { trusted, calls } = bridge([TERMINAL_ITEM]);
  const confirmed = await confirm(noUi.ctx, trusted, terminalBlock());
  assert.equal(calls.length, 0);
  assert.equal(confirmed.failure, "presentation_failed");
});
