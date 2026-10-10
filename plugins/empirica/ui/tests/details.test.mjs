// The run detail's builders as data: G16 UI-16 (one heading per route a run waits on).
import assert from "node:assert/strict";
import { test } from "node:test";
// The builders live beside the custom elements; a bare class stands in for the DOM base they extend.
globalThis.HTMLElement ??= class {};
const { runDetail } = await import("../elements/templates/details.js");

const run = { key: "p/r/1", project: "p", state: "active", audit: null, diagnostic: null, generation: 1, updated: null, claims: [{ id: "C0", text: "Root", kind: "ordinary", gating: true, state: "blocked" }] };
const detail = (waiting) => ({
  goal: "g", author: null, auditor: null, control: null, budgets: null, audit: { findings: [], independence: "unknown" },
  residuals: [], waiting, stale: [], steps: [], claims: [],
});

test("a run that waits on two routes has one heading and one list per route", () => {
  const parts = runDetail(run, detail([
    { claim: "C0", route: "human", records: 1, decision: false },
    { claim: "C0", route: "unavailable", records: 1, decision: false },
    { claim: "C0", route: "human", records: 0, decision: true },
  ])).parts;
  assert.equal(parts.length, 2, "one part per route, not one titled by the first item's route");
  assert.notEqual(parts[0].label, parts[1].label);
  assert.equal((parts[0].html.match(/gap-3xs/g) ?? []).length, 2, "both items of the first route sit under its heading");
});

test("a run that waits on nothing has no waiting part", () => {
  assert.equal(runDetail(run, detail([])).parts.length, 0);
});
