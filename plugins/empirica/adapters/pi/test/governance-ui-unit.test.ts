import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@earendil-works/pi-tui";
import { expectedApprovalKind, govern, governanceTimeout, piGovernanceContext } from "../src/governance-ui.ts";
import { initialState, reduce, renderLines } from "../src/dialog-view.ts";
import type { Dialog, DialogDecision } from "../src/dialog-view.ts";
import type { PrivateIngress } from "../src/private-transport.ts";
import { fakeCtx } from "./fakes.ts";

const fixture = JSON.parse(readFileSync(new URL("../../../tests/fixtures/governance-dialog-golden.json", import.meta.url), "utf8")) as {
  dialogs: { review: Dialog; confirmation: Dialog; hostile: Dialog; hostile_rationale: Dialog };
};
const reviewDialog = fixture.dialogs.review;
const theme = { accent: (x: string) => x, muted: (x: string) => x, warning: (x: string) => x };
const ENTER = "\r", DOWN = "\x1b[B", RIGHT = "\x1b[C", BACKSPACE = "\x7f", ESC = "\x1b[27u";

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
    { confirmation: false, timeoutMs: 900_000 });
  assert.ok(lines.some(line => line.includes("Must be an integer between 1 and 1024")));
});

test("edited integer is returned exactly", () => {
  const result = drive(["6", ...toButtons, ENTER]);
  assert.equal(result?.type, "approve");
  if (result?.type === "approve") assert.equal(result.configuration.budgets.max_passes, 6);
});

test("renderLines shows an escaped, bounded prefix of a hostile goal at widths 80 and 50", () => {
  for (const width of [80, 50]) {
    const lines = renderLines(fixture.dialogs.hostile, initialState(fixture.dialogs.hostile), width, theme,
      { confirmation: false, timeoutMs: 120_000 });
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    const start = lines.findIndex(line => line.startsWith("Goal  "));
    const end = lines.findIndex(line => line.startsWith("Host  "));
    const goal = lines.slice(start, end);
    assert.equal(goal.length, 4, "the goal never takes more than four lines");
    assert.match(goal[3], /… \(\+\d+ more lines\)$/);
    const shown = goal.slice(0, 3).map(line => line.slice(6)).join("");
    assert.ok(`"${fixture.dialogs.hostile.goal}"`.startsWith(shown), "the shown goal is an exact prefix");
    assert.ok(!lines.some(line => line.includes("\n")), "escaped control characters stay escaped");
    if (width === 80) assert.ok(lines[0].includes("2 min"));
  }
});

test("renderLines matches checked-in screens byte-for-byte", () => {
  const invalid = initialState(reviewDialog); invalid.values.max_passes = 0;
  const cases = {
    review: (width: number) => renderLines(reviewDialog, initialState(reviewDialog), width, theme,
      { confirmation: false, timeoutMs: 900_000 }),
    confirmation: (width: number) => renderLines(fixture.dialogs.confirmation,
      initialState(fixture.dialogs.confirmation, true), width, theme,
      { confirmation: true, before: reviewDialog, timeoutMs: 900_000 }),
    hostile: (width: number) => renderLines(fixture.dialogs.hostile,
      initialState(fixture.dialogs.hostile), width, theme,
      { confirmation: false, timeoutMs: 900_000 }),
    "hostile-rationale": (width: number) => renderLines(fixture.dialogs.hostile_rationale,
      initialState(fixture.dialogs.hostile_rationale), width, theme,
      { confirmation: false, timeoutMs: 900_000 }),
    "range-error": (width: number) => renderLines(reviewDialog, invalid, width, theme,
      { confirmation: false, timeoutMs: 900_000 }),
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
    plan_revision: 0, first_approval: false, prompt_error: null as string | null, proposal: {
      budgets: Object.fromEntries(dialog.budgets.map(row => [row.key, row.value])) as Record<string, number>,
      rationale: dialog.rationale,
    }, context: { author: null, ingress: "pi_ui", interactive: true, delegation: false } };
  const run = { id: "run", status: "active", governance: g };
  const decisions: Array<Record<string, unknown>> = [];
  const settings: HarnessSettings = { onPresent() {}, onRefresh() {}, onCustom() {} };
  let refreshes = 0, customCalls = 0;
  const ctx = fakeCtx(); ctx.hasUI = true;
  ctx.ui.custom = async factory => await new Promise(resolve => {
    customCalls++;
    const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
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
    const withPresentation = request.operation === "governance_decision"
      && (request.payload as { outcome?: string }).outcome === "present";
    return structuredClone({ protocol: "empirica/v2", request_id: "trusted-governance",
      result: { type: "Allow", converged: false, run,
        ...(withPresentation ? { presentation: { dialog, scope: null } } : {}) } }) as never;
  };
  return { ctx, trusted, decisions, run, g, settings, get customCalls() { return customCalls; },
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
test("without a UI (print/json mode) the dialog degrades to a stored dismissal, never a prompt", async () => {
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
  run: { id: "cfg-run", status: "active", goal: "g", governance: null } } } as const;

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

test("the amendment warning and rationale label are wrapped in full, never truncated", () => {
  const dialog = fixture.dialogs.confirmation;
  for (const width of [80, 50]) {
    const lines = renderLines(dialog, initialState(dialog), width, theme,
      { confirmation: true, timeoutMs: 120_000, before: fixture.dialogs.review });
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    const text = lines.map(line => line.trim()).join(" ");
    assert.ok(text.includes(dialog.amendment_warning), `warning truncated at ${width}`);
    assert.ok(text.includes(dialog.rationale_label), `label truncated at ${width}`);
  }
});
