// Fast UI-control tests. The fake ingress records decisions; it does NOT prove core policy.
// Real CAS/restore/replay/consent transitions remain covered by the Python governance suites.
import { test } from "node:test";
import assert from "node:assert/strict";
import { govern, governanceTimeout, readableGovernance, safeGovernanceText } from "../src/governance-ui.ts";
import type { PrivateIngress } from "../src/private-transport.ts";
import { fakeCtx } from "./fakes.ts";

const APPROVE = "Approve current displayed proposal";
const EDIT = "Edit configuration for another review";
const REQUEST = "Request changes in plain language";
const author = { provider_id: "anthropic", model_id: "claude-sonnet-4-6" };
const auditor = { provider_id: "anthropic", model_id: "claude-opus-4-6" };
function harness(choice = APPROVE) {
  const g = {
    state: "pending", control_mode: "deliberative", proposal_digest: "sha256:" + "a".repeat(64),
    plan_revision: 2, revision_limit: 64, inventory_status: "multiple", prompt_error: null,
    interactions_remaining: { proposal: 3, total: 128 }, change_request: null,
    proposal: { budgets: { max_passes: 8, max_spawns: 0, max_audit_spawns: 1 },
      modes: { multi_provider: false, cli_exec: false }, auditor, allow_same_model: false },
    budgets: { passes_used: 2, spawns_used: 0, audit_spawns_used: 0 },
    scope: { root: "G0", claims: [{ id: "G0", text: "scope", kind: "ordinary", gating: true }],
      edges: [] as Array<{ from: string; to: string; type: string }> },
    context: { inventory: { members: [author, auditor], source: "pi_registry", complete: true, authorized: true },
      author, ingress: "pi_ui" },
  };
  const run = { id: "ui-unit-run", status: "active", goal: "goal", governance: g };
  const calls: string[] = [];
  const decisions: Array<Record<string, unknown>> = [];
  const h = { g, run, calls, decisions, cancelAt: "", inputValue: "", requestText: "Please revise scope.", onPresent: () => {} };
  const ctx = fakeCtx();
  ctx.hasUI = true;
  ctx.ui.confirm = async title => { calls.push(title); return title !== h.cancelAt; };
  ctx.ui.select = async (title, options) => {
    calls.push(title);
    if (title === h.cancelAt) return undefined;
    if (title === "Decision") return choice;
    if (title.startsWith("Independent auditor")) return options.at(-1);
    return "Disabled";
  };
  ctx.ui.input = async title => {
    calls.push(title);
    if (title === h.cancelAt) return undefined;
    return title.startsWith("What must change?") ? h.requestText : h.inputValue;
  };
  const trusted: PrivateIngress = async request => {
    if (request.operation === "governance_decision") {
      const payload = request.payload as Record<string, unknown>;
      decisions.push(structuredClone(payload));
      if (payload.outcome === "present") h.onPresent();
    }
    return { protocol: "empirica/v2", request_id: "trusted-governance",
      result: { type: "Allow", converged: false, run } };
  };
  return { ...h, settings: h, ctx, trusted, invoke: (signal?: AbortSignal) => govern(run.id, ctx, trusted, signal) };
}

function dismissed(h: ReturnType<typeof harness>) {
  assert.deepEqual(h.decisions.map(d => d.outcome), ["present", "dismiss"]);
}

test("timeout override remains finite and bounded", () => {
  const prior = process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
  try {
    for (const value of ["", "0", "1501", "NaN", "Infinity", "invalid"]) {
      process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = value;
      assert.equal(governanceTimeout(), 900_000);
    }
    process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = "1";
    assert.equal(governanceTimeout(), 1000);
  } finally {
    if (prior === undefined) delete process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS;
    else process.env.EMPIRICA_GOVERNANCE_TIMEOUT_SECONDS = prior;
  }
});

test("blank feedback and unknown mode labels dismiss without consent", async () => {
  const request = harness(REQUEST);
  request.settings.requestText = "   ";
  await request.invoke();
  dismissed(request);
  const edit = harness(EDIT);
  edit.ctx.ui.select = async title => title === "Decision" ? EDIT : "Yes";
  await edit.invoke();
  dismissed(edit);
});

test("reject does not ask for an auditor selection", async () => {
  const h = harness("Reject proposal");
  await h.invoke();
  assert.deepEqual(h.decisions.map(d => d.outcome), ["present", "reject"]);
  assert.ok(!h.calls.some(title => title.startsWith("Independent auditor")));
});

test("no-op Edit never grants approval; explicit Approve does", async () => {
  const edit = harness(EDIT);
  await edit.invoke();
  dismissed(edit);
  const approve = harness();
  await approve.invoke();
  assert.deepEqual(approve.decisions.map(d => d.outcome), ["present", "approve"]);
});

test("reservation latency or abort cannot display an expired dialog", async () => {
  const original = Date.now;
  let clock = 1000;
  Date.now = () => clock;
  try {
    for (const abort of [false, true]) {
      const h = harness();
      const controller = new AbortController();
      h.settings.onPresent = () => { if (abort) controller.abort(); else clock += 1_500_001; };
      await h.invoke(controller.signal);
      assert.deepEqual(h.calls, []);
      dismissed(h);
    }
  } finally { Date.now = original; }
});

test("each edit/request cancellation ends the flow without later dialogs or consent", async () => {
  const cases: Array<[string, string, string]> = [
    [EDIT, "Empirica scope review — revision 2 of at most 64", ""],
    [EDIT, "Decision", ""],
    [EDIT, "Investigation passes — proposed 8, already used 2. Empty keeps current.", ""],
    [EDIT, "Child spawns — proposed 0, already used 0. Empty keeps current.", ""],
    [EDIT, "Audit spawns — proposed 1, already used 0. Empty keeps current.", ""],
    [EDIT, "Cross-provider actors — currently Disabled", ""],
    [EDIT, "External model/actor CLI use — currently Disabled", ""],
    [EDIT, "Independent auditor (authorized configured inventory)", ""],
    [EDIT, "Submit edits for another review?", "6"],
    [REQUEST, "What must change? Plain language; this approves nothing.", ""],
    [REQUEST, "Send this request to the author?", ""],
  ];
  for (const [choice, cancelAt, inputValue] of cases) {
    const h = harness(choice);
    Object.assign(h.settings, { cancelAt, inputValue });
    await h.invoke();
    assert.equal(h.calls.at(-1), cancelAt);
    dismissed(h);
  }
});

test("UI budget validation rejects non-integer forms and honors nonzero usage", async () => {
  for (const value of ["1", "6.0", "+6", "1e1", "1025", "0"]) {
    const invalid = harness(EDIT);
    invalid.settings.inputValue = value;
    await invalid.invoke();
    dismissed(invalid);
  }
  const valid = harness(EDIT);
  valid.ctx.ui.input = async title => title.startsWith("Investigation passes") ? "6" : "";
  await valid.invoke();
  const decision = valid.decisions.at(-1)!;
  assert.equal(decision.outcome, "amend");
  const amendment = decision.amendment as { configuration: typeof valid.g.proposal };
  assert.deepEqual(amendment.configuration.budgets, { max_passes: 6, max_spawns: 0, max_audit_spawns: 1 });
  assert.equal(valid.g.budgets.passes_used, 2);
});

test("unknown choices grant nothing and reversible mode edits are explicit", async () => {
  const unknown = harness("Approve");
  await unknown.invoke();
  dismissed(unknown);

  for (const [current, answer] of [[false, "Enabled"], [true, "Disabled"]] as const) {
    const h = harness(EDIT);
    h.g.proposal.modes = { multi_provider: current, cli_exec: current };
    h.ctx.ui.select = async (title, options) => {
      if (title === "Decision") return EDIT;
      if (title.startsWith("Independent auditor")) return options.at(-1);
      return answer;
    };
    await h.invoke();
    const amendment = h.decisions.at(-1)!.amendment as { configuration: typeof h.g.proposal };
    assert.deepEqual(amendment.configuration.modes, {
      multi_provider: !current, cli_exec: !current,
    });
  }
});

test("singleton warning requires a fresh affirmative response", async () => {
  const h = harness();
  h.g.context.inventory.members = [author];
  h.g.inventory_status = "singleton";
  h.g.proposal.auditor = author;
  let warnings = 0;
  h.ctx.ui.confirm = async title => {
    if (title.includes("SAME MODEL")) { warnings++; return false; }
    return true;
  };
  await h.invoke();
  assert.equal(warnings, 1);
  assert.ok(!h.decisions.some(d => d.outcome === "approve"));
  dismissed(h);
});

test("human request text remains exact and its response gives actionable guidance", async () => {
  const h = harness(REQUEST);
  h.settings.requestText = "  Add restore checks.\n";
  const result = (await h.invoke()).result;
  assert.equal(h.decisions.at(-1)!.change_request, h.settings.requestText);
  assert.equal(result.type, "Block");
  if (result.type === "Block") assert.match(result.reasons[0].message!, /run\.governance\.change_request/);
});

test("bidi controls and auditor options are visibly escaped, never terminal controls", async () => {
  assert.equal(safeGovernanceText("\u061c"), "\\u061c");
  assert.notEqual(safeGovernanceText("\u061c"), safeGovernanceText("\\u061c"));
  const h = harness(EDIT);
  h.g.context.inventory.members.push({ provider_id: "unknown", model_id: "private\x1b[2J\u061c" });
  let labels: string[] = [];
  h.ctx.ui.select = async (title, options) => {
    if (title.startsWith("Independent auditor")) { labels = options; return undefined; }
    return title === "Decision" ? EDIT : "Disabled";
  };
  await h.invoke();
  assert.equal(labels.length, 3);
  assert.ok(labels.every(label => !/[\x00-\x1f\u061c]/u.test(label)));
  assert.match(labels[2], /\\x1b\[2J\\u061c/);
});

test("maximum-size scope preserves each complete claim, edge, and material context", () => {
  const h = harness();
  h.g.scope.claims = Array.from({ length: 32 }, (_, n) => ({ id: `C${n}`,
    text: `claim-${n} ` + "中".repeat(2038), kind: "needs-experiment", gating: n % 2 === 0 }));
  h.g.scope.root = "C0";
  h.g.scope.edges = h.g.scope.claims.flatMap((c, i) => h.g.scope.claims.slice(i + 1)
    .map(d => ({ from: c.id, to: d.id, type: "SupportedBy" }))).slice(0, 128);
  const text = readableGovernance(h.run);
  for (const claim of h.g.scope.claims) assert.ok(text.includes(claim.text));
  for (const edge of h.g.scope.edges) assert.ok(text.includes(`${edge.from} ${edge.type} ${edge.to}`));
  for (const value of [h.run.goal, "C0", author.model_id, auditor.model_id, "pi_registry", "pi_ui",
    h.g.proposal_digest, "64", "8", "2", "false", "complete=true", "authorized=true"]) assert.ok(text.includes(value), value);
});
