// The end-of-pass evidence dialog on Pi (ADR-0067): standard ctx.ui.select/input dialogs, nothing custom,
// no dialog without a UI, and nothing granted on cancel, timeout or error.

import { test } from "node:test";
import assert from "node:assert/strict";

import { askItems, confirmEvidence, evidenceConfirmationWait, evidenceHostFailure, itemTitle, KEEP_COMPLETED,
  notePrompt, observeSurface } from "../src/evidence-ui.ts";
import { PUBLIC_TOOLS } from "../src/public-tools.ts";
import type { EvidenceAnswer, EvidenceDocument, EvidenceItem, PrivateIngressRequest } from "../src/private-transport.ts";
import type { ExtensionContext } from "../src/pi-types.ts";
import type { Response } from "../src/contract.ts";

const AUTHOR_LABELS = PUBLIC_TOOLS.author_view.labels as Readonly<Record<string, string>>;

const RECORD: EvidenceItem = { subject: "research", id: "sha256:" + "a".repeat(64), record_ids: ["sha256:" + "a".repeat(64)],
  claims: [{ id: "C0", text: "The API is stable." }, { id: "C2", text: "The API is documented." }],
  source_kind: "web", source_ref: "https://example.test/spec", quote: "stable since v2", class: "retrievable",
  title: "example.test/spec", note_title: "Note (optional)",
  description: 'C0, C2 · Does the page say this?' };
const OBSERVED: EvidenceItem = { ...RECORD, id: "sha256:" + "b".repeat(64), record_ids: ["sha256:" + "b".repeat(64)],
  claims: [{ id: "C0", text: "The API is stable." }], source_kind: "runtime",
  source_ref: "deploy output", class: "observed", reason: "Needs production credentials.",
  title: "deploy output", note_title: "Note (optional)",
  description: 'C0 · Is the observation right?' };
const DECISION: EvidenceItem = { subject: "claim", id: "C1", digest: "sha256:" + "c".repeat(64), text: "Ship it?",
  title: "Your decision on C1", note_title: "Note (optional)", description: "C1 · Ship it?" };

const BATCH = (promptError: string | null = null) => ({ key: "sha256:" + "d".repeat(64), record_ids: [RECORD.id],
  claim_ids: [], prompt_error: promptError });
// PR 12: the presentation answer names the written document — the same bound identity the Python
// bridge hands back (the full digest, the file's absolute path, and a terminal batch's residual count);
// never its text. The short line the dialog renders is the digest's first 12 hex.
// PR 16 (A10/A12): the fixture document also carries the link fields the Python flow computes
// once per presentation — `url: null` is the honest fallback most fixtures need (no live
// observer), and `LINKED` below reuses the shared golden fixture's Details constants
// (plugins/empirica/tests/fixtures/governance-dialog-golden.json `details`), so the URL a test
// dialog shows is derived from one fixture constant, as the round-09 goldens derive the path.
import { readFileSync } from "node:fs";
const GOLDEN = JSON.parse(readFileSync(new URL("../../../tests/fixtures/governance-dialog-golden.json",
  import.meta.url), "utf8")) as { details: { run12: string; url: string } };
const LINKED: EvidenceDocument = {
  digest: "sha256:0a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789",
  path: "/Users/amelia.dvn/empirica-projects/p-3f9c2a81b7d4/r-5e0a91c88f2b7e/gen-1/hitl/"
    + "0a1b2c3d4e5f6789abcdef0123456789abcdef0123456789abcdef0123456789.json",
  url: GOLDEN.details.url, run12: GOLDEN.details.run12 };
const DOCUMENT: EvidenceDocument = { ...LINKED, url: null };
const SHORT_DIGEST_LINE = `Document ${DOCUMENT.digest.slice(0, 19)}`;
/** The exact path a title's broken pieces encode: the `File: ` label opens the first piece line. */
const pathOfTitle = (title: string): string =>
  title.split("\n").filter(line => line === "File: " || /^File: \/|^\//.test(line))
    .map(line => line.replace(/^File: /, "")).join("");
const POINTER = "Claim texts and their no-deterministic-check reasons: in the document.";
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

function bridge(items: EvidenceItem[], receipt: unknown = { type: "evidence_receipt", result: { type: "Allow" } },
               document: unknown = DOCUMENT) {
  const calls: PrivateIngressRequest[] = [];
  const trusted = async (request: PrivateIngressRequest) => {
    calls.push(request);
    return request.operation === "evidence_presentation"
      ? (document === NO_DOCUMENT ? { type: "evidence_presentation", receipt_id: "p-1", items }
        : { type: "evidence_presentation", receipt_id: "p-1", items, document }) : receipt;
  };
  return { trusted, calls };
}

/** The "member absent" sentinel: a default parameter would swallow an explicit `undefined`. */
const NO_DOCUMENT = Symbol("no document");

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
  const sent = calls[1].payload as { receipt_id: string; answers: unknown[]; document_digest?: string };
  assert.equal(sent.receipt_id, "p-1");
  assert.equal(sent.document_digest, DOCUMENT.digest,
    "PR 12: the confirmation repeats the presentation's digest, so the receipt names the document the human read");
  assert.deepEqual(sent.answers, [
    { subject: "research", id: RECORD.id, choice: "confirm", note: null },
    { subject: "research", id: OBSERVED.id, choice: "reject", note: "Wrong system." },
    { subject: "claim", id: "C1", choice: "approve", note: "Ship it." }]);
  assert.deepEqual(live.shown.filter((s) => s.kind === "select").map((s) => s.options),
    [["Confirm", "Reject", "Later"], ["Confirm", "Reject", "Later"], ["Approve", "Reject", "Later"]]);
});

test("PR 12: the research dialog keeps the source and the quote; the claim context and the reason are in the document", () => {
  const title = itemTitle(OBSERVED, 2, 3, DOCUMENT);
  for (const text of ["2 of 3", "runtime source: deploy output", 'Quote: "stable since v2"', POINTER,
    SHORT_DIGEST_LINE]) assert.ok(title.includes(text), text);
  for (const gone of ["For claim C0: The API is stable.", "Why no deterministic check: Needs production credentials."])
    assert.ok(!title.includes(gone), `${gone} moved to the document`);
  const grouped = itemTitle(RECORD, 1, 1, DOCUMENT);
  assert.ok(!grouped.includes("For claim C0:") && !grouped.includes("For claim C2:"),
    "one source quoted for two claims is one item; both claims' texts are in the document");
  const long = itemTitle({ ...RECORD, quote: `${"x".repeat(900)} THE QUALIFICATION AT THE END` }, 1, 1, DOCUMENT);
  assert.ok(long.includes("THE QUALIFICATION AT THE END") && !long.includes("…"), "the human reads the whole quote");
  assert.ok(long.endsWith(SHORT_DIGEST_LINE),
    "the dialog ends with the short digest: ui.select's countdown attaches to the LAST line (A12)");
  assert.equal(pathOfTitle(long), DOCUMENT.path,
    "the fallback path is broken at `/` and nothing else: the pieces rebuild the exact record");
  const longDetails = long.slice(long.indexOf("Details: ")).split("\n");
  assert.ok(longDetails.every(line => line.length <= 76),
    "the composed Details lines fit A12's 76 columns (the item's own quote stays full, never wrapped)");
  // PR 16 (A11): with a live observer the dialog shows the deep link and no path at all
  const linked = itemTitle(OBSERVED, 1, 1, LINKED);
  assert.ok(linked.includes(`Details: ${GOLDEN.details.url}`), "the link is one whole line");
  const linkedDetails = linked.slice(linked.indexOf("Details: ")).split("\n");
  assert.ok(linkedDetails.every(line => line.length <= 76),
    "the URL line (about 64 characters with a five-digit port) fits the 76-column bound");
  assert.ok(!pathOfTitle(linked), "a verified link drops the path entirely (O5)");
  assert.ok(linked.endsWith(SHORT_DIGEST_LINE), "the LAST line stays the short digest (A12)");
});

test("PR 12: the decision dialog shows the full claim text — what is decided — and the terminal dialog the residual count", () => {
  // The claim text IS what the human decides, exactly like a research item's quote: it stays in full,
  // on both hosts (the Claude message shows it too — adapters/confirmation.py `_item_text`).
  const decision = itemTitle(DECISION, 1, 1, DOCUMENT).split("\n");
  assert.ok(decision.includes("Ship it?"), "the claim text is shown in full");
  assert.ok(decision.includes(SHORT_DIGEST_LINE)
    && pathOfTitle(decision.join("\n")) === DOCUMENT.path,
    "every item dialog ends with the document's Details and short digest, the digest last");
  assert.ok(!decision.some((line) => line.includes("no-deterministic-check")),
    "nothing of a decision item moved; the pointer belongs to the research items whose context did");
  const terminal = itemTitle(TERMINAL_ITEM, 1, 1, { ...DOCUMENT, residuals: 4 }).split("\n");
  const terminalItem = TERMINAL_ITEM as Extract<EvidenceItem, { subject: "claim" }>;
  assert.equal(terminal[0], "Empirica · end this run without converging?");
  assert.ok(terminal.some((line) => line === terminalItem.text), "the decision wording stays in full");
  assert.ok(terminal.some((line) => line === "4 unresolved obligations would remain — they are listed in the document."),
    "the count read out of the written document");
  assert.ok(terminal.includes(SHORT_DIGEST_LINE)
    && pathOfTitle(terminal.join("\n")) === DOCUMENT.path);
  assert.ok(!terminal.some((line) => line.includes("no-deterministic-check")), "the terminal dialog has no research pointer");
  const none = itemTitle(TERMINAL_ITEM, 1, 1, { ...DOCUMENT, residuals: 0 });
  assert.ok(none.includes("Nothing would stay unresolved; the record is in the document."));
  assert.ok(!none.includes("unresolved obligations"), "a zero count is worded, never \"0 obligations\"");
  const unknown = itemTitle(TERMINAL_ITEM, 1, 1, DOCUMENT);
  assert.ok(!unknown.includes("unresolved "), "no count was read: no sentence is invented");
});

test("F-Q4-2: the dialog names its subject on the first line, the note dialog names it too, and the end has its own heading", async () => {
  const live = ctxFor({ selects: ["Confirm", "Approve"], inputs: ["", ""] });
  const { trusted } = bridge([RECORD, DECISION]);
  await run(live.ctx, trusted);
  const [first, second] = live.shown.filter((s) => s.kind === "select");
  assert.equal(first.title.split("\n")[0], "Empirica · evidence that needs you (1 of 2)");
  assert.equal(first.title.split("\n")[1], "example.test/spec");
  assert.equal(first.title.split("\n")[2], "C0, C2 · Does the page say this?",
    "the description is the third line: the same text Claude shows under the field — and PR 16 (A12) keeps the quote out of it, shown once as `Quote: …`");
  assert.equal(second.title.split("\n")[1], "Your decision on C1");
  assert.equal(second.title.split("\n")[2], "C1 · Ship it?");
  assert.deepEqual(live.shown.filter((s) => s.kind === "input").map((s) => s.title),
    [notePrompt("Note (optional)"), notePrompt("Note (optional)")],
    "PR 18 (A4): the note prompt says what Enter finishes and that Esc goes back (composed in TS, not in the shared NOTE_TITLE)");
  const ending = itemTitle(TERMINAL_ITEM, 1, 1, { ...DOCUMENT, residuals: 3 }).split("\n");
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

test("PR 18 (§1): a cancel, a wrong label or a past deadline at the FIRST item submits nothing — there is no earlier answer to keep", async () => {
  const cases: Array<[string, Script, number]> = [
    ["cancel the first select", { selects: [undefined], inputs: [] }, 60_000],
    ["a label that was not offered", { selects: ["Approve"], inputs: [""] }, 60_000],
    ["deadline already past", { selects: ["Confirm"], inputs: [""] }, -1]];
  for (const [label, script, ms] of cases) {
    const { ctx } = ctxFor(script);
    const { trusted, calls } = bridge([RECORD, OBSERVED]);
    assert.equal(await run(ctx, trusted, blocked(), Date.now() + ms), refreshed, label);  // the shown batch is spent
    assert.ok(!calls.some((c) => c.operation === "evidence_confirmation"), label);
  }
});

test("PR 18 (§1, §4-1): Esc at item 2's select keeps item 1's answer — item 2 and the rest are sent as Later", async () => {
  const script = { selects: ["Approve", undefined] as Array<string | undefined>, inputs: ["Ship it."] as Array<string | undefined> };
  const live = ctxFor(script);
  const { trusted, calls } = bridge([DECISION, OBSERVED]);
  assert.equal(await run(live.ctx, trusted), refreshed);
  assert.deepEqual(calls.map((c) => c.operation), ["evidence_presentation", "evidence_confirmation"],
    "the completed answer is submitted once: one presentation, one confirmation (A5d)");
  assert.deepEqual((calls[1].payload as { answers: unknown[] }).answers, [
    { subject: "claim", id: "C1", choice: "approve", note: "Ship it." },
    { subject: "research", id: OBSERVED.id, choice: "later", note: null }]);
  const second = live.shown.filter((s) => s.kind === "select")[1]!.title;
  const lines = second.split("\n");
  assert.ok(lines.includes(KEEP_COMPLETED), "the select says what Esc does before the human presses it (A4)");
  assert.ok(lines.indexOf(KEEP_COMPLETED) < lines.findIndex((line) => line.startsWith("Details: ")),
    "the promise line comes before the Details lines");
  assert.equal(lines[lines.length - 1], SHORT_DIGEST_LINE, "the Document sha256 line stays last");
});

test("PR 18 (A3, §4-2, the Lima sequence): Esc at the optional note goes back to the same item's choice", async () => {
  // The qualification's own case: Approve item 1, a stray Esc at the note, and the careful answer must not be lost.
  // The select is shown again under the same deadline; Approve again, Enter on the empty note (§4-6: an empty note
  // submitted with Enter stays the choice with no note), then item 2 answered normally.
  const live = ctxFor({ selects: ["Approve", "Approve", "Confirm"], inputs: [undefined, "", "fine"] });
  const { trusted, calls } = bridge([DECISION, RECORD]);
  assert.equal(await run(live.ctx, trusted), refreshed);
  assert.deepEqual(calls.map((c) => c.operation), ["evidence_presentation", "evidence_confirmation"],
    "one presentation, one confirmation: the go-back reshow submits nothing by itself (A5d)");
  assert.deepEqual((calls[1].payload as { answers: unknown[] }).answers, [
    { subject: "claim", id: "C1", choice: "approve", note: null },
    { subject: "research", id: RECORD.id, choice: "confirm", note: "fine" }]);
  const selects = live.shown.filter((s) => s.kind === "select");
  assert.equal(selects.length, 3, "item 1: select, note Esc, select again; then item 2's select");
  assert.equal(selects[0]!.title, selects[1]!.title,
    "the reshowed select is the same item's dialog, under the same deadline");
  assert.ok(!selects[1]!.title.includes(KEEP_COMPLETED),
    "item 1 of 2 has no earlier answer: no promise line (§4-7a)");
  assert.ok(selects[2]!.title.includes(KEEP_COMPLETED),
    "item 2 of 2 is asked after a completed answer (§4-7a)");
});

test("PR 18 (§4-3): Esc at the asked-again select stops there; earlier answers are kept, the rest are Later", async () => {
  const live = ctxFor({ selects: ["Confirm", "Confirm", undefined], inputs: ["one.", undefined] });
  const { trusted, calls } = bridge([RECORD, OBSERVED, DECISION]);
  assert.equal(await run(live.ctx, trusted), refreshed);
  assert.deepEqual(calls.map((c) => c.operation), ["evidence_presentation", "evidence_confirmation"]);
  assert.deepEqual((calls[1].payload as { answers: unknown[] }).answers, [
    { subject: "research", id: RECORD.id, choice: "confirm", note: "one." },
    { subject: "research", id: OBSERVED.id, choice: "later", note: null },
    { subject: "claim", id: "C1", choice: "later", note: null }]);
});

test("PR 18 (§4-4): a timeout at item 2's select keeps the earlier answer", async () => {
  const { ctx, shown } = ctxFor({ selects: ["Confirm"], inputs: [""] });
  const realSelect = ctx.ui.select!.bind(ctx.ui) as
    (title: string, options: string[], opts?: { timeout?: number }) => Promise<string | undefined>;
  let asked = 0;
  ctx.ui.select = async (title: string, options: string[], opts?: { timeout?: number }) => {
    if (++asked > 1) { await new Promise((resolve) => setTimeout(resolve, 60)); return undefined; }
    return realSelect(title, options, opts);
  };
  const { trusted, calls } = bridge([RECORD, OBSERVED]);
  const deadline = Date.now() + 30;
  assert.equal(await run(ctx, trusted, blocked(), deadline), refreshed);
  assert.deepEqual((calls[1].payload as { answers: unknown[] }).answers, [
    { subject: "research", id: RECORD.id, choice: "confirm", note: null },
    { subject: "research", id: OBSERVED.id, choice: "later", note: null }]);
  assert.equal(asked, 2, "item 2's select was asked and timed out");
});

test("PR 18 (A5b): a timeout at item 2's note keeps the earlier answer; the item stays Later", async () => {
  const { ctx } = ctxFor({ selects: ["Confirm", "Approve"], inputs: ["one."] });
  const realInput = ctx.ui.input!.bind(ctx.ui);
  let asked = 0;
  ctx.ui.input = async (title: string, placeholder?: string, opts?: unknown) => {
    if (++asked > 1) { await new Promise((resolve) => setTimeout(resolve, 60)); return undefined; }
    return realInput(title, placeholder, opts as { timeout?: number });
  };
  const { trusted, calls } = bridge([RECORD, DECISION]);
  const deadline = Date.now() + 30;
  assert.equal(await run(ctx, trusted, blocked(), deadline), refreshed);
  assert.deepEqual((calls[1].payload as { answers: unknown[] }).answers, [
    { subject: "research", id: RECORD.id, choice: "confirm", note: "one." },
    { subject: "claim", id: "C1", choice: "later", note: null }],
    "the item whose note timed out is never its chosen answer without its note (O1, decided): it stays Later");
});

test("PR 18 (§4-7, A4): the promise lines fit 76 columns; the keep line needs an earlier completed answer", () => {
  assert.ok(KEEP_COMPLETED.length <= 76, KEEP_COMPLETED);
  const earlier: EvidenceAnswer = { subject: "claim", id: "C1", choice: "approve", note: null };
  const shown = itemTitle(DECISION, 2, 2, DOCUMENT, [earlier]).split("\n");
  assert.ok(shown.includes(KEEP_COMPLETED), "k > 1 with an earlier completed answer: the line is shown");
  const keepAt = shown.indexOf(KEEP_COMPLETED);
  assert.ok(keepAt < shown.findIndex((line) => line.startsWith("Details: ")), "it comes before the Details lines");
  assert.equal(shown[shown.length - 1], SHORT_DIGEST_LINE, "the Document sha256 line stays last");
  const deferred: EvidenceAnswer = { subject: "claim", id: "C1", choice: "later", note: null };
  for (const [number, history] of [[1, [earlier]], [2, []], [2, [deferred]]] as Array<[number, EvidenceAnswer[]]>)
    assert.ok(!itemTitle(DECISION, number, 2, DOCUMENT, history).includes(KEEP_COMPLETED),
      `number ${number} with ${history.length} earlier answers: no keep line`);
  // A4: the note prompt line is at most 76 columns even when the shared note title is long
  assert.ok(notePrompt("Note (optional)").length <= 76, notePrompt("Note (optional)"));
  const long = notePrompt("Note (optional, the reason a deterministic check cannot exist today)");
  assert.ok(long.length <= 76, long);
  assert.ok(long.endsWith(" · Enter finishes this item · Esc goes back"),
    "the shared title is shortened, never the instructions");
});

test("the dialogs are given the time that is left, so a stuck dialog times out", async () => {
  const { ctx, shown } = ctxFor({ selects: ["Later"], inputs: [] });
  const deadline = Date.now() + 5_000;
  assert.deepEqual(await askItems([RECORD], ctx, deadline, DOCUMENT),
    [{ subject: "research", id: RECORD.id, choice: "later", note: null }]);
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

test("a refused presentation or an error is evaluated again; a refused receipt is the host's failure to record (review-2 m5)", async () => {
  const original = blocked();
  const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] });
  const refused = async () => ({ type: "evidence_refused", result: { type: "Fault" } });
  assert.equal(await run(ctx, refused, original), refreshed);
  const notAllowed = bridge([RECORD], { type: "evidence_receipt", result: { type: "Fault" } });
  const failures: unknown[] = [];
  const out = await confirmEvidence(original, "run-1", ctx, notAllowed.trusted as never,
    (failure) => { failures.push(failure); return Promise.resolve(refreshed); }, Date.now() + 60_000);
  assert.equal(out.response, refreshed);
  assert.equal(out.failure, "presentation_failed",
    "a receipt the core faulted on recorded nobody's answer: the failure to ask, never a decline");
  assert.deepEqual(failures, ["presentation_failed"]);
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

test("PR 12: a presentation answer without a valid document pair is nothing shown: no dialog, no confirmation", async () => {
  const cases: Array<[string, unknown]> = [
    ["no document member", NO_DOCUMENT],
    ["not an object", "sha256:nonsense"],
    ["a short digest", { digest: "sha256:0a1b2c3d4e5f", path: "/var/hitl/d.json" }],
    ["not a sha256 digest", { digest: "deadbeef".repeat(8), path: "/var/hitl/d.json" }],
    ["a relative path", { digest: DOCUMENT.digest, path: "empirica-projects/x/hitl/d.json" }],
    ["no path at all", { digest: DOCUMENT.digest }],
    ["bytes offered", { digest: DOCUMENT.digest, path: DOCUMENT.path, url: null, run12: null,
      text: '{"kind": "hitl_document"}' }],
    ["a foreign url", { digest: DOCUMENT.digest, path: DOCUMENT.path,
      url: "http://evil.example:18770/app/", run12: null }],
    ["an invented run id", { digest: DOCUMENT.digest, path: DOCUMENT.path, url: null,
      run12: "not-twelve-hex" }]];
  for (const [label, document] of cases) {
    const { ctx, shown } = ctxFor({ selects: ["Confirm"], inputs: [""] });
    const { trusted, calls } = bridge([RECORD],
      { type: "evidence_receipt", result: { type: "Allow" } }, document);
    const failures: unknown[] = [];
    const out = await confirmEvidence(blocked(), "run-1", ctx, trusted as never,
      async (failure) => { failures.push(failure); return refreshed; }, Date.now() + 60_000);
    assert.equal(out.response, refreshed, label);
    assert.equal(out.failure, "presentation_failed", label);
    assert.deepEqual(failures, ["presentation_failed"], label);
    assert.deepEqual(shown, [], label);
    assert.ok(!calls.some((call) => call.operation === "evidence_confirmation"),
      `${label}: nothing was shown, so nothing may be submitted`);
  }
});

test("PR 12: only the digest rides the confirmation — the document pair and the path never leave the private lane", async () => {
  const { ctx } = ctxFor({ selects: ["Confirm"], inputs: [""] });
  const calls: PrivateIngressRequest[] = [];
  const trusted = async (request: PrivateIngressRequest) => {
    calls.push(request);
    return request.operation === "evidence_presentation"
      ? { type: "evidence_presentation", receipt_id: "p-1", items: [RECORD], document: DOCUMENT }
      : { type: "evidence_receipt", result: { type: "Allow" } };
  };
  const out = await confirmEvidence(blocked(), "run-1", ctx, trusted as never, async () => refreshed,
    Date.now() + 60_000);
  assert.equal(out.response, refreshed);
  assert.deepEqual(calls[0].payload, {}, "the presentation request carries no document members");
  assert.ok(!JSON.stringify(out).includes(DOCUMENT.path), "the re-evaluation the model reads carries no path");
  assert.ok(!JSON.stringify(out).includes(DOCUMENT.digest), "nor the document identity");
  for (const call of calls) {
    const payload = JSON.stringify(call.payload ?? {});
    assert.ok(!payload.includes(DOCUMENT.path), `${call.operation}: the path never crosses a request`);
    assert.ok(!("document" in (call.payload ?? {})), `${call.operation}: the pair never crosses a request`);
  }
  const confirmation = calls.find((call) => call.operation === "evidence_confirmation")?.payload as
    Record<string, unknown>;
  assert.equal(confirmation.document_digest, DOCUMENT.digest,
    "the confirmation names the document the presentation wrote — the only member that ever leaves the pair");
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
  assert.deepEqual((calls[1].payload as { answers: unknown[]; document_digest?: string }).answers,
    [{ subject: "claim", id: TERMINAL.status, choice: "approve", note: null }]);
  assert.equal((calls[1].payload as { document_digest?: string }).document_digest, DOCUMENT.digest,
    "PR 12: the terminal consent's confirmation too repeats its presentation's digest");
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

test("PR 18 (A3/A5c): Esc at the terminal note goes back to the select; Esc there or a timeout records no approval", async () => {
  // Terminal consent (`consent_stop`, `freeze_scope`) shares the evidence dialog; its batch has one item,
  // so there is never an earlier answer to keep — what must not happen is an approval recorded off an Esc.
  for (const [name, status] of [["consent_stop", "stopped_residual"], ["freeze_scope", "stopped_frozen"]] as const) {
    const item: EvidenceItem = { ...TERMINAL_ITEM, id: status,
      text: `End this run as ${status}, without converging? Approve to end it now; Reject or Later keeps it active.`,
      title: status === "stopped_residual" ? TERMINAL_ITEM.title : "End the run as stopped_frozen?",
      description: `Not converged. Approve ends the run now as ${status}; Reject or Later keeps it active.` };
    const termination = { status, digest: TERMINAL.digest };
    const terminal = (): Response => {
      const block = terminalBlock();
      const batch = (block.result as unknown as { run: { confirmation_batch: Record<string, unknown> } })
        .run.confirmation_batch;
      batch.claim_ids = [status]; batch.terminal = termination;
      return block;
    };
    for (const [label, script, delay] of [
      ["Esc at the note, asked again, Esc at the select", { selects: ["Approve", undefined], inputs: [undefined] }, 0],
      ["a timeout at the note", { selects: ["Approve"], inputs: [] }, 1]] as Array<[string, Script, number]>) {
      const live = ctxFor(script);
      const realInput = live.ctx.ui.input!.bind(live.ctx.ui) as
        (title: string, placeholder?: string, opts?: { timeout?: number }) => Promise<string | undefined>;
      let notes = 0;
      if (delay > 0) live.ctx.ui.input = async (title: string, placeholder?: string, opts?: { timeout?: number }) => {
        if (++notes > 0) { await new Promise((resolve) => setTimeout(resolve, 60)); return undefined; }
        return realInput(title, placeholder, opts);
      };
      const { trusted, calls } = bridge([item]);
      const deadline = Date.now() + 30;
      const out = await confirmEvidence(terminal(), "run-1", live.ctx, trusted as never,
        async () => refreshed, deadline);
      assert.equal(out.response, refreshed, `${name}: ${label}`);
      assert.equal(out.failure, undefined, `${name}: ${label}: a shown batch spent as the human's own nothing`);
      assert.ok(!calls.some((call) => call.operation === "evidence_confirmation"), `${name}: ${label}: no approval receipt`);
    }
  }
});

// ---- review-2 m5 (round 12, decision 7c): a refused submit is nobody's recorded answer, never a decline ----------

for (const [label, receipt] of [
  ["the residuals moved while the dialog was open (evidence.document_mismatch)",
    { type: "evidence_receipt", result: { type: "Block", reasons: [{ code: "evidence.document_mismatch" }] } }],
  ["the submit faulted", { type: "evidence_receipt", result: { type: "Fault", code: "conflict", fail_direction: "closed" } }],
] as Array<[string, unknown]>) {
  test(`review-2 m5: a refused submit (${label}) reports the host's failure to record, never the human's decline`, async () => {
    // The round-11 scenario, Pi half: the presentation succeeds, the human approves the end of the
    // run, and the submit comes back refused — the core recorded nobody's answer, so the adapter
    // reports its own failure (`presentation_failed`) to re-evaluate with; the fresh evaluation
    // words the block `host_failed` ("Stop again to try again"), never "You declined ending the run".
    const live = ctxFor({ selects: ["Approve"], inputs: [""] });
    const { trusted, calls } = bridge([TERMINAL_ITEM], receipt, { ...DOCUMENT, residuals: 1 });
    const failures: unknown[] = [];
    const out = await confirmEvidence(terminalBlock(), "run-1", live.ctx, trusted as never,
      (failure) => { failures.push(failure); return Promise.resolve(refreshed); }, Date.now() + 60_000);
    assert.equal(out.response, refreshed, label);
    assert.equal(out.failure, "presentation_failed", label);
    assert.deepEqual(failures, ["presentation_failed"], label);
    assert.deepEqual(calls.map((c) => c.operation),
      ["evidence_presentation", "evidence_confirmation"],
      `${label}: the batch was shown and the answer submitted; only the submission was refused`);
  });
}

test("review-2 m5: a submit that never answers (the bridge threw) is the same failure to record", async () => {
  const live = ctxFor({ selects: ["Approve"], inputs: [""] });
  const calls: PrivateIngressRequest[] = [];
  const trusted = async (request: PrivateIngressRequest) => {
    calls.push(request);
    if (request.operation === "evidence_confirmation") throw new Error("the bridge went down");
    return { type: "evidence_presentation", receipt_id: "p-1", items: [TERMINAL_ITEM],
      document: { ...DOCUMENT, residuals: 1 } };
  };
  const failures: unknown[] = [];
  const out = await confirmEvidence(terminalBlock(), "run-1", live.ctx, trusted as never,
    (failure) => { failures.push(failure); return Promise.resolve(refreshed); }, Date.now() + 60_000);
  assert.equal(out.response, refreshed);
  assert.equal(out.failure, "presentation_failed",
    "a throw between present and confirm left nobody's answer recorded");
  assert.deepEqual(failures, ["presentation_failed"]);
  assert.equal(calls.length, 2);
});

test("review-2 m5: an admitted submit, a left-Later batch and a cancelled dialog stay the human's word", async () => {
  // The refusal is of the submit, not the dialog: later, cancel and a clean admitted receipt keep
  // the granted-nothing semantics of a shown batch (evaluated again with no host failure).
  for (const [label, selects, receipt] of [
    ["admitted receipt", ["Approve"], { type: "evidence_receipt", result: { type: "Allow" } }],
    ["all later", ["Later"], { type: "evidence_receipt", result: { type: "Block" } }],
    ["cancelled dialog", [undefined], { type: "evidence_receipt", result: { type: "Block" } }],
  ] as Array<[string, Array<string | undefined>, unknown]>) {
    const live = ctxFor({ selects, inputs: [""] });
    const failures: unknown[] = [];
    const out = await confirmEvidence(terminalBlock(), "run-1", live.ctx,
      bridge([TERMINAL_ITEM], receipt).trusted as never,
      (failure) => { failures.push(failure); return Promise.resolve(refreshed); }, Date.now() + 60_000);
    assert.equal(out.response, refreshed, label);
    assert.deepEqual(failures, [undefined], `${label}: a genuine answer or nothing is never a host failure`);
    assert.equal(out.failure, undefined, label);
  }
});
