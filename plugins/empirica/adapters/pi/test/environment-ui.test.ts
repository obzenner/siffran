// The Pi environment approval (Empirica 7.2 §5/§6, ADR-0072 write-first): the admitted revision's
// document is written and presented by the private bridge, the shared dialog frame shows the
// document's own facts, and approve / reject / no decision map as the configuration dialog does.
// Fake private legs only; the real Python legs are pinned by tests/test_pi_private_bridge.py and
// the live bridge file.

import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { visibleWidth } from "@earendil-works/pi-tui";

import { governEnvironment, type EnvironmentAdmission } from "../src/governance-ui.ts";
import { ENVIRONMENT_CONTROLS, PLAN_LABEL, RATIONALE_LABEL, initialState, renderEnvironmentLines }
  from "../src/dialog-view.ts";
import type { EnvironmentFacts } from "../src/dialog-view.ts";
import { PROTOCOL, type Response } from "../src/contract.ts";
import { assertPrivateResponse, type PrivateIngress, type PrivateIngressRequest, type PrivateOperation }
  from "../src/private-transport.ts";
import { fakeCtx } from "./fakes.ts";

const ENTER = "\r", RIGHT = "\x1b[C", ESC = "\x1b[27u";
const HITL = new URL("../../../../../contracts/empirica/v2/hitl-fixtures/valid-environment-approve.json",
  import.meta.url);
const HITL_SCHEMA = new URL("../../../../../contracts/empirica/v2/hitl.schema.json", import.meta.url);
const written = JSON.parse(readFileSync(HITL, "utf8")) as Record<string, unknown>;
const FACT_NAMES = ["revision", "proposal_digest", "goal", "profile_id", "networked", "spike_images", "preparation",
  "rationale", "previous_active_binding"] as const;
/** The facts exactly as the private bridge reads them out of the written document. */
const FACTS = Object.fromEntries(FACT_NAMES.map(name => [name, written[name]])) as unknown as EnvironmentFacts;
/** The networked profile: the schema's own two-way `networked` pair names it. */
const NETWORKED_PROFILE = (JSON.parse(readFileSync(HITL_SCHEMA, "utf8")).$defs.environmentDocument.allOf as Array<{
  if: { properties: { profile_id: { const: string } } }; then: { properties: { networked: { const: boolean } } };
}>).find(rule => rule.then.properties.networked.const)!.if.properties.profile_id.const;
const DOCUMENT = { digest: `sha256:${"d".repeat(64)}`, path: `/home/hitl/${"d".repeat(64)}.json` };
const theme = { accent: (x: string) => x, muted: (x: string) => x, warning: (x: string) => `!${x}` };

function envelope(result: Record<string, unknown>): Response {
  return { protocol: PROTOCOL, request_id: "trusted-environment", result } as unknown as Response;
}

function run(id = "run"): Record<string, unknown> {
  return { id, status: "active", spike_backend: { kind: "unqualified" }, governance: null };
}

function admitted(id = "run"): Response {
  return { protocol: PROTOCOL, request_id: "observe", result: { type: "Allow", converged: false, run: run(id) } } as
    unknown as Response;
}

/** One environment mediation against fake private legs: `keys` are typed into the one dialog. */
function harness(keys: string[] = [ENTER]) {
  const calls: PrivateIngressRequest[] = [];
  const screens: string[][] = [];
  const settings = {
    present: (): unknown => ({ type: "environment_presentation", receipt_id: "receipt-1", document: DOCUMENT,
      environment: structuredClone(FACTS), response: envelope({ type: "Allow", converged: false, run: run() }) }),
    onCustom: () => {},
  };
  const ctx = fakeCtx(); ctx.hasUI = true;
  ctx.ui.custom = async factory => await new Promise(resolve => {
    const component = factory({ requestRender() {} }, { fg: (_color, text) => text }, {}, resolve);
    screens.push(component.render(80));
    settings.onCustom();
    for (const key of keys) component.handleInput(key);
  });
  const trusted: PrivateIngress = async request => {
    calls.push(structuredClone(request));
    if (request.operation === "environment_present") return settings.present();
    if (request.operation === "environment_decision")
      return envelope({ type: "Allow", converged: false, run: run() });
    throw new Error(`unexpected ${request.operation}`);
  };
  const admission: EnvironmentAdmission = { run_id: "run", revision: FACTS.revision };
  return { ctx, trusted, calls, screens, settings, admission,
    invoke: (response = admitted(), signal?: AbortSignal, deadline?: number, own: EnvironmentAdmission | null = admission) =>
      governEnvironment(response, own, ctx, trusted, signal, deadline) };
}

const operations = (h: ReturnType<typeof harness>) => h.calls.map(call => call.operation);

test("an approval presents the admitted revision once and decides on the written document's digest", async () => {
  for (const [keys, outcome] of [[[ENTER], "approve"], [[RIGHT, ENTER], "reject"]] as const) {
    const h = harness([...keys]);
    const response = await h.invoke();
    assert.equal(response.result.type, "Allow");
    assert.deepEqual(operations(h), ["environment_present", "environment_decision"]);
    assert.deepEqual(h.calls[0]!.payload, { revision: h.admission.revision });
    assert.deepEqual(h.calls[1]!.payload, { run_id: "run", receipt_id: "receipt-1", revision: FACTS.revision,
      proposal_digest: FACTS.proposal_digest, outcome, document_digest: DOCUMENT.digest });
    assert.equal(h.screens.length, 1, "one dialog");
  }
});

test("Esc, a dialog that shows nothing, a past deadline and an abort decide nothing", async () => {
  const original = Date.now;
  try {
    const cases: Array<[string[], (h: ReturnType<typeof harness>) => Promise<Response>]> = [
      [[ESC], h => h.invoke()],
      [[ENTER], h => { h.ctx.ui.custom = async () => undefined as never; return h.invoke(); }],
      [[ENTER], h => { let clock = 0; Date.now = () => clock; h.settings.onCustom = () => { clock = 2; };
        return h.invoke(undefined, undefined, 1); }],
      [[ENTER], h => { const controller = new AbortController(); h.settings.onCustom = () => controller.abort();
        return h.invoke(undefined, controller.signal); }],
    ];
    for (const [keys, mediate] of cases) {
      const h = harness(keys);
      const response = await mediate(h);
      Date.now = original;
      assert.deepEqual(operations(h), ["environment_present"], "only the presentation row");
      assert.equal(response.result.type, "Allow");
    }
  } finally { Date.now = original; }
});

test("nothing is presented without a dialog surface, an admission of this run, or time left", async () => {
  const cases: Array<(h: ReturnType<typeof harness>) => Promise<Response>> = [
    h => { h.ctx.hasUI = false; return h.invoke(); },
    h => { delete h.ctx.ui.custom; return h.invoke(); },
    h => h.invoke(admitted(), undefined, undefined, null),
    h => h.invoke(admitted(), undefined, undefined, { run_id: "another", revision: FACTS.revision }),
    h => h.invoke(admitted(), undefined, Date.now() - 1),
    h => h.invoke(envelope({ type: "Inert", reason: "no_run" })),
  ];
  for (const mediate of cases) {
    const h = harness();
    await mediate(h);
    assert.deepEqual(h.calls, []);
    assert.equal(h.screens.length, 0);
  }
});

test("a refused presentation, a document of another revision or a failing leg returns without a decision", async () => {
  const refusal = envelope({ type: "Inert", reason: "no_run" });
  const cases: Array<[() => unknown, Response["result"]["type"]]> = [
    [() => ({ type: "environment_refused", response: refusal }), "Inert"],
    [() => ({ type: "environment_refused", response: null }), "Allow"],
    [() => ({ type: "environment_presentation", receipt_id: "receipt-1", document: DOCUMENT,
      environment: { ...structuredClone(FACTS), revision: FACTS.revision + 1 },
      response: envelope({ type: "Allow", converged: false, run: run() }) }), "Allow"],
    [() => { throw new Error("private bridge unavailable"); }, "Allow"],
    [() => ({ type: "environment_presentation", receipt_id: "receipt-1", document: { ...DOCUMENT, text: "{}" },
      environment: structuredClone(FACTS), response: envelope({ type: "Allow" }) }), "Allow"],
  ];
  for (const [present, type] of cases) {
    const h = harness();
    h.settings.present = present;
    const response = await h.invoke();
    assert.deepEqual(operations(h), ["environment_present"]);
    assert.equal(h.screens.length, 0, "no dialog");
    assert.equal(response.result.type, type);
  }
});

test("the dialog shows the document's facts under their unverified labels and the networked call-out", () => {
  const isolated = renderEnvironmentLines(FACTS, DOCUMENT, initialState(ENVIRONMENT_CONTROLS), 80, theme, 900_000);
  const networked = renderEnvironmentLines({ ...FACTS, profile_id: NETWORKED_PROFILE, networked: true,
    spike_images: [{ reference: FACTS.spike_images[0]!.reference, image_id: `sha256:${"e".repeat(64)}` }],
    previous_active_binding: { record_digest: `sha256:${"f".repeat(64)}`, revision: FACTS.revision } },
  DOCUMENT, initialState(ENVIRONMENT_CONTROLS), 80, theme, 900_000);
  assert.ok(isolated[0]!.includes(`environment revision ${FACTS.revision}`));
  for (const lines of [isolated, networked]) {
    assert.ok(lines.includes(PLAN_LABEL) && lines.includes(RATIONALE_LABEL));
    assert.ok(lines.indexOf(PLAN_LABEL) < lines.indexOf(RATIONALE_LABEL));
    assert.ok(lines.some(line => line.includes(FACTS.spike_images[0]!.reference)));
    assert.ok(lines.includes(`Document ${DOCUMENT.digest.slice(0, "sha256:".length + 12)}`));
    assert.equal(lines.filter(line => line.includes("Approve") && line.includes("Reject")).length, 1);
  }
  const profile = (lines: string[]) => lines.find(line => line.includes(FACTS.profile_id) || line.includes(NETWORKED_PROFILE))!;
  assert.ok(!profile(isolated).startsWith("!"), "the isolated profile is not called out");
  assert.ok(profile(networked).startsWith("!") && /EGRESS/.test(profile(networked)),
    "the networked profile is called out in the warning colour");
  assert.ok(networked.some(line => line.includes("(observed sha256:")), "an observed ID is shown as a diagnostic");
  assert.ok(isolated.some(line => /Previous binding +none/.test(line)));
  assert.ok(networked.some(line => line.includes(`environment revision ${FACTS.revision}`)
    && line.startsWith("Previous binding")));
});

test("long free text is bounded and every line fits the width", () => {
  const long = "x ".repeat(400).trim();
  const facts = { ...FACTS, goal: long, rationale: long, preparation: Array(8).fill(long) as string[] };
  for (const width of [80, 50]) {
    const lines = renderEnvironmentLines(facts, DOCUMENT, initialState(ENVIRONMENT_CONTROLS), width, theme, 900_000);
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    const start = lines.indexOf(RATIONALE_LABEL);
    const rationale = lines.slice(start + 1).findIndex(line => !line.startsWith("  "));
    assert.equal(rationale, 3, "the rationale keeps at most three lines");
    assert.ok(lines[start + 3]!.includes("…"), "a cut rationale ends with an ellipsis");
    const plan = lines.slice(lines.indexOf(PLAN_LABEL) + 1, start);
    assert.equal(plan.length, 16, "each step keeps at most two lines");
    assert.ok(lines.join("\n").includes(DOCUMENT.path.slice(-20)), "the exact path stays whole");
  }
});

test("the private environment answers are validated at the transport before any mediator reads them", () => {
  const response = admitted();
  const shown = { type: "environment_presentation", receipt_id: "receipt-1", document: DOCUMENT,
    environment: structuredClone(FACTS), response };
  const accepted: Array<[PrivateOperation, unknown]> = [
    ["environment_admit", { type: "environment_admitted", response, admission: { run_id: "run", revision: 1 } }],
    ["environment_admit", { type: "environment_admitted", response, admission: null }],
    ["environment_present", shown],
    ["environment_present", { ...shown, environment: { ...FACTS, previous_active_binding: {
      record_digest: `sha256:${"f".repeat(64)}`, revision: 1 } } }],
    ["environment_present", { type: "environment_refused", response }],
    ["environment_present", { type: "environment_refused", response: null }],
    ["environment_decision", response],
  ];
  for (const [operation, value] of accepted)
    assert.deepEqual(assertPrivateResponse(operation, value), value, operation);
  const facts = (changes: Record<string, unknown>) => ({ ...shown, environment: { ...FACTS, ...changes } });
  const rejected: Array<[PrivateOperation, unknown]> = [
    ["environment_admit", { type: "environment_admitted", response: {}, admission: null }],
    ["environment_admit", { type: "environment_admitted", response, admission: { run_id: "run", revision: 0 } }],
    ["environment_admit", { type: "environment_admitted", response, admission: { run_id: "", revision: 1 } }],
    ["environment_admit", { type: "environment_admitted", response }],
    ["environment_present", { ...shown, receipt_id: "" }],
    ["environment_present", { ...shown, response: null }],
    ["environment_present", { ...shown, document: { ...DOCUMENT, text: "{}" } }],
    ["environment_present", { ...shown, document: { ...DOCUMENT, path: "hitl/a.json" } }],
    ["environment_present", { ...shown, document: { ...DOCUMENT, digest: "sha256:abc" } }],
    ["environment_present", facts({ revision: 0 })],
    ["environment_present", facts({ proposal_digest: "sha256:abc" })],
    ["environment_present", facts({ networked: "no" })],
    ["environment_present", facts({ spike_images: [] })],
    ["environment_present", facts({ spike_images: [{ reference: "", image_id: null }] })],
    ["environment_present", facts({ spike_images: [{ reference: "a" }] })],
    ["environment_present", facts({ preparation: [] })],
    ["environment_present", facts({ preparation: [""] })],
    ["environment_present", facts({ rationale: "" })],
    ["environment_present", facts({ goal: 1 })],
    ["environment_present", facts({ profile_id: "" })],
    ["environment_present", facts({ previous_active_binding: { record_digest: "sha256:abc", revision: 1 } })],
    ["environment_present", facts({ previous_active_binding: { record_digest: `sha256:${"f".repeat(64)}` } })],
    ["environment_present", { type: "environment_refused", response: {} }],
    ["environment_present", { type: "environment_refused" }],
    ["environment_decision", { result: { type: "Allow" } }],
  ];
  for (const [operation, value] of rejected)
    assert.throws(() => assertPrivateResponse(operation, value), /response is invalid/, operation);
});
