// Fast native-control tests. The fake ingress models only service response shape;
// Python service tests own decision resolution, CAS, replay, and consent authority.
import { test } from "node:test";
import assert from "node:assert/strict";
import { govern, governanceTimeout, safeGovernanceText } from "../src/governance-ui.ts";
import type { PrivateIngress } from "../src/private-transport.ts";
import { fakeCtx } from "./fakes.ts";

const APPROVE = "Approve current displayed proposal";
const EDIT = "Edit configuration for another review";
const REQUEST = "Request changes in plain language";
const REJECT = "Reject proposal";
const author = { provider_id: "anthropic", model_id: "claude-sonnet-4-6" };
const auditor = { provider_id: "anthropic", model_id: "claude-opus-4-6" };

function harness(choice = APPROVE) {
  const g = {
    state: "pending", control_mode: "deliberative", proposal_digest: "sha256:" + "a".repeat(64),
    plan_revision: 2, revision_limit: 64, inventory_status: "multiple", prompt_error: null as string | null,
    proposal: { budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 },
      modes: { multi_provider: false, cli_exec: false }, auditor, allow_same_model: false },
    budgets: { passes_used: 2, spawns_used: 0, audit_spawns_used: 0 },
    review_text: "CANONICAL REVIEW TEXT",
    context: { inventory: { members: [author, auditor], source: "pi_registry", complete: true, authorized: true },
      author, ingress: "pi_ui" },
  };
  const run = { id: "ui-unit-run", status: "active", goal: "goal", governance: g };
  const calls: string[] = [], decisions: Array<Record<string, unknown>> = [];
  const settings = { cancelAt: "", inputValue: "", requestText: "Please revise scope.",
    onPresent: () => {}, onRefresh: (_count: number) => {} };
  let refreshes = 0;
  const ctx = fakeCtx();
  ctx.hasUI = true;
  ctx.ui.confirm = async title => { calls.push(title); return title !== settings.cancelAt; };
  ctx.ui.select = async (title, options) => {
    calls.push(title);
    if (title === settings.cancelAt) return undefined;
    if (title === "Decision") return choice;
    if (title === "Independent auditor") return options.at(-1);
    return "Disabled";
  };
  ctx.ui.input = async title => {
    calls.push(title);
    if (title === settings.cancelAt) return undefined;
    return title.startsWith("What must change?") ? settings.requestText : settings.inputValue;
  };
  const trusted: PrivateIngress = async request => {
    if (request.operation === "governance_context") settings.onRefresh(++refreshes);
    if (request.operation === "governance_decision") {
      const payload = structuredClone(request.payload as Record<string, unknown>);
      decisions.push(payload);
      if (payload.outcome === "present") settings.onPresent();
      const submission = payload.submission as { action: string; configuration: typeof g.proposal } | undefined;
      if (submission && ["approve", "edit"].includes(submission.action) &&
          JSON.stringify(submission.configuration) !== JSON.stringify(g.proposal)) {
        g.proposal = structuredClone(submission.configuration);
        g.plan_revision++;
        g.proposal_digest = "sha256:" + "b".repeat(64);
        g.review_text = "CANONICAL EDITED REVIEW TEXT";
      } else if (submission?.action === "approve") g.state = "approved";
    }
    return { protocol: "empirica/v2", request_id: "trusted-governance",
      result: { type: "Allow", converged: false, run } };
  };
  return { g, run, calls, decisions, settings, ctx, trusted,
    invoke: (signal?: AbortSignal) => govern(run.id, ctx, trusted, signal) };
}

function dismissed(h: ReturnType<typeof harness>) {
  assert.deepEqual(h.decisions.map(d => d.outcome ?? (d.submission as { action: string })?.action), ["present", "dismiss"]);
}

test("canonical review text is delivered and unchanged approval is raw submission", async () => {
  const h = harness();
  await h.invoke();
  assert.ok(h.calls.includes("Empirica scope review — revision 2 of at most 64"));
  assert.deepEqual(h.decisions.map(d => d.outcome ?? (d.submission as { action: string })?.action), ["present", "approve"]);
  assert.equal((h.decisions[1].submission as { configuration: unknown }).configuration !== undefined, true);
});

test("already-cancelled review consumes no presentation capacity", async () => {
  const h = harness(), controller = new AbortController(); controller.abort();
  const result = (await h.invoke(controller.signal)).result;
  assert.equal(result.type, "Block");
  assert.deepEqual(h.decisions, []);
  assert.deepEqual(h.calls, []);
});

test("approval requires an actual inventory affirmation", async () => {
  const h = harness(); h.settings.cancelAt = "Inventory is complete and authorized";
  const result = (await h.invoke()).result;
  assert.ok(h.calls.includes(h.settings.cancelAt));
  assert.equal(result.type, "Block");
  dismissed(h);
  assert.equal(h.g.state, "pending");
});

test("confirmation refresh cannot silently replace the amended revision or digest", async () => {
  for (const changeDigest of [false, true]) {
    const h = harness(EDIT);
    h.ctx.ui.input = async title => title.startsWith("Investigation passes") ? "6" : "";
    h.settings.onRefresh = count => {
      if (count === 2) {
        h.g.plan_revision++;
        if (changeDigest) h.g.proposal_digest = "sha256:" + "c".repeat(64);
      }
    };
    const result = (await h.invoke()).result;
    assert.equal(result.type, "Block");
    if (result.type === "Block") assert.equal(result.reasons[0].code, "governance.stale_proposal");
    assert.deepEqual(h.decisions.map(d => d.outcome ?? (d.submission as { action: string })?.action),
      ["present", "edit"]);
    assert.equal(h.g.state, "pending");
    assert.ok(!h.calls.some(title => title.startsWith("FINAL CONFIRMATION")));
  }
});

test("confirmation shares the original review deadline", async () => {
  const original = Date.now; let clock = 0; Date.now = () => clock;
  try {
    const h = harness(EDIT);
    h.ctx.ui.input = async title => title.startsWith("Investigation passes") ? "6" : "";
    h.settings.onRefresh = count => { if (count === 2) clock = 900_001; };
    const result = (await h.invoke()).result;
    assert.equal(result.type, "Block");
    assert.deepEqual(h.decisions.map(d => d.outcome ?? (d.submission as { action: string })?.action),
      ["present", "edit"]);
  } finally { Date.now = original; }
});

test("edited proposal gets same-call locked confirmation before approval", async () => {
  const h = harness(EDIT);
  h.ctx.ui.input = async title => title.startsWith("Investigation passes") ? "6" : "";
  await h.invoke();
  assert.deepEqual(h.decisions.map(d => d.outcome ?? (d.submission as { action: string })?.action),
    ["present", "edit", "present", "approve"]);
  assert.ok(h.calls.some(title => title.startsWith("FINAL CONFIRMATION")));
  assert.equal(h.g.proposal.budgets.max_passes, 6);
  assert.equal(h.g.state, "approved");
});

test("declining locked confirmation keeps edited revision pending", async () => {
  const h = harness(EDIT);
  h.ctx.ui.input = async title => title.startsWith("Investigation passes") ? "6" : "";
  h.ctx.ui.confirm = async title => { h.calls.push(title); return !title.startsWith("FINAL CONFIRMATION"); };
  await h.invoke();
  assert.deepEqual(h.decisions.map(d => d.outcome ?? (d.submission as { action: string })?.action),
    ["present", "edit", "present", "dismiss"]);
  assert.equal(h.g.plan_revision, 3);
  assert.equal(h.g.state, "pending");
});

test("request changes has a separate exact-text path", async () => {
  const h = harness(REQUEST);
  h.settings.requestText = "  Add restore checks.\n";
  const result = (await h.invoke()).result;
  const submission = h.decisions[1].submission as { action: string; feedback: string };
  assert.equal(submission.action, "request_changes");
  assert.equal(submission.feedback, h.settings.requestText);
  assert.equal(result.type, "Block");
});

test("blank feedback, unknown choices, no-op edit, and cancellation approve nothing", async () => {
  const blank = harness(REQUEST); blank.settings.requestText = "   "; await blank.invoke(); dismissed(blank);
  const unknown = harness("Approve"); await unknown.invoke(); dismissed(unknown);
  const noOp = harness(EDIT); await noOp.invoke();
  assert.deepEqual(noOp.decisions.map(d => d.outcome ?? (d.submission as { action: string })?.action), ["present", "edit"]);
  assert.equal(noOp.g.state, "pending");
  const cancel = harness(); cancel.settings.cancelAt = "Decision"; await cancel.invoke(); dismissed(cancel);
});

test("reject is a raw choice and never asks for auditor", async () => {
  const h = harness(REJECT); await h.invoke();
  assert.equal((h.decisions[1].submission as { action: string }).action, "reject");
  assert.ok(!h.calls.includes("Independent auditor"));
});

test("singleton Edit proposes an exception without asserting approval consent", async () => {
  const h = harness(EDIT);
  h.g.context.inventory.members = [author]; h.g.inventory_status = "singleton"; h.g.proposal.auditor = author;
  h.ctx.ui.confirm = async title => { h.calls.push(title); return !title.startsWith("FINAL CONFIRMATION"); };
  await h.invoke();
  const submission = h.decisions[1].submission as { action: string; allow_same_model: boolean; configuration: { allow_same_model: boolean } };
  assert.equal(submission.action, "edit");
  assert.equal(submission.allow_same_model, false);
  assert.equal(submission.configuration.allow_same_model, true);
  assert.equal(h.g.state, "pending");
  assert.ok(h.calls.includes("SAME MODEL — proposal only"));
  assert.ok(!h.calls.includes("SAME MODEL — LOWERED INDEPENDENCE"));
});

test("singleton requires fresh positive consent even when the exception was proposed", async () => {
  for (const proposed of [false, true]) {
    const h = harness(); h.g.context.inventory.members = [author]; h.g.inventory_status = "singleton"; h.g.proposal.auditor = author;
    h.g.proposal.allow_same_model = proposed;
    h.ctx.ui.confirm = async title => !title.includes("SAME MODEL");
    await h.invoke(); dismissed(h);
    assert.equal(h.g.proposal.allow_same_model, proposed);
  }
});

test("prompt errors and expiry keep typed failure without consent", async () => {
  const h = harness(); h.g.prompt_error = "governance.interaction_limit";
  const result = (await h.invoke()).result; assert.equal(result.type, "Block"); assert.deepEqual(h.decisions, []);
  const original = Date.now; let clock = 0; Date.now = () => clock;
  try { const expired = harness(); expired.settings.onPresent = () => { clock += 1_500_001; }; await expired.invoke(); dismissed(expired); }
  finally { Date.now = original; }
});

test("budget parser rejects coercions and accepts bounded decimal", async () => {
  for (const value of ["6.0", "+6", "1e1", "1025", "0"]) {
    const h = harness(EDIT); h.settings.inputValue = value; await h.invoke(); dismissed(h);
  }
  const valid = harness(EDIT); valid.ctx.ui.input = async title => title.startsWith("Investigation passes") ? "6" : "";
  await valid.invoke(); assert.equal(valid.g.proposal.budgets.max_passes, 6);
});

test("timeout and escaping remain bounded and reversible", () => {
  const prior = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  try {
    for (const value of ["", "0", "1501", "NaN", "Infinity", "invalid"]) {
      process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = value; assert.equal(governanceTimeout(), 900_000);
    }
    process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = "1"; assert.equal(governanceTimeout(), 1000);
  } finally { if (prior === undefined) delete process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS; else process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = prior; }
  assert.equal(safeGovernanceText("\u061c"), "\\u061c");
  assert.notEqual(safeGovernanceText("\u061c"), safeGovernanceText("\\u061c"));
});
