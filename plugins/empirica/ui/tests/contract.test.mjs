// The UI's presentation maps against the observer contract (contracts/empirica-ui-view/v1): every states.json
// domain and every contract list in catalogue.json is keyed by exactly one contract enum, no more and no fewer
// values, so a value the plugin adds or drops fails here before it renders as "Unknown".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (u) => JSON.parse(readFileSync(new URL(u, import.meta.url)));
const CONTRACT = "../../../../contracts/empirica-ui-view/v1/";
const view = read(`${CONTRACT}view.schema.json`), run = read(`${CONTRACT}run.schema.json`);
const states = read("../states.json"), catalogue = read("../catalogue.json");

/** The enum at a JSON-pointer-like path of property names, without null. */
function enumAt(schema, ...path) {
  const deref = (n) => (n?.$ref ? deref(schema.$defs[n.$ref.replace("#/$defs/", "")]) : n);
  let node = schema;
  for (const p of path) {
    node = deref(node);
    node = deref(p === "[]" ? node.items : node.properties?.[p] ?? node.oneOf?.map(deref).find((o) => o.properties)?.properties[p]);
  }
  assert.ok(node?.enum, `no enum at ${path.join(".")}`);
  return node.enum.filter((v) => v !== null);
}
const ROW = ["projects", "[]", "runs", "[]"], CLAIM = [...ROW, "claims", "[]"];
const COVERS = {
  "states.run": [view, ...ROW, "state"],
  "states.diagnostic": [view, ...ROW, "diagnostic"],
  "states.audit": [view, ...ROW, "audit"],
  "states.claim": [view, ...CLAIM, "state"],
  "states.evidence": [view, ...CLAIM, "evidence"],
  "states.review": [view, ...CLAIM, "review"],
  "states.step": [run, "steps", "[]", "kind"],
  "states.outcome": [run, "steps", "[]", "outcome"],
  "states.file": [run, "stale", "[]", "state"],
  "states.verification": [run, "claims", "[]", "records", "[]", "status"],
  "states.route": [run, "claims", "[]", "records", "[]", "route"],
  // the answer, or while it waits the route it waits on: one vocabulary, two enums
  "states.decision": [[run, "claims", "[]", "decision", "outcome"], [run, "claims", "[]", "decision", "route"]],
  "catalogue.record_classes": [run, "claims", "[]", "records", "[]", "class"],
  "catalogue.kinds": [view, ...CLAIM, "kind"],
  "catalogue.independence": [run, "audit", "independence"],
  "catalogue.control_modes": [run, "control", "mode"],
  "catalogue.approvals": [run, "control", "approval"],
};
const own = (o) => Object.keys(o).filter((k) => !k.startsWith("$"));

for (const [where, spec] of Object.entries(COVERS)) {
  const specs = Array.isArray(spec[0]) ? spec : [spec];
  test(`${where} covers the contract's ${specs.map(([, ...path]) => path.filter((p) => p !== "[]").join(".")).join(" and ")} exactly`, () => {
    const [file, key] = where.split(".");
    const have = file === "states" ? own(states[key]) : catalogue[key];
    assert.deepEqual([...have].sort(), [...new Set(specs.flatMap(([schema, ...path]) => enumAt(schema, ...path)))].sort());
  });
}

test("every states.json domain is held to a contract enum (only the fallback is not)", () => {
  assert.deepEqual(own(states).filter((d) => d !== "unknown").sort(), Object.keys(COVERS).filter((k) => k.startsWith("states.")).map((k) => k.slice(7)).sort());
});

test("every filter names a covered field or domain", () => {
  const fields = { run: Object.keys(view.$defs.run.properties), claim: Object.keys(view.$defs.claim.properties) };
  for (const f of catalogue.filters) {
    assert.ok(fields[f.of].includes(f.field ?? f.flag), `${f.id}: ${f.of} has no field ${f.field ?? f.flag}`);
    if (f.domain) assert.ok(states[f.domain], `${f.id}: no domain ${f.domain}`);
    if (f.values) assert.ok(`catalogue.${f.values}` in COVERS, `${f.id}: ${f.values} is not a contract list`);
  }
});
