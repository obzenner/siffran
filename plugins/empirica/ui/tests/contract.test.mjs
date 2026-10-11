// The UI's value lists against the observer contract (contracts/empirica-ui-view/v1): enums.json is generated from the
// contract and must equal what is generated now, and every state vocabulary in states.json is keyed by exactly one
// contract list, no more and no fewer values, so a value the plugin adds or drops fails here before it renders as
// "Unknown".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { CONTRACT_DIR, PATHS, generate, render } from "../scripts/enums.mjs";

const read = (u) => JSON.parse(readFileSync(new URL(u, import.meta.url)));
const enums = read("../enums.json"), states = read("../states.json");
const own = (o) => Object.keys(o).filter((k) => !k.startsWith("$"));

test("enums.json is what the contract generates now (make ui-enums rewrites it)", () => {
  assert.equal(readFileSync(new URL("../enums.json", import.meta.url), "utf8"), render(generate(fileURLToPath(CONTRACT_DIR))));
});

test("a contract value the file lacks is caught, and so is an extra one", () => {
  const dropped = structuredClone(enums); dropped.run.pop();
  assert.notEqual(render(dropped), render(generate(fileURLToPath(CONTRACT_DIR))));
});

test("every state vocabulary is keyed by exactly the values of one contract list (only the fallback is not)", () => {
  const domains = own(states).filter((d) => d !== "unknown");
  for (const d of domains) assert.deepEqual(own(states[d]).sort(), [...enums[d]].sort(), d);
  assert.ok(domains.length >= 12);
});

test("every list the contract defines is either presented (states.json) or worded by copy families the audit expands", () => {
  const presented = new Set(own(states));
  const copyOnly = ["independence", "kind", "who", "where", "standing", "record_class", "mode", "approval", "budgets", "spike", "spike_outcome"];
  for (const name of [...Object.keys(PATHS), "budgets"]) assert.ok(presented.has(name) || copyOnly.includes(name), `${name} has neither a presentation nor a copy family`);
});

test("every value the demo fixtures hold is one the contract lists", () => {
  const dir = "../../vendor/contracts/empirica-ui-view/v1/fixtures/";
  const view = read(`${dir}view.json`), runs = read(`${dir}runs.json`);
  const rows = view.projects.flatMap((p) => p.runs);
  const seen = {
    run: rows.map((r) => r.state), attention: rows.map((r) => r.attention), diagnostic: rows.map((r) => r.diagnostic),
    audit: rows.map((r) => r.audit?.verdict), claim: rows.flatMap((r) => (r.claims ?? []).map((c) => c.state)),
    assurance: rows.flatMap((r) => (r.claims ?? []).map((c) => c.assurance)), who: rows.map((r) => r.next?.who),
    where: rows.map((r) => r.next?.where), step: Object.values(runs).flatMap((d) => (d.steps ?? []).map((s) => s.kind)),
    decision: Object.values(runs).flatMap((d) => (d.claims ?? []).map((c) => c.decision?.status)),
  };
  for (const [name, values] of Object.entries(seen)) for (const v of values.filter((x) => x != null)) assert.ok(enums[name].includes(v), `${name}: ${v}`);
});
