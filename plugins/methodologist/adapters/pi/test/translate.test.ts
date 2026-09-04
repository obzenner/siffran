// Proves the translation both directions: Pi invocation -> methodologist/v1
// Request (checked against the shared contract fixture), and Result -> Pi UI.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { PROTOCOL, type Response } from "../src/contract.ts";
import {
  applyResult,
  parseThinkInvocation,
  selectMethodologyRequest,
} from "../src/translate.ts";
import { PiWidgetTaskTracker } from "../src/task-tracker.ts";
import { PiHumanPort } from "../src/human-port.ts";
import { HumanDismissed, UnsupportedByHost } from "../src/ports.ts";
import { createMethodologistExtension } from "../src/index.ts";
import { FakePi, FakeUi } from "./fakes.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..", "..");
const FIXTURE = path.join(
  REPO_ROOT,
  "contracts",
  "fixtures",
  "methodologist-select.json",
);

function deps(ui: FakeUi) {
  return { tracker: new PiWidgetTaskTracker(ui), human: new PiHumanPort(ui), ui };
}

// --- invocation -> request ---------------------------------------------------

test("bare /think asks the core to select (no requested methodology)", () => {
  const parsed = parseThinkInvocation("", ["invariant-analysis"]);
  assert.equal(parsed.requestedMethodology, null);
  assert.ok(parsed.intent.length > 0, "intent must be non-empty (schema minLength 1)");
});

test("/think <known-name> is parsed as an explicit request", () => {
  const parsed = parseThinkInvocation("Invariant-Analysis", ["invariant-analysis"]);
  assert.equal(parsed.requestedMethodology, "invariant-analysis"); // canonical case
  assert.equal(parsed.intent, "Invariant-Analysis");
});

test("free-text /think becomes intent with no requested methodology", () => {
  const parsed = parseThinkInvocation("why does the cache miss", ["invariant-analysis"]);
  assert.equal(parsed.requestedMethodology, null);
  assert.equal(parsed.intent, "why does the cache miss");
});

test("selectMethodologyRequest matches the shared contract fixture", () => {
  const fixture = JSON.parse(readFileSync(FIXTURE, "utf-8"));
  const request = selectMethodologyRequest(
    fixture.request.command.intent,
    fixture.request.command.requested_methodology,
    fixture.request.request_id,
  );
  // The adapter must produce byte-for-byte the substrate-neutral envelope every
  // other adapter is held to (ADR-30: "Adapter suites must consume the same fixtures").
  assert.deepEqual(request, fixture.request);
  assert.equal(request.protocol, PROTOCOL);
});

// --- result -> Pi ------------------------------------------------------------

test("MethodologySelected renders a phase widget and announces the choice", async () => {
  const ui = new FakeUi();
  const result: Response["result"] = {
    type: "MethodologySelected",
    methodology: "invariant-analysis",
    reason: "The primary uncertainty is a preserved property.",
    phases: [
      { id: "scope", title: "Identify operation and scope" },
      { number: 2, title: "State preconditions" },
    ],
  };

  const outcome = await applyResult(result, deps(ui));

  assert.deepEqual(outcome, { kind: "selected", methodology: "invariant-analysis" });
  assert.match(ui.notifications[0].message, /Using invariant-analysis/);
  const lines = ui.lastWidgetLines();
  assert.ok(lines);
  assert.equal(lines.length, 2);
  assert.match(lines[0], /^\[▶\] Identify operation and scope$/); // first started
  assert.match(lines[1], /^\[ \] State preconditions$/); // rest pending
});

test("HumanDecisionRequired routes candidates through ctx.ui.select", async () => {
  const ui = new FakeUi(["invariant-analysis"]); // the human picks candidate 1
  const result: Response["result"] = {
    type: "HumanDecisionRequired",
    question: "Two methodologies fit — which addresses the primary uncertainty?",
    candidates: [
      { name: "invariant-analysis", rationale: "a preserved property" },
      { name: "first-principles", rationale: "decompose to axioms" },
    ],
  };

  const outcome = await applyResult(result, deps(ui));

  assert.deepEqual(outcome, { kind: "choice", chosen: "invariant-analysis" });
  assert.equal(ui.selects.length, 1);
  assert.deepEqual(ui.selects[0].options, [
    "invariant-analysis — a preserved property",
    "first-principles — decompose to axioms",
  ]);
});

test("a dismissed human decision surfaces as HumanDismissed", async () => {
  const ui = new FakeUi([undefined]); // cancelled / timed out
  const result: Response["result"] = {
    type: "HumanDecisionRequired",
    question: "pick one",
    candidates: ["a", "b"],
  };
  await assert.rejects(() => applyResult(result, deps(ui)), HumanDismissed);
});

test("Fault is reported to the user and returned", async () => {
  const ui = new FakeUi();
  const outcome = await applyResult(
    { type: "Fault", code: "unknown_methodology" },
    deps(ui),
  );
  assert.deepEqual(outcome, { kind: "fault", code: "unknown_methodology" });
  assert.equal(ui.notifications[0].level, "error");
  assert.match(ui.notifications[0].message, /unknown_methodology/);
});

test("HumanPort.ask reports the Pi capability gap rather than faking input", async () => {
  const ui = new FakeUi();
  await assert.rejects(() => new PiHumanPort(ui).ask("open question?"), UnsupportedByHost);
});

// --- end-to-end through the command handler ---------------------------------

test("/think handler: ambiguous -> human choice -> resolved re-dispatch", async () => {
  const requests: string[] = [];
  const dispatch = (request: {
    command: { requested_methodology?: string | null };
  }): Response => {
    requests.push(request.command.requested_methodology ?? "<null>");
    // First call (no requested methodology) is ambiguous; second (resolved) selects.
    if (request.command.requested_methodology == null) {
      return {
        protocol: PROTOCOL,
        request_id: "r1",
        result: {
          type: "HumanDecisionRequired",
          question: "which one?",
          candidates: [
            { name: "invariant-analysis", rationale: "property" },
            { name: "first-principles", rationale: "axioms" },
          ],
        },
      };
    }
    return {
      protocol: PROTOCOL,
      request_id: "r2",
      result: {
        type: "MethodologySelected",
        methodology: request.command.requested_methodology!,
        reason: "resolved by the human",
        phases: [{ title: "Only phase" }],
      },
    };
  };

  const ui = new FakeUi(["first-principles — axioms"]); // human picks candidate 2
  const pi = new FakePi();
  createMethodologistExtension({ dispatch: dispatch as never })(pi);
  const handler = pi.commands.get("think")!.handler;

  await handler("", { ui });

  assert.deepEqual(requests, ["<null>", "first-principles"]); // re-dispatched with the pick
  assert.match(ui.notifications.at(-1)!.message, /Using first-principles/);
  assert.deepEqual(ui.lastWidgetLines(), ["[▶] Only phase"]);
});

test("/think handler: a thrown dispatch is reported, not swallowed", async () => {
  const pi = new FakePi();
  createMethodologistExtension({
    dispatch: () => {
      throw new Error("core offline");
    },
  })(pi);
  const ui = new FakeUi();

  await pi.commands.get("think")!.handler("", { ui });

  assert.equal(ui.notifications.at(-1)!.level, "error");
  assert.match(ui.notifications.at(-1)!.message, /core offline/);
});
