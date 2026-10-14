// The UI's value lists against the observer contract (contracts/empirica-ui-view/v1): enums.json is generated from the
// contract and must equal what is generated now, and every state vocabulary in states.json is keyed by exactly one
// contract list, no more and no fewer values, so a value the plugin adds or drops fails here before it renders as
// "Unknown".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "vitest";
import { FIXTURES } from "./paths.mjs";
import { CONTRACT_DIR, PATHS, generate, generateAll, render } from "../scripts/enums.mjs";

const read = (u) => JSON.parse(readFileSync(new URL(u, import.meta.url)));
const enums = read("../enums.json"), states = read("../states.json");
const own = (o) => Object.keys(o).filter((k) => !k.startsWith("$"));

test("enums.json is what the contract generates now (make ui-enums rewrites it)", () => {
  assert.equal(readFileSync(new URL("../enums.json", import.meta.url), "utf8"), render(generateAll()));
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
  const copyOnly = ["independence", "kind", "who", "where", "record_class", "mode", "approval", "budgets", "spike", "spike_outcome", "ask", "effect", "flag", "step_standing"];
  for (const name of [...Object.keys(PATHS), "budgets"]) assert.ok(presented.has(name) || copyOnly.includes(name), `${name} has neither a presentation nor a copy family`);
});

test("every value the demo fixtures hold is one the contract lists", () => {
  const dir = FIXTURES;
  const rows = read(`${dir}rows.json`), sheets = Object.values(read(`${dir}runs.json`));
  const claims = sheets.flatMap((d) => d.claims ?? []), steps = sheets.flatMap((d) => d.steps ?? []);
  const seen = {
    standing: rows.map((r) => r.standing), ask: [...rows.map((r) => r.ask), ...sheets.flatMap((d) => (d.decisions ?? []).map((x) => x.asked))],
    run: rows.map((r) => r.state), diagnostic: rows.map((r) => r.diagnostic), audit: [...rows.map((r) => r.audit?.verdict), ...sheets.flatMap((d) => (d.audits ?? []).map((x) => x.verdict))],
    claim: rows.flatMap((r) => (r.claims ?? []).map((c) => c.state)), assurance: rows.flatMap((r) => (r.claims ?? []).map((c) => c.assurance)),
    who: [...rows.map((r) => r.next?.who), ...sheets.flatMap((d) => (d.outstanding ?? []).map((x) => x.who))], where: [...rows.map((r) => r.next?.where), ...sheets.flatMap((d) => (d.outstanding ?? []).map((x) => x.where))],
    step: steps.map((s) => s.kind), step_standing: steps.map((s) => s.standing), decision: claims.map((c) => c.decision?.status),
    why: sheets.flatMap((d) => (d.dropped ?? []).map((x) => x.why)), answer: sheets.flatMap((d) => (d.decisions ?? []).map((x) => x.answer)),
    effect: sheets.flatMap((d) => (d.decisions ?? []).map((x) => x.effect)), verification: claims.flatMap((c) => c.records.map((r) => r.status)),
  };
  for (const [name, values] of Object.entries(seen)) for (const v of values.filter((x) => x != null)) assert.ok(enums[name].includes(v), `${name}: ${v}`);
});
