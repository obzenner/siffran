// Proves the translation both directions: Pi invocation -> empirica/v1 Request
// (checked against the shared contract fixture), and Result -> host-neutral gate /
// notice outcomes.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { PROTOCOL, type Result } from "../src/contract.ts";
import {
  REPORT_CONVERGENCE_INTENT,
  convergenceNotice,
  evaluateRunRequest,
  gateFromDecision,
  getRunRequest,
  settledFollowUp,
  startRunRequest,
  statusNotice,
} from "../src/translate.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..", "..");
const FIXTURE = path.join(REPO_ROOT, "contracts", "fixtures", "empirica-block-audit.json");

// --- invocation -> request ---------------------------------------------------

test("startRunRequest builds a StartRun envelope with the selector and goal", () => {
  const request = startRunRequest(
    { project: "p", session: "s" },
    "make the widget converge",
    "req-1",
  );
  assert.equal(request.protocol, PROTOCOL);
  assert.equal(request.request_id, "req-1");
  assert.deepEqual(request.command, {
    type: "StartRun",
    selector: { project: "p", session: "s" },
    goal: "make the widget converge",
  });
});

test("startRunRequest carries optional passes/spawns/modes only when given", () => {
  const bare = startRunRequest({ project: "p", session: "s" }, "g", "r");
  assert.equal("max_passes" in bare.command, false);

  const rich = startRunRequest({ project: "p", session: "s" }, "g", "r", {
    maxPasses: 4,
    maxSpawns: null,
    modes: { multi_provider: true },
  });
  assert.deepEqual(rich.command, {
    type: "StartRun",
    selector: { project: "p", session: "s" },
    goal: "g",
    max_passes: 4,
    max_spawns: null,
    modes: { multi_provider: true },
  });
});

test("getRunRequest builds a GetRun envelope", () => {
  assert.deepEqual(getRunRequest("handle-x", "req-2").command, {
    type: "GetRun",
    run_id: "handle-x",
  });
});

test("evaluateRunRequest matches the shared contract fixture", () => {
  const fixture = JSON.parse(readFileSync(FIXTURE, "utf-8"));
  const request = evaluateRunRequest(
    fixture.request.command.run_id,
    fixture.request.command.intent,
    fixture.request.request_id,
  );
  // Byte-for-byte the substrate-neutral envelope every adapter is held to
  // (ADR-30: "Adapter suites must consume the same fixtures").
  assert.deepEqual(request, fixture.request);
  assert.equal(fixture.request.command.intent, REPORT_CONVERGENCE_INTENT);
});

// --- result -> gate ----------------------------------------------------------

test("the fixture's Block decision maps to a gate denial with its reason", () => {
  const fixture = JSON.parse(readFileSync(FIXTURE, "utf-8"));
  assert.deepEqual(gateFromDecision(fixture.expected.result), {
    kind: "deny",
    reason: "independent audit required",
  });
});

test("Allow permits, Inert permits (no run to gate)", () => {
  const allow: Result = {
    type: "Allow",
    converged: true,
    run: { id: "r", status: "converged", revision: 3 },
  };
  const inert: Result = { type: "Inert", reason: "no_run" };
  assert.deepEqual(gateFromDecision(allow), { kind: "permit" });
  assert.deepEqual(gateFromDecision(inert), { kind: "permit" });
});

test("a closed Fault denies; an open Fault permits", () => {
  const closed: Result = { type: "Fault", code: "corrupt_run", fail_direction: "closed" };
  const open: Result = { type: "Fault", code: "unavailable", fail_direction: "open" };
  assert.equal(gateFromDecision(closed).kind, "deny");
  assert.equal(gateFromDecision(open).kind, "permit");
});

// --- result -> notices -------------------------------------------------------

test("convergenceNotice: Allow(converged) is allowed, Block is an error", () => {
  const allow = convergenceNotice({
    type: "Allow",
    converged: true,
    run: { id: "r", status: "converged", revision: 1 },
  });
  assert.equal(allow.type, "info");
  assert.match(allow.text, /converged/);

  const block = convergenceNotice({
    type: "Block",
    reason: "two claims lack evidence",
    run: { id: "r", status: "active", revision: 2 },
  });
  assert.equal(block.type, "error");
  assert.match(block.text, /two claims lack evidence/);
});

test("statusNotice reports id/status/revision, and 'no run' for Inert", () => {
  const notice = statusNotice({
    type: "Allow",
    converged: false,
    run: { id: "abc", status: "active", revision: 5 },
  });
  assert.match(notice.text, /abc/);
  assert.match(notice.text, /active/);
  assert.match(notice.text, /5/);

  assert.match(statusNotice({ type: "Inert", reason: "no_run" }).text, /no active run/);
});

// --- settled follow-up honesty ----------------------------------------------

test("settledFollowUp nudges only on Block, and labels itself not-a-gate", () => {
  const nudge = settledFollowUp({
    type: "Block",
    reason: "root claim unproven",
    run: { id: "r", status: "active", revision: 1 },
  });
  assert.ok(nudge);
  assert.match(nudge, /reminder, not a gate/);
  assert.match(nudge, /root claim unproven/);
});

test("settledFollowUp is silent on Allow, Inert, and Fault", () => {
  assert.equal(
    settledFollowUp({
      type: "Allow",
      converged: false,
      run: { id: "r", status: "active", revision: 1 },
    }),
    null,
  );
  assert.equal(settledFollowUp({ type: "Inert", reason: "no_run" }), null);
  assert.equal(
    settledFollowUp({ type: "Fault", code: "unavailable", fail_direction: "open" }),
    null,
  );
});
