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

test("any non-empty /think argument is an explicit name for core validation", () => {
  const parsed = parseThinkInvocation("why does the cache miss", ["invariant-analysis"]);
  assert.equal(parsed.requestedMethodology, "why does the cache miss");
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

test("MethodologySelected announces the choice and renders no persistent phase widget", async () => {
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
  // The stateless bridge cannot advance or clear tasks, so no persistent phase
  // widget is rendered; the phase plan travels in the tool result instead.
  assert.ok(
    ui.widgets.every((w) => w.content === undefined),
    "no phase-task widget content should be rendered",
  );
  assert.equal(ui.lastWidgetLines(), undefined);
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

test("bare /think delegates semantic selection to the model through shared resources", async () => {
  const pi = new FakePi();
  createMethodologistExtension({
    dispatch: () => {
      throw new Error("bare selection must not dispatch before the model chooses");
    },
  })(pi);
  const ui = new FakeUi();

  await pi.commands.get("think")!.handler("", { ui });

  assert.equal(pi.sentUserMessages.length, 1);
  assert.match(pi.sentUserMessages[0], /SKILL\.md/);
  assert.match(pi.sentUserMessages[0], /registry\.json/);
  assert.match(pi.sentUserMessages[0], /semantically compare/);
  assert.match(pi.sentUserMessages[0], /methodologist_select/);
});

test("/think --simple sends one direct shared-skill prompt and bypasses runtime state", async () => {
  let dispatches = 0;
  const pi = new FakePi();
  createMethodologistExtension({
    dispatch: () => {
      dispatches += 1;
      throw new Error("simple mode must not dispatch");
    },
  })(pi);
  const ui = new FakeUi();

  await pi.commands.get("think")!.handler(
    "--simple decide whether retries preserve request ordering",
    { ui },
  );

  assert.equal(dispatches, 0, "simple mode must bypass the bridge");
  assert.equal(pi.sentUserMessages.length, 1, "simple mode must emit exactly one prompt");
  assert.equal(pi.stateEntries.length, 0, "simple mode must not write workflow state");
  assert.equal(ui.widgets.length, 0, "simple mode must not render phase widgets");
  assert.equal(ui.selects.length, 0, "simple mode must not instantiate HumanPort UI");
  assert.match(pi.sentUserMessages[0], /SKILL\.md/);
  assert.match(pi.sentUserMessages[0], /registry\.json/);
  assert.match(pi.sentUserMessages[0], /retries preserve request ordering/);
  assert.match(pi.sentUserMessages[0], /simple-mode/);
  assert.doesNotMatch(pi.sentUserMessages[0], /\/think(?:\s|$)/, "prompt must not recurse");
  assert.doesNotMatch(pi.sentUserMessages[0], /methodologist_select/);
});

test("/think --simple requires an intent without dispatching or prompting", async () => {
  const pi = new FakePi();
  createMethodologistExtension({
    dispatch: () => {
      throw new Error("missing simple intent must not dispatch");
    },
  })(pi);
  const ui = new FakeUi();

  await pi.commands.get("think")!.handler("--simple", { ui });

  assert.equal(pi.sentUserMessages.length, 0);
  assert.equal(pi.stateEntries.length, 0);
  assert.equal(ui.notifications.at(-1)?.level, "warning");
  assert.match(ui.notifications.at(-1)?.message ?? "", /<intent>/);
});

test("normal named /think remains bridge-backed", async () => {
  let dispatches = 0;
  const pi = new FakePi();
  createMethodologistExtension({
    dispatch: (request): Response => {
      dispatches += 1;
      // The named-/think path only ever dispatches SelectMethodology; narrow the
      // Command union so the mock is type-checked, not just runtime-correct.
      const { command } = request;
      if (command.type !== "SelectMethodology") {
        throw new Error(`expected SelectMethodology, got ${command.type}`);
      }
      return {
        protocol: PROTOCOL,
        request_id: request.request_id,
        result: {
          type: "MethodologySelected",
          methodology: command.requested_methodology!,
          reason: command.intent,
          phases: Array.from({ length: 6 }, (_, index) => ({
            number: index + 1,
            title: `Phase ${index + 1}`,
          })),
        },
      };
    },
  })(pi);
  const ui = new FakeUi();

  await pi.commands.get("think")!.handler("formal-reasoning", { ui });

  assert.equal(dispatches, 1);
  assert.equal(pi.sentUserMessages.length, 0);
  assert.ok(
    ui.widgets.every((w) => w.content === undefined),
    "named /think renders no persistent phase widget",
  );
});

test("model selection tool: ambiguity -> human choice -> named bridge", async () => {
  const requests: string[] = [];
  const dispatch = (request: {
    request_id: string;
    command: { intent: string; requested_methodology?: string | null };
  }): Response => {
    requests.push(request.command.requested_methodology ?? "<null>");
    return {
      protocol: PROTOCOL,
      request_id: request.request_id,
      result: {
        type: "MethodologySelected",
        methodology: request.command.requested_methodology!,
        reason: request.command.intent,
        phases: Array.from({ length: 6 }, (_, index) => ({
          number: index + 1,
          title: `Phase ${index + 1}`,
        })),
      },
    };
  };

  const ui = new FakeUi(["first-principles — axioms"]);
  const pi = new FakePi();
  createMethodologistExtension({ dispatch: dispatch as never })(pi);
  const tool = pi.tools.get("methodologist_select")!;
  const result = await tool.execute(
    "call-1",
    {
      candidates: [
        { name: "invariant-analysis", rationale: "property" },
        { name: "first-principles", rationale: "axioms" },
      ],
    },
    undefined,
    undefined,
    { ui },
  );

  assert.deepEqual(requests, ["first-principles"]);
  assert.match(result.content[0].text, /Using \*\*first-principles\*\*/);
  assert.ok(
    ui.widgets.every((w) => w.content === undefined),
    "selection renders no persistent phase widget",
  );
});

test("/think handler: a thrown dispatch is reported, not swallowed", async () => {
  const pi = new FakePi();
  createMethodologistExtension({
    dispatch: () => {
      throw new Error("core offline");
    },
  })(pi);
  const ui = new FakeUi();

  await pi.commands.get("think")!.handler("formal-reasoning", { ui });

  assert.equal(ui.notifications.at(-1)!.level, "error");
  assert.match(ui.notifications.at(-1)!.message, /core offline/);
  assert.ok(
    ui.widgets.some((w) => w.content === undefined),
    "an error clears the phase widget",
  );
});

test("session_shutdown clears any lingering phase widget", () => {
  const pi = new FakePi();
  createMethodologistExtension({
    dispatch: () => {
      throw new Error("unused");
    },
  })(pi);
  const ui = new FakeUi();

  const handler = pi.handlers.get("session_shutdown") as
    | ((event: unknown, ctx: { ui: FakeUi }) => void)
    | undefined;
  assert.equal(typeof handler, "function", "session_shutdown handler must be registered");
  handler!(undefined, { ui });

  assert.deepEqual(ui.widgets.at(-1), {
    id: "methodologist:phases",
    content: undefined,
    placement: "belowEditor",
  });
});
