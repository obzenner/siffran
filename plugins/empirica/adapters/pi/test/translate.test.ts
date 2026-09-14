// Pure translation tests: request builder semantics, mode parsing, gate
// decisions, notices, and the subagent classifier. No core, no bridge — only
// the pure functions in translate.ts.
//
// Builder structural shape and schema conformance are proven by parity.test.ts
// (lexical projection + emitted-builder jsonschema). This file retains only the
// SEMANTICS the schema cannot prove: optional-field omission/default behaviour,
// mode-flag parsing, gate decision mapping, and notice text/type.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  startRunRequest,
  parseModeFlags,
  gateFromDecision,
  convergenceNotice,
  statusNotice,
  startRunNotice,
  isExecutableSubagentLaunch,
  subagentUnsupportedReason,
  SUBAGENT_TOOL,
  REPORT_CONVERGENCE_INTENT,
  REPORT_CONVERGENCE_TOOL,
} from "../src/translate.ts";
import { type Result } from "../src/contract.ts";

const SEL = { project: "p", session: "s" };
const RID = "rid-1";
const RUN = { id: "h", status: "active" as const };

// --- request builder semantics (omission/default — schema cannot prove) -----

test("startRunRequest omits budgets and modes when not supplied (omission semantics)", () => {
  const req = startRunRequest(SEL, "g", RID);
  if (req.command.type === "StartRun") {
    assert.equal("budgets" in req.command, false, "budgets must be absent, not undefined");
    assert.equal("modes" in req.command, false, "modes must be absent, not undefined");
  }
});

test("startRunRequest carries budgets and modes only when supplied", () => {
  const req = startRunRequest(SEL, "g", RID, { maxPasses: 3, maxSpawns: 1, modes: { cli_exec: true } });
  if (req.command.type === "StartRun") {
    assert.deepEqual(req.command.budgets, { max_passes: 3, max_spawns: 1 });
    assert.deepEqual(req.command.modes, { cli_exec: true });
  }
});

// --- mode flags --------------------------------------------------------------

test("parseModeFlags surfaces unknown leading flags", () => {
  assert.deepEqual(parseModeFlags("--cli-exec --wat goal words"), {
    goal: "goal words", modes: { cli_exec: true }, unknownFlags: ["--wat"],
  });
});

test("parseModeFlags parses multi-provider and no- variants", () => {
  assert.deepEqual(parseModeFlags("--multi-provider --no-cli-exec g"), {
    goal: "g", modes: { multi_provider: true, cli_exec: false }, unknownFlags: [],
  });
});

test("parseModeFlags empty args yields empty modes and goal", () => {
  assert.deepEqual(parseModeFlags(""), { goal: "", modes: {}, unknownFlags: [] });
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

// --- isExecutableSubagentLaunch (table-driven D8 classifier) -----------------

const SUBAGENT_CASES: Array<{
  label: string;
  toolName: string;
  input: Record<string, unknown> | undefined;
  expected: boolean;
}> = [
  { label: "agent launch is executable", toolName: SUBAGENT_TOOL, input: { agent: "empirica:empirica-auditor" }, expected: true },
  { label: "workflowScript launch is executable", toolName: SUBAGENT_TOOL, input: { workflowScript: "audit-flow.ts" }, expected: true },
  { label: "resume launch is executable", toolName: SUBAGENT_TOOL, input: { resume: "child-1" }, expected: true },
  { label: "management list is NOT executable (inert)", toolName: SUBAGENT_TOOL, input: { action: "list" }, expected: false },
  { label: "management status is NOT executable (inert)", toolName: SUBAGENT_TOOL, input: { action: "status" }, expected: false },
  { label: "malformed multi-key launch is NOT executable (inert)", toolName: SUBAGENT_TOOL, input: { agent: "x", workflowScript: "y" }, expected: false },
  { label: "empty input {} is NOT executable", toolName: SUBAGENT_TOOL, input: {}, expected: false },
  { label: "undefined input is NOT executable", toolName: SUBAGENT_TOOL, input: undefined, expected: false },
  { label: "non-subagent tool is NOT executable", toolName: "bash", input: { agent: "x" }, expected: false },
];

for (const c of SUBAGENT_CASES) {
  test(`isExecutableSubagentLaunch: ${c.label}`, () => {
    assert.equal(isExecutableSubagentLaunch(c.toolName, c.input), c.expected);
  });
}

test("subagentUnsupportedReason is D8-owned and names the profile", () => {
  const reason = subagentUnsupportedReason();
  assert.match(reason, /D8/);
  assert.match(reason, /pi@0.84.1/);
  assert.match(reason, /foreground_only/);
});
