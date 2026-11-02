import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@earendil-works/pi-tui";
import { expectedApprovalKind, govern, governanceTimeout, piGovernanceContext } from "../src/governance-ui.ts";
import { initialState, reduce, renderLines } from "../src/dialog-view.ts";
import type { Dialog, DialogDecision } from "../src/dialog-view.ts";
import type { PrivateIngress } from "../src/private-transport.ts";
import { PUBLIC_TOOLS } from "../src/public-tools.ts";
import { fakeCtx } from "./fakes.ts";

const fixture = JSON.parse(readFileSync(new URL("../../../tests/fixtures/governance-dialog-golden.json", import.meta.url), "utf8")) as {
  dialogs: { review: Dialog; confirmation: Dialog; hostile: Dialog; hostile_rationale: Dialog };
  review: { message: string };
};
const reviewDialog = fixture.dialogs.review;
const theme = { accent: (x: string) => x, muted: (x: string) => x, warning: (x: string) => x };
const ENTER = "\r", DOWN = "\x1b[B", RIGHT = "\x1b[C", BACKSPACE = "\x7f", ESC = "\x1b[27u";

/** The canonical bound document exactly as the Python private bridge returns it: the full
 * `digest`, the file's absolute `path`, the core-owned amendment `priors`, and the
 * core-derived `question` discriminator (M1, review 2) that chooses which dialog is shown.
 * PR 09's TypeScript carries no document text; the human opens the file.
 *
 * The path comes from the shared golden fixture's review message (M3, review 2): the
 * round-08 Claude fixture pins the *real* sidecar layout —
 * `<home>/projects/s256-<64 hex>/runs/s256-<64 hex>/gen-<n>/hitl/<64 hex>.json` — so the
 * Pi side can never drift back to a made-up layout without this test noticing. */
const reviewMessage = fixture.review.message;
const digestAt = reviewMessage.split("\n").findIndex(line => line.startsWith("Document: "));
assert.ok(digestAt >= 0, "the fixture's review message names its document");
const REAL_PATH = reviewMessage.split("\n")[digestAt + 1] as string;
assert.ok(REAL_PATH.length > 250, `the fixture path is the real layout, got ${REAL_PATH.length}`);
/** The path's file name is the document's own full digest, minus the `.json`. */
const HEX_OF_PATH = (REAL_PATH.split("/").pop() ?? "").replace(/\.json$/, "");
const DOCUMENT = { digest: `sha256:${HEX_OF_PATH}`, path: REAL_PATH,
  priors: { max_passes: 8, max_spawns: 1, max_audit_spawns: 2 }, question: "approve" } as const;

function drive(keys: string[], dialog = reviewDialog, confirmation = false): DialogDecision | undefined {
  let state = initialState(dialog, confirmation);
  for (const key of keys) {
    const result = reduce(state, key, dialog, confirmation);
    if ("done" in result) return result.done;
    state = result.state;
  }
  return undefined;
}

const toButtons = [DOWN, DOWN, DOWN];

test("dialog reducer approves defaults and maps reject and kitty escape", () => {
  assert.equal(drive([ENTER, ENTER])?.type, "approve");
  assert.equal(drive([...toButtons, RIGHT, ENTER])?.type, "reject");
  assert.equal(drive([ESC])?.type, "dismiss");
});

test("invalid integer histories never emit Approve", () => {
  const histories = [
    ["0", ...toButtons, ENTER],
    [DOWN, "9", "9", "9", "9", ...toButtons.slice(1), ENTER],
    ["1", "2", "3", "4", "5", ...toButtons, ENTER],
    ["5", BACKSPACE, ...toButtons, ENTER],
  ];
  for (const keys of histories) assert.equal(drive(keys), undefined, keys.join(" "));
});

test("range error follows its row after focus moves", () => {
  let state = initialState(reviewDialog);
  for (const key of ["0", DOWN]) {
    const result = reduce(state, key, reviewDialog);
    assert.ok("state" in result);
    if ("state" in result) state = result.state;
  }
  const lines = renderLines(reviewDialog, state, 80, theme,
    { confirmation: false, timeoutMs: 900_000, document: DOCUMENT });
  assert.ok(lines.some(line => line.includes("Must be an integer between 1 and 1024")));
});

test("edited integer is returned exactly", () => {
  const result = drive(["6", ...toButtons, ENTER]);
  assert.equal(result?.type, "approve");
  if (result?.type === "approve") assert.equal(result.configuration.budgets.max_passes, 6);
});

test("renderLines shows an escaped, bounded first line of a hostile goal at widths 80 and 50", () => {
  for (const width of [80, 50]) {
    const lines = renderLines(fixture.dialogs.hostile, initialState(fixture.dialogs.hostile), width, theme,
      { confirmation: false, timeoutMs: 120_000, document: DOCUMENT });
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    const start = lines.findIndex(line => line.startsWith("Goal  "));
    assert.ok(start >= 0, "the goal line is present");
    const plain = lines[start]!.replace(/\x1b\[0m/g, "");
    assert.match(plain, /\u2026"$/);
    const shown = plain.slice(6).slice(0, -1).replace(/\u2026$/, "");
    assert.ok(`"${fixture.dialogs.hostile.goal}"`.startsWith(shown),
      "the shown goal is an exact prefix, closed with an ellipsis and its quote");
    assert.ok(lines.filter(line => line.startsWith("Goal  ")).length === 1, "the goal is one line");
    assert.ok(!lines.some(line => line.includes("\n")), "escaped control characters stay escaped");
    if (width === 80) assert.ok(lines[0]!.includes("2 min"));
  }
});

test("renderLines matches checked-in screens byte-for-byte", () => {
  const invalid = initialState(reviewDialog); invalid.values.max_passes = 0;
  const cases = {
    review: (width: number) => renderLines(reviewDialog, initialState(reviewDialog), width, theme,
      { confirmation: false, timeoutMs: 900_000, document: DOCUMENT }),
    confirmation: (width: number) => renderLines(fixture.dialogs.confirmation,
      initialState(fixture.dialogs.confirmation, true), width, theme,
      { confirmation: true, timeoutMs: 900_000, document: DOCUMENT }),
    hostile: (width: number) => renderLines(fixture.dialogs.hostile,
      initialState(fixture.dialogs.hostile), width, theme,
      { confirmation: false, timeoutMs: 900_000, document: DOCUMENT }),
    "hostile-rationale": (width: number) => renderLines(fixture.dialogs.hostile_rationale,
      initialState(fixture.dialogs.hostile_rationale), width, theme,
      { confirmation: false, timeoutMs: 900_000, document: DOCUMENT }),
    "range-error": (width: number) => renderLines(reviewDialog, invalid, width, theme,
      { confirmation: false, timeoutMs: 900_000, document: DOCUMENT }),
  };
  for (const [name, render] of Object.entries(cases)) {
    for (const width of [80, 50]) {
      const expected = readFileSync(new URL(`dialog-view-golden/${name}-${width}.txt`, import.meta.url), "utf8");
      assert.equal(render(width).join("\n") + "\n", expected);
    }
  }
});

interface HarnessSettings {
  onPresent(): void;
  onRefresh(count: number): void;
  onCustom(): void;
}

function harness(keyScripts: string[][] = [[ENTER, ENTER]]) {
  const dialog = structuredClone(reviewDialog);
  const g = { state: "pending", control_mode: "deliberative", proposal_digest: "sha256:" + "a".repeat(64),
    plan_revision: 0, first_approval: false, prompt_error: null as string | null, human_wait: true, proposal: {
      budgets: Object.fromEntries(dialog.budgets.map(row => [row.key, row.value])) as Record<string, number>,
      rationale: dialog.rationale,
    }, context: { author: null, ingress: "pi_ui", interactive: true, delegation: false } };
  const run = { id: "run", status: "active", spike_backend: { kind: "spikes_unavailable" }, governance: g };
  const decisions: Array<Record<string, unknown>> = [];
  const renders: string[][] = [];
  const settings: HarnessSettings = { onPresent() {}, onRefresh() {}, onCustom() {} };
  let refreshes = 0, customCalls = 0;
  const ctx = fakeCtx(); ctx.hasUI = true;
  ctx.ui.custom = async factory => await new Promise(resolve => {
    customCalls++;
    const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
    renders.push(component.render(80));
    settings.onCustom();
    for (const key of keyScripts.shift() ?? []) component.handleInput(key);
  });
  const trusted: PrivateIngress = async request => {
    if (request.operation === "governance_context") settings.onRefresh(++refreshes);
    if (request.operation === "governance_decision") {
      const payload = structuredClone(request.payload as Record<string, unknown>);
      decisions.push(payload);
      if (payload.outcome === "present") settings.onPresent();
      const submission = payload.submission as { action: string; configuration: { budgets: Record<string, number> } } | undefined;
      if (submission?.action === "reject") g.state = "rejected";
      if (submission?.action === "approve") {
        if (JSON.stringify(submission.configuration.budgets) === JSON.stringify(g.proposal.budgets)) g.state = "approved";
        else {
          g.proposal = { ...g.proposal, budgets: structuredClone(submission.configuration.budgets) };
          g.plan_revision++;
          g.proposal_digest = "sha256:" + "b".repeat(64);
          dialog.epoch = g.plan_revision;
          for (const row of dialog.budgets) row.value = g.proposal.budgets[row.key];
        }
      }
    }
    const withPresentation = (request.operation === "governance_decision"
      && (request.payload as { outcome?: string }).outcome === "present")
      || request.operation === "governance_context";
    // The bound document the core hands back: `approve` until the human amends, then the
    // amended epoch's `confirm_amendment` — the question, not any adapter flag, picks the
    // dialog (M1, review 2).
    const document = (request.operation === "governance_context")
      ? (documentOverride.value ?? { ...DOCUMENT,
          question: g.plan_revision > 0 ? "confirm_amendment" as const : "approve" as const })
      : undefined;
    return structuredClone({ protocol: "empirica/v2", request_id: "trusted-governance",
      result: { type: "Allow", converged: false, run,
        ...(withPresentation ? { presentation: { dialog, scope: null,
          ...(document ? { document } : {}),
          ...(marker.value ? { document_unavailable: true } : {}) } } : {}) } }) as never;
  };
  const documentOverride = { value: undefined as Record<string, unknown> | undefined };
  const marker = { value: false };
  return { ctx, trusted, decisions, renders, run, g, settings, documentOverride, marker,
    get customCalls() { return customCalls; },
    invoke: (signal?: AbortSignal, deadline?: number) => govern("run", ctx, trusted, signal, undefined, deadline) };
}

function outcomes(h: ReturnType<typeof harness>): string[] {
  return h.decisions.map(row => row.outcome as string ?? (row.submission as { action: string })?.action);
}

test("govern uses one custom component and Accept approves", async () => {
  const h = harness();
  const result = await h.invoke();
  assert.equal(result.result.type, "Allow");
  assert.deepEqual(outcomes(h), ["present", "approve"]);
  assert.equal(h.customCalls, 1);
});

test("already-cancelled review consumes no presentation capacity", async () => {
  const h = harness(), controller = new AbortController(); controller.abort();
  assert.equal((await h.invoke(controller.signal)).result.type, "Block");
  assert.deepEqual(h.decisions, []);
  assert.equal(h.customCalls, 0);
});

test("confirmation refresh rejects stale revision or digest", async () => {
  for (const digest of [false, true]) {
    const h = harness([["6", ...toButtons, ENTER], [ENTER]]);
    h.settings.onRefresh = count => {
      if (count === 2) {
        h.g.plan_revision++;
        if (digest) h.g.proposal_digest = "sha256:" + "c".repeat(64);
      }
    };
    const result = await h.invoke();
    assert.equal(result.result.type, "Block");
    if (result.result.type === "Block") assert.equal(result.result.reasons[0].code, "governance.stale_proposal");
    assert.deepEqual(outcomes(h), ["present", "approve"]);
    assert.equal(h.g.state, "pending");
  }
});

test("confirmation shares the original deadline", async () => {
  const original = Date.now; let clock = 0; Date.now = () => clock;
  try {
    const h = harness([["6", ...toButtons, ENTER], [ENTER]]);
    h.settings.onRefresh = count => { if (count === 2) clock = 900_001; };
    assert.equal((await h.invoke(undefined, 900_000)).result.type, "Block");
    assert.deepEqual(outcomes(h), ["present", "approve"]);
    assert.equal(h.customCalls, 1);
  } finally { Date.now = original; }
});

test("amend receives locked confirmation and then approves", async () => {
  const h = harness([["6", ...toButtons, ENTER], [ENTER]]);
  await h.invoke();
  assert.deepEqual(outcomes(h), ["present", "approve", "present", "approve"]);
  assert.equal(h.g.proposal.budgets.max_passes, 6);
  assert.equal(h.g.state, "approved");
});

test("declining confirmation keeps the edited revision pending", async () => {
  const h = harness([["6", ...toButtons, ENTER], [RIGHT, ENTER]]);
  await h.invoke();
  assert.deepEqual(outcomes(h), ["present", "approve", "present", "dismiss"]);
  assert.equal(h.g.plan_revision, 1);
  assert.equal(h.g.state, "pending");
});

test("no-op keys and cancel approve nothing", async () => {
  const h = harness([["x", ESC]]);
  await h.invoke();
  assert.deepEqual(outcomes(h), ["present", "dismiss"]);
  assert.equal(h.g.state, "pending");
});

test("prompt error and expiry remain fail closed", async () => {
  const prompt = harness(); prompt.g.prompt_error = "governance.interaction_limit";
  assert.equal((await prompt.invoke()).result.type, "Block");
  assert.deepEqual(prompt.decisions, []);
  const original = Date.now; let clock = 0; Date.now = () => clock;
  try {
    const expired = harness([[ESC]]);
    expired.settings.onPresent = () => { clock = 2; };
    await expired.invoke(undefined, 1);
    assert.deepEqual(outcomes(expired), ["present", "dismiss"]);
  } finally { Date.now = original; }
});

test("abort closes an open custom dialog", async () => {
  const controller = new AbortController();
  const h = harness([[]]);
  h.settings.onCustom = () => controller.abort();
  await h.invoke(controller.signal);
  assert.deepEqual(outcomes(h), ["present", "dismiss"]);
});

test("missing custom UI fails closed without prompt fallback", async () => {
  const h = harness(); delete h.ctx.ui.custom;
  assert.equal((await h.invoke()).result.type, "Block");
  assert.deepEqual(h.decisions, []);
});

test("interactive auto presents initially, then accepts automatically without a dialog", async () => {
  const initial = harness(); initial.g.control_mode = "auto";
  await initial.invoke();
  assert.deepEqual(outcomes(initial), ["present", "approve"]);
  assert.equal(initial.customCalls, 1);

  const later = harness(); later.g.control_mode = "auto"; later.g.first_approval = true;
  await later.invoke();
  assert.deepEqual(outcomes(later), ["approve"]);
  assert.equal(later.customCalls, 0);
});

test("interactive auto with unavailable UI fails closed and never delegates", async () => {
  const h = harness(); h.g.control_mode = "auto"; h.g.context.delegation = true;
  h.ctx.hasUI = false; h.g.context.ingress = "unavailable"; delete h.ctx.ui.custom;
  assert.equal((await h.invoke()).result.type, "Block");
  assert.deepEqual(h.decisions, []);
});

// Pi 1.0 extensions also load in --mode json/--print (hasUI=false, every ui method a no-op) and
// in RPC mode (hasUI=true, custom() resolves undefined). Neither may crash or approve.
test("without a UI (print/json mode) the dialog degrades to an unavailable refusal, never a prompt", async () => {
  const h = harness(); h.ctx.hasUI = false;
  const response = await h.invoke();
  assert.equal(response.result.type, "Block");
  assert.equal(h.customCalls, 0, "no component is attempted");
  assert.deepEqual(h.decisions, []);
});

test("a UI whose custom() resolves nothing (RPC mode) is a dismissal, not a crash or an approval", async () => {
  const h = harness();
  h.ctx.ui.custom = async () => { h.settings.onCustom(); return undefined as never; };
  const response = await h.invoke();
  assert.equal(response.result.type, "Block");
  assert.deepEqual(outcomes(h), ["present", "dismiss"]);
  assert.ok(!outcomes(h).includes("approve"));
});

test("phase decision matches deliberative, interactive-auto, and delegated authority", () => {
  const h = harness();
  assert.equal(expectedApprovalKind(h.g), "host_ui");
  h.g.control_mode = "auto";
  assert.equal(expectedApprovalKind(h.g), "host_ui");
  h.g.first_approval = true;
  assert.equal(expectedApprovalKind(h.g), "auto");
  h.g.first_approval = false; h.g.context.interactive = false; h.g.context.delegation = true;
  assert.equal(expectedApprovalKind(h.g), "auto");
});

test("governance context does not inspect configured models", () => {
  const ctx = fakeCtx(); ctx.hasUI = true; ctx.model = { provider: "anthropic", id: "sonnet" } as never;
  let calls = 0; ctx.modelRegistry = { getAvailable: () => { calls++; throw new Error("must not read"); },
    getError: () => { calls++; return "configured error"; } } as never;
  assert.deepEqual(piGovernanceContext(ctx), { author: { provider_id: "anthropic", model_id: "sonnet", source: "pi-context" }, ingress: "pi_ui" });
  assert.equal(calls, 0);
});

test("timeout follows the shared Python/TypeScript case table with exact results and messages", () => {
  const table = JSON.parse(readFileSync(new URL("../../../tests/fixtures/governance-timeout-cases.json", import.meta.url), "utf8")) as {
    cases: { name: string; env: string | null; seconds: number | null; error: string | null }[];
  };
  assert.ok(table.cases.length >= 20);
  const prior = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  try {
    for (const { name, env, seconds, error } of table.cases) {
      if (env === null) delete process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
      else process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = env;
      if (error === null) assert.equal(governanceTimeout(), (seconds as number) * 1000, name);
      else assert.throws(() => governanceTimeout(), (caught: unknown) => caught instanceof Error && caught.message === error, name);
    }
  } finally { if (prior === undefined) delete process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS; else process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = prior; }
});

const configureFallback = { protocol: "empirica/v2", request_id: "cfg", result: { type: "Allow", converged: false,
  run: { id: "cfg-run", status: "active", goal: "g",
    spike_backend: { kind: "spikes_unavailable" }, governance: null } } } as const;

test("initial ingress throw fails closed with fallback RunView", async () => {
  const throwing: PrivateIngress = async () => { throw new Error("injected"); };
  const ctx = fakeCtx(); ctx.hasUI = true;
  const out = await govern("cfg-run", ctx, throwing, undefined, undefined, undefined, structuredClone(configureFallback));
  assert.equal(out.result.type, "Block");
  if (out.result.type === "Block") assert.equal(out.result.reasons[0].code, "governance.approval_unavailable");
});

test("malformed initial response fails closed with fallback Block", async () => {
  const malformed: PrivateIngress = async () => ({ not: "a response" } as never);
  const ctx = fakeCtx(); ctx.hasUI = true;
  const out = await govern("cfg-run", ctx, malformed, undefined, undefined, undefined, structuredClone(configureFallback));
  assert.equal(out.result.type, "Block");
});

test("initial ingress throw without fallback returns closed Fault", async () => {
  const throwing: PrivateIngress = async () => { throw new Error("injected"); };
  const ctx = fakeCtx(); ctx.hasUI = true;
  const out = await govern("cfg-run", ctx, throwing);
  assert.equal(out.result.type, "Fault");
  if (out.result.type === "Fault") assert.equal(out.result.fail_direction, "closed");
});

test("the amendment warning is wrapped in full, never truncated, beside the document lines", () => {
  const dialog = fixture.dialogs.confirmation;
  for (const width of [80, 50]) {
    const lines = renderLines(dialog, initialState(dialog, true), width, theme,
      { confirmation: true, timeoutMs: 120_000, document: DOCUMENT });
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    const text = lines.map(line => line.trim()).join(" ");
    assert.ok(text.includes(dialog.amendment_warning), `warning truncated at ${width}`);
    // the document lines: the short digest, then the absolute path, wrapped but never cut.
    // X4 (Copilot #50) makes it a hard rule at every width: a segment wider than the dialog
    // width is hard-wrapped at the width — no character dropped, no ellipse, `truncate`
    // never fires — so joining the path lines back gives the exact path, at 80 where every
    // segment fits and at 50 where the 70-column `s256-<64 hex>` segments do not.
    const pathLines = joinBackPaysThePath(lines);
    assert.ok(pathLines.pieces.length > 1, `the ~274-character path wraps at ${width}`);
    assert.equal(pathLines.joined, DOCUMENT.path, `the whole path survives at ${width}`);
    assert.ok(!pathLines.pieces.some(line => line.includes("…")), "a path line is never cut");
  }
});

/** The path lines as they appear between the digest line and the blank line that ends the
 * document region — hard-wrapped pieces included, which do not start with `/` — and what they
 * join back to (X4, Copilot #50: the exact path, at every width). */
function joinBackPaysThePath(lines: string[]): { pieces: string[]; joined: string } {
  const digestLine = lines.findIndex(line => line.startsWith("Document: "));
  assert.ok(digestLine >= 0, "document digest line missing");
  assert.match(lines[digestLine]!, /^Document: sha256:[0-9a-f]{12}$/);
  let end = digestLine + 1;
  while (end < lines.length && lines[end] !== "") end += 1;
  const pieces = lines.slice(digestLine + 1, end);
  return { pieces, joined: pieces.join("") };
}

test("the review screen keeps no rationale text and names the document", () => {
  const dialog = fixture.dialogs.review;
  for (const width of [80, 50]) {
    const lines = renderLines(dialog, initialState(dialog), width, theme,
      { confirmation: false, timeoutMs: 120_000, document: DOCUMENT });
    assert.ok(!lines.join("\n").includes("Sized for the supplied claims"),
      "the rationale body lives in the document, not on screen");
    const pointer = lines.map(line => line.trim()).join(" ");
    assert.ok(pointer.includes(dialog.rationale_label)
      && pointer.includes("in the document"),
      "one pointer region under the contract-owned label, never the rationale body");
  }
});

test("the confirm screen takes its (was ...) priors from the document, not a host-held dialog", () => {
  const dialog = fixture.dialogs.confirmation;
  const withPriors = renderLines(dialog, initialState(dialog, true), 80, theme,
    { confirmation: true, timeoutMs: 120_000, document: DOCUMENT });
  assert.ok(withPriors.some(line => line.includes("6 (was 8)")),
    "the amendment prior is shown from the document's priors");
  const noPriors = renderLines(dialog, initialState(dialog, true), 80, theme,
    { confirmation: true, timeoutMs: 120_000, document: { ...DOCUMENT, priors: null } });
  assert.ok(!noPriors.some(line => line.includes("(was")),
    "no prior in the document means no invented prior on screen");
});
// --- PR 09: write-first binding through the TypeScript mediation (ADR-0072) ---

test("every governance decision payload carries the bound document's digest", async () => {
  const h = harness([["6", ...toButtons, ENTER], [ENTER]]);
  await h.invoke();
  assert.deepEqual(outcomes(h), ["present", "approve", "present", "approve"]);
  for (const payload of h.decisions)
    assert.equal(payload.document_digest, DOCUMENT.digest, JSON.stringify(payload));
  assert.equal(h.documentOverride.value, undefined);
});

test("present, decision and dismiss all name the document the human read", async () => {
  const h = harness([[ESC]]);
  await h.invoke();
  assert.deepEqual(outcomes(h), ["present", "dismiss"]);
  for (const payload of h.decisions)
    assert.equal(payload.document_digest, DOCUMENT.digest);
});

test("no bound document on the context answer refuses before any present", async () => {
  for (const kind of ["absent", "malformed", "marker"] as const) {
    const h = harness([[ENTER, ENTER]]);
    if (kind === "absent") h.documentOverride.value = {};
    if (kind === "marker") h.marker.value = true;
    if (kind === "malformed")
      h.documentOverride.value = { digest: "sha256:" + "g".repeat(64), path: "/no/file" };
    const result = await h.invoke();
    assert.equal(result.result.type, "Block");
    if (result.result.type === "Block")
      assert.equal(result.result.reasons[0]!.code, "governance.approval_unavailable");
    assert.deepEqual(h.decisions, [], "no present may accompany an unbindable document");
    assert.equal(h.customCalls, 0);
  }
});

test("a governance.document_mismatch Block reaches the caller unchanged", async () => {
  const h = harness([[ENTER, ENTER]]);
  const recovery = PUBLIC_TOOLS.recovery["governance.document_mismatch"];
  const mismatch = structuredClone({ protocol: "empirica/v2", request_id: "trusted-governance",
    result: { type: "Block", run: h.run,
      reasons: [{ code: "governance.document_mismatch", parameters: {}, ...recovery }] } }) as never;
  const trusted = async (request: Parameters<typeof h.trusted>[0]) =>
    (request as { operation?: string }).operation === "governance_decision"
      && (request.payload as { outcome?: string }).outcome === "present" ? mismatch : h.trusted(request);
  const out = await govern("run", h.ctx, trusted);
  assert.equal(out.result.type, "Block");
  if (out.result.type === "Block")
    assert.equal((out.result.reasons[0] as { code: string }).code, "governance.document_mismatch");
  assert.deepEqual(outcomes(h), []);
});

test("the bound document never escapes into a model-facing result", async () => {
  const h = harness([[ENTER, ENTER]]);
  const out = await h.invoke();
  const text = JSON.stringify(out);
  assert.ok(!text.includes(DOCUMENT.path), "the absolute machine path is private");
  assert.ok(!text.includes(DOCUMENT.digest), "the document's digest name is private to the mediation");
  assert.ok(!text.includes('"document"'), "no private presentation member survives");
  assert.ok(!text.includes("document_unavailable"), "the marker is private to the mediation");
});

test("the dialog shown follows the bound document's question after a dismissed confirm", async () => {
  // M1 (review 2), the Pi half: amend 8→6, dismiss the confirm dialog, then mediate again
  // on the same values (re-proposing them is a no-op revision). The epoch's document still
  // asks confirm_amendment, so the next dialog is the CONFIRM view — with its (was 8) priors
  // and the amendment warning — never the editable review view. The document chooses; the
  // adapter's own recursion flag does not.
  const h = harness([["6", ...toButtons, ENTER], [ESC], [ESC]]);
  const first = await h.invoke();
  assert.equal(first.result.type, "Block");
  assert.deepEqual(outcomes(h), ["present", "approve", "present", "dismiss"]);
  assert.equal(h.g.state, "pending");
  assert.equal(h.g.proposal.budgets.max_passes, 6);
  const second = await h.invoke();
  assert.equal(h.customCalls, 3);
  const confirm = h.renders[2].join("\n");
  assert.ok(confirm.includes("confirm edited configuration"), confirm);
  assert.ok(confirm.includes("6 (was 8)"), "the amendment prior is shown");
  assert.ok(confirm.includes(reviewDialog.amendment_warning), "the amendment warning is shown");
  assert.ok(!confirm.includes("reviews left"), "the confirm view never counts review capacity");
  assert.deepEqual(outcomes(h),
    ["present", "approve", "present", "dismiss", "present", "dismiss"]);
});

test("the path block wraps at slash boundaries, hard-wrapping only what cannot fit", () => {
  // M3 (review 2) set the wrap rule; X4 (Copilot #50) completes it: the realistic path is
  // ~274 characters and must reach the human with NOTHING dropped. The Pi dialog breaks it
  // at "/" boundaries while the pieces fit, and a single segment wider than the dialog width
  // (the 70-column `s256-<64 hex>` directories at width 50) is hard-wrapped at the width, so
  // every piece fits, no piece is cut with an ellipse and joining the lines back rebuilds the
  // exact path. A hard-wrapped continuation holds no "/": it is the inside of one segment.
  // The 69-character file name still owns the last line whole wherever it fits.
  const base = { confirmation: false, timeoutMs: 900_000, document: DOCUMENT };
  for (const width of [80, 50]) {
    const lines = renderLines(reviewDialog, initialState(reviewDialog), width, theme, base);
    assert.ok(lines.every(line => visibleWidth(line) <= width), `every line fits ${width}`);
    const digestAt = lines.findIndex(line => line.startsWith("Document: "));
    assert.ok(digestAt >= 0, "the short digest line");
    const nextBlank = lines.findIndex((line, index) => index > digestAt && line === "");
    const pathLines = lines.slice(digestAt + 1, nextBlank >= 0 ? nextBlank : lines.length);
    assert.ok(pathLines.length > 1, `the 274-character path wraps at ${width}`);
    assert.ok(pathLines.every(line => line.startsWith("/") || !line.includes("/")),
      "a piece either starts a segment or is the inside of one — never a splice across two");
    assert.ok(!pathLines.some(line => line.includes("…")), "a path piece is never cut");
    assert.equal(pathLines.join(""), DOCUMENT.path,
      `nothing is lost or split joining the fragments back together at ${width}`);
    const fileName = "/" + (DOCUMENT.path.split("/").pop() ?? "");
    if (width >= visibleWidth(fileName)) {
      assert.equal(pathLines[pathLines.length - 1], fileName,
        "the file name owns the last line, whole");
    }
  }
});
