// Pure translation tests: request builder semantics, invocation parsing, gate
// decisions, notices, and the subagent classifier. No core, no bridge — only
// the pure functions in translate.ts.
//
// Builder structural shape and schema conformance are proven by parity.test.ts
// (lexical projection + emitted-builder jsonschema). This file retains only the
// SEMANTICS the schema cannot prove: optional-field omission/default behaviour,
// invocation-flag parsing, gate decision mapping, and notice text/type.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as path from "node:path";

import {
  startRunRequest,
  splitLeadingFlags,
  parseInvocationFlags,
  gateFromDecision,
  convergenceNotice,
  statusNotice,
  startRunNotice,
  MANAGEMENT_ACTIONS,
  classifySubagentCall,
  REPORT_CONVERGENCE_INTENT,
  REPORT_CONVERGENCE_TOOL,
  type SubagentCallKind,
} from "../src/translate.ts";
import { loadInventory, type SubagentInventory } from "../src/subagent-inventory.ts";
import { type Result } from "../src/contract.ts";

const SEL = { project: "p", session: "s" };
const RID = "rid-1";
const INVOCATION = { host: "pi", interactive: true, signal: "ctx.mode=tui", delegation: false };
const RUN = { id: "h", status: "active" as const };
const SPLIT_CASES = path.resolve(path.dirname(fileURLToPath(import.meta.url)),
  "../../../../../contracts/empirica/v2/invocation-split-cases.json");

// --- request builder semantics (omission/default — schema cannot prove) -----

test("startRunRequest omits budgets when not supplied (omission semantics)", () => {
  const req = startRunRequest(SEL, "g", RID, INVOCATION);
  if (req.command.type === "StartRun") {
    assert.equal("budgets" in req.command, false, "budgets must be absent, not undefined");
  }
});

test("startRunRequest carries budgets only when supplied", () => {
  const req = startRunRequest(SEL, "g", RID, INVOCATION, {
    maxPasses: 3, maxSpawns: 1, maxAuditSpawns: 2,
  });
  if (req.command.type === "StartRun") {
    assert.deepEqual(req.command.budgets,
      { max_passes: 3, max_spawns: 1, max_audit_spawns: 2 });
  }
});

// --- invocation flags --------------------------------------------------------

test("splitLeadingFlags matches the shared Python host split table", () => {
  const cases = JSON.parse(readFileSync(SPLIT_CASES, "utf8")) as
    Array<{ args: string; flags: string[]; goal: string }>;
  for (const row of cases) {
    assert.deepEqual(splitLeadingFlags(row.args), { flags: row.flags, goal: row.goal }, row.args);
  }
});

test("removed flags surface as unknown", () => {
  assert.deepEqual(parseInvocationFlags("--cli-exec --multi-provider goal words"), {
    goal: "goal words", unknownFlags: ["--cli-exec", "--multi-provider"],
  });
});

test("auto is the only recognized invocation flag", () => {
  assert.deepEqual(parseInvocationFlags("--auto goal"), {
    goal: "goal", unknownFlags: [], controlMode: "auto",
  });
  assert.equal(parseInvocationFlags("goal --auto").controlMode, undefined);
});

test("parseInvocationFlags preserves empty and verbatim goals", () => {
  assert.deepEqual(parseInvocationFlags(""), { goal: "", unknownFlags: [] });
  assert.equal(parseInvocationFlags("  exact goal  ").goal, "  exact goal  ");
});

// --- gateFromDecision (table-driven) -----------------------------------------

const GATE_CASES: Array<{ label: string; result: Result; kind: "permit" | "deny"; reason?: string }> = [
  { label: "Allow converged permits", result: { type: "Allow", converged: true, run: { ...RUN, status: "converged" } }, kind: "permit" },
  { label: "Allow not-converged permits", result: { type: "Allow", converged: false, run: RUN }, kind: "permit" },
  { label: "Block denies with first reason message", result: { type: "Block", run: RUN, reasons: [{ code: "claim.research_missing", message: "evidence owed" }] }, kind: "deny", reason: "evidence owed" },
  { label: "Block denies with code when no message", result: { type: "Block", run: RUN, reasons: [{ code: "audit.required" }] }, kind: "deny", reason: "audit.required" },
  { label: "Inert denies (run gone but handle exists)", result: { type: "Inert", reason: "no_run" }, kind: "deny", reason: "no active run to report" },
  { label: "closed Fault denies", result: { type: "Fault", code: "corrupt_run", fail_direction: "closed" }, kind: "deny" },
  { label: "open Fault denies", result: { type: "Fault", code: "unavailable", fail_direction: "open" }, kind: "deny" },
];

for (const c of GATE_CASES) {
  test(`gateFromDecision: ${c.label}`, () => {
    const d = gateFromDecision(c.result);
    assert.equal(d.kind, c.kind);
    if (d.kind === "deny" && c.reason !== undefined) assert.equal(d.reason, c.reason);
  });
}

// --- convergenceNotice / statusNotice (table-driven) -------------------------

const NOTICE_CASES: Array<{
  label: string;
  fn: (r: Result) => { type: string; text: string };
  result: Result;
  type: string;
  patterns: RegExp[];
}> = [
  { label: "convergenceNotice: Allow converged is info", fn: convergenceNotice, result: { type: "Allow", converged: true, run: { ...RUN, status: "converged" } }, type: "info", patterns: [/converged/] },
  { label: "convergenceNotice: Block is error with reason", fn: convergenceNotice, result: { type: "Block", run: RUN, reasons: [{ code: "audit.required", message: "audit owed" }] }, type: "error", patterns: [/audit owed/] },
  { label: "convergenceNotice: Inert is info", fn: convergenceNotice, result: { type: "Inert", reason: "no_run" }, type: "info", patterns: [/no active run/] },
  { label: "convergenceNotice: Fault is error", fn: convergenceNotice, result: { type: "Fault", code: "unavailable", fail_direction: "closed" }, type: "error", patterns: [/unavailable/] },
  { label: "statusNotice: Allow reports id+status", fn: statusNotice, result: { type: "Allow", converged: false, run: { id: "h", status: "active" } }, type: "info", patterns: [/h.*active/] },
  { label: "statusNotice: Inert reports no active run", fn: statusNotice, result: { type: "Inert", reason: "no_run" }, type: "info", patterns: [/no active run/] },
];

for (const c of NOTICE_CASES) {
  test(`notice: ${c.label}`, () => {
    const n = c.fn(c.result);
    assert.equal(n.type, c.type);
    for (const p of c.patterns) assert.match(n.text, p);
  });
}

test("REPORT_CONVERGENCE_TOOL and INTENT constants are correct", () => {
  assert.equal(REPORT_CONVERGENCE_TOOL, "report_convergence");
  assert.equal(REPORT_CONVERGENCE_INTENT, "report_convergence");
});

// --- startRunNotice (table-driven, truthful D6 UX) ---------------------------

const START_NOTICE_CASES: Array<{
  label: string;
  result: Result;
  type: "info" | "warning" | "error";
  patterns: RegExp[];
}> = [
  { label: "Allow active is info with D6 attempt and id", result: { type: "Allow", converged: false, run: { id: "r1", status: "active" } }, type: "info", patterns: [/D6 StartRun attempt/, /r1/, /active/] },
  { label: "Allow converged notes already converged", result: { type: "Allow", converged: true, run: { id: "r2", status: "converged" } }, type: "info", patterns: [/already converged/] },
  { label: "Fault is error with D6 attempt, could-not-start, and actual code", result: { type: "Fault", code: "unsupported", fail_direction: "closed" }, type: "error", patterns: [/D6 StartRun attempt could not start/, /unsupported/] },
  { label: "Block is warning with D6 attempt", result: { type: "Block", run: RUN, reasons: [{ code: "x", message: "denied" }] }, type: "warning", patterns: [/D6 StartRun attempt blocked/] },
  { label: "Inert is warning with D6 attempt", result: { type: "Inert", reason: "no_run" }, type: "warning", patterns: [/D6 StartRun attempt/] },
];

for (const c of START_NOTICE_CASES) {
  test(`startRunNotice: ${c.label}`, () => {
    const n = startRunNotice(c.result);
    assert.equal(n.type, c.type);
    for (const p of c.patterns) assert.match(n.text, p);
  });
}

// --- classifySubagentCall (table-driven D4 classifier) ------------------------
//
// Each row is proven for every reviewed inventory it names: the expectation differs by version only
// where the package's surface differs (0.74 removed workflowScript/workflowScriptPath and made
// `workflow` boolean|path|name; 0.50 has a top-level `resume` and no `validate`/`lane.status`).

const inventory = (version: string): SubagentInventory => {
  const found = loadInventory(version);
  assert.ok(found, `no reviewed inventory for ${version}`);
  return found;
};
const V050 = "0.50.0", V064 = "0.64.0", V074 = "0.74.0", V075 = "0.75.0";
const ALL = [V050, V064, V074, V075];

const CLASSIFY_CASES: Array<{
  label: string; input: unknown; expected: Partial<Record<string, SubagentCallKind>> | SubagentCallKind;
}> = [
  { label: "agent + task launch", input: { agent: "scout", task: "look" }, expected: "executable" },
  { label: "agent alone", input: { agent: "empirica.empirica-auditor" }, expected: "executable" },
  { label: "agent with a null action", input: { agent: "scout", action: null }, expected: "executable" },
  { label: "workflow: true", input: { workflow: true }, expected: { [V050]: "malformed", [V064]: "executable", [V074]: "executable", [V075]: "executable" } },
  { label: "workflow by path", input: { workflow: "./w/review.js" }, expected: { [V050]: "malformed", [V064]: "executable", [V074]: "executable", [V075]: "executable" } },
  { label: "workflow by name", input: { workflow: "run-ci", args: { command: "npm test" } }, expected: { [V050]: "malformed", [V064]: "executable", [V074]: "executable", [V075]: "executable" } },
  { label: "workflowScript (removed in 0.74)", input: { workflowScript: "return 1" }, expected: { [V050]: "executable", [V064]: "executable", [V074]: "malformed", [V075]: "malformed" } },
  { label: "workflowScriptPath (removed in 0.74)", input: { workflowScriptPath: "w.js" }, expected: { [V050]: "malformed", [V064]: "executable", [V074]: "malformed", [V075]: "malformed" } },
  { label: "top-level resume (0.50 only)", input: { resume: "child-1" }, expected: { [V050]: "executable", [V064]: "malformed", [V074]: "malformed", [V075]: "malformed" } },
  { label: "agent + workflow is two launch forms", input: { agent: "x", workflow: true }, expected: { [V050]: "executable", [V064]: "malformed", [V074]: "malformed", [V075]: "malformed" } },
  { label: "agent + workflowScript is two launch forms", input: { agent: "x", workflowScript: "y" }, expected: { [V050]: "malformed", [V064]: "malformed", [V074]: "executable", [V075]: "executable" } },
  { label: "agent + resume (0.50)", input: { agent: "x", resume: "c" }, expected: { [V050]: "malformed", [V064]: "executable", [V074]: "executable", [V075]: "executable" } },
  { label: "null launch forms do not count", input: { agent: "x", workflow: null, resume: null }, expected: "executable" },
  { label: "empty input", input: {}, expected: "malformed" },
  { label: "input that is not an object", input: undefined, expected: "malformed" },
  { label: "null input", input: null, expected: "malformed" },
  { label: "array input", input: [{ agent: "x" }], expected: "malformed" },
  { label: "string input", input: "agent", expected: "malformed" },
  { label: "unknown field only (chain/tasks are not launch forms)", input: { tasks: [{ agent: "x", task: "y" }] }, expected: "malformed" },
  // --- management: the reviewed read-only allowlist
  { label: "action list", input: { action: "list" }, expected: "management" },
  { label: "action status with an id", input: { action: "status", id: "run-1" }, expected: "management" },
  { label: "action models", input: { action: "models" }, expected: "management" },
  { label: "action guide", input: { action: "guide", topic: "tool-reference" }, expected: "management" },
  { label: "action doctor", input: { action: "doctor" }, expected: "management" },
  { label: "action children.list", input: { action: "children.list" }, expected: "management" },
  { label: "action project.status", input: { action: "project.status" }, expected: "management" },
  { label: "action lane.status (absent in 0.50)", input: { action: "lane.status", laneId: "l" }, expected: { [V050]: "unsupported", [V064]: "management", [V074]: "management", [V075]: "management" } },
  { label: "action watchdog.status", input: { action: "watchdog.status" }, expected: "management" },
  { label: "action inspector.status", input: { action: "inspector.status" }, expected: "management" },
  { label: "action refine.show takes its agent as target", input: { action: "refine.show", agent: "reviewer" }, expected: "management" },
  { label: "action list with surrounding whitespace", input: { action: " list " }, expected: "management" },
  // A field the version's schema no longer declares is not a launch form for it (the executor rejects it).
  { label: "validate a workflow script (offline; absent in 0.50)", input: { action: "validate", workflowScript: "return 1" },
    expected: { [V050]: "unsupported", [V064]: "management", [V074]: "management", [V075]: "management" } },
  { label: "validate workflow: true (0.74 form)", input: { action: "validate", workflow: true },
    expected: { [V050]: "unsupported", [V064]: "management", [V074]: "management", [V075]: "management" } },
  { label: "validate a script path", input: { action: "validate", workflowScriptPath: "w.js" },
    expected: { [V050]: "unsupported", [V064]: "management", [V074]: "management", [V075]: "management" } },
  // --- a launch form beside an action is ambiguous
  { label: "list beside an agent (not a target-taking action)", input: { action: "list", agent: "x" }, expected: "malformed" },
  { label: "validate beside an agent", input: { action: "validate", agent: "x", workflow: true }, expected: { [V050]: "unsupported", [V064]: "malformed", [V074]: "malformed", [V075]: "malformed" } },
  { label: "status beside workflow", input: { action: "status", workflow: true }, expected: { [V050]: "management", [V064]: "malformed", [V074]: "malformed", [V075]: "malformed" } },
  { label: "refine.show beside a workflow", input: { action: "refine.show", agent: "x", workflow: "w" }, expected: { [V050]: "management", [V064]: "malformed", [V074]: "malformed", [V075]: "malformed" } },
  { label: "status beside the 0.50 top-level resume", input: { action: "status", resume: "c" }, expected: { [V050]: "malformed", [V064]: "management", [V074]: "management", [V075]: "management" } },
  // --- everything else is unsupported, never inert
  ...["steer", "stop", "interrupt", "resume", "reset", "schedule.create", "schedule.run-due", "mission.create", "mission.close",
    "worktree.discard", "worktree.cleanup", "project.open", "project.close", "inspector.open", "inspector.command",
    "watchdog.configure", "watchdog.check", "watchdog.recommend-model", "create", "update", "delete", "eject", "disable",
    "enable", "refine", "refine.rollback", "debug.run", "grant-spawn-budget", "dismiss", "get", "lane.recordMerge",
    "command.yield"].map((action) => ({ label: `action ${action}`, input: { action },
    expected: { [V050]: "unsupported", [V064]: "unsupported", [V074]: "unsupported", [V075]: "unsupported" } as Record<string, SubagentCallKind> })),
  { label: "resume as an action spawns a follow-up child", input: { action: "resume", id: "r", message: "go" }, expected: "unsupported" },
  { label: "unknown action", input: { action: "frobnicate" }, expected: "unsupported" },
  { label: "action spelled in another case", input: { action: "LIST" }, expected: "unsupported" },
  { label: "action from a future version", input: { action: "swarm.start" }, expected: "unsupported" },
  { label: "empty action", input: { action: "" }, expected: "malformed" },
  { label: "blank action", input: { action: "   " }, expected: "malformed" },
  { label: "numeric action", input: { action: 5 }, expected: "malformed" },
  { label: "object action", input: { action: { kind: "list" } }, expected: "malformed" },
];

for (const row of CLASSIFY_CASES) {
  test(`classifySubagentCall: ${row.label}`, () => {
    for (const version of ALL) {
      const expected = typeof row.expected === "string" ? row.expected : row.expected[version];
      assert.ok(expected, `${version} has no expectation`);
      const classified = classifySubagentCall(row.input, inventory(version));
      assert.equal(classified.kind, expected, `${version}: ${classified.detail}`);
      assert.ok(classified.detail.length > 0);
    }
  });
}

test("classifySubagentCall: every inventory action is management iff it is in the reviewed allowlist", () => {
  for (const version of ALL) {
    const inv = inventory(version);
    assert.ok(inv.actions.length > 40, version);
    for (const action of inv.actions) {
      const kind = classifySubagentCall({ action }, inv).kind;
      const expected = MANAGEMENT_ACTIONS.has(action) && !(action === "validate" && !inv.supports.validateOffline)
        ? "management" : "unsupported";
      assert.equal(kind, expected, `${version} ${action}`);
    }
  }
});

test("classifySubagentCall: the reviewed allowlist is exactly the read-only set, each entry in some inventory", () => {
  assert.deepEqual([...MANAGEMENT_ACTIONS.keys()].sort(), ["children.list", "doctor", "guide", "inspector.status", "lane.status",
    "list", "models", "project.status", "refine.show", "status", "validate", "watchdog.status"]);
  for (const action of MANAGEMENT_ACTIONS.keys())
    assert.ok(ALL.some((version) => inventory(version).actions.includes(action)), action);
});

test("classifySubagentCall: validate is management only where the version documents it as offline", () => {
  const offline: SubagentInventory = { ...inventory(V075), supports: { ...inventory(V075).supports, validateOffline: false } };
  assert.equal(classifySubagentCall({ action: "validate", workflow: true }, inventory(V075)).kind, "management");
  const refused = classifySubagentCall({ action: "validate", workflow: true }, offline);
  assert.equal(refused.kind, "unsupported");
  assert.match(refused.detail, /does not document validate as offline/);
});

test("classifySubagentCall: an action beyond the allowlist is unsupported even if the inventory lists it", () => {
  const widened: SubagentInventory = { ...inventory(V075), actions: [...inventory(V075).actions, "frobnicate"] };
  assert.equal(classifySubagentCall({ action: "frobnicate" }, widened).kind, "unsupported");
  assert.match(classifySubagentCall({ action: "frobnicate" }, widened).detail, /allowlist/);
  assert.match(classifySubagentCall({ action: "frobnicate" }, inventory(V075)).detail, /inventory/);
});

test("classifySubagentCall: the launch forms come from the inventory, not from a built-in list", () => {
  const custom: SubagentInventory = { ...inventory(V075), launch_forms: ["agent"] };
  assert.equal(classifySubagentCall({ workflow: true }, custom).kind, "malformed");
  assert.equal(classifySubagentCall({ workflow: true }, inventory(V075)).kind, "executable");
});
