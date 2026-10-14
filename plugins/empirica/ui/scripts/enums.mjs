// The UI's value lists, generated from the observer contract (contracts/empirica-ui-view/v1) into enums.json, so no list of
// values is kept by hand: `node scripts/enums.mjs` writes it (make ui-enums), tests/contract.test.mjs regenerates it and
// fails when the file differs, and states.json / copy are checked against it. `generate(dir)` is pure.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** Where each list lives in the contract: [schema, ...property path]; `[]` steps into array items. A list that two places
 * share (where, ask) is their union. The row schema is a list of rows; the run schema is one run's sheet. */
const ROW = ["row", "[]"], CLAIM = [...ROW, "claims", "[]"], SHEET_CLAIM = ["run", "claims", "[]"];
export const PATHS = {
  standing: [[...ROW, "standing"]],
  ask: [[...ROW, "ask"], ["run", "decisions", "[]", "asked"]],
  run: [[...ROW, "state"]],
  diagnostic: [[...ROW, "diagnostic"]],
  audit: [[...ROW, "audit", "verdict"], ["run", "audits", "[]", "verdict"]],
  independence: [[...ROW, "audit", "independence"], ["run", "audits", "[]", "independence"]],
  claim: [[...CLAIM, "state"]],
  kind: [[...CLAIM, "kind"], ["run", "dropped", "[]", "kind"]],
  evidence: [[...CLAIM, "evidence"]],
  review: [[...CLAIM, "review"]],
  assurance: [[...CLAIM, "assurance"]],
  who: [[...ROW, "next", "who"], ["run", "outstanding", "[]", "who"]],
  where: [[...ROW, "next", "where"], ["run", "outstanding", "[]", "where"], [...SHEET_CLAIM, "records", "[]", "wait", "where"]],
  step: [["run", "steps", "[]", "kind"]],
  outcome: [["run", "steps", "[]", "outcome"]],
  step_standing: [["run", "steps", "[]", "standing"]],
  file: [["run", "stale", "[]", "state"]],
  verification: [[...SHEET_CLAIM, "records", "[]", "status"]],
  record_class: [[...SHEET_CLAIM, "records", "[]", "class"]],
  decision: [[...SHEET_CLAIM, "decision", "status"]],
  spike: [[...SHEET_CLAIM, "proof", "[]", "state"]],
  spike_outcome: [[...SHEET_CLAIM, "proof", "[]", "outcome"], [...SHEET_CLAIM, "proof", "[]", "gate"]],
  mode: [["run", "governance", "mode"]],
  approval: [["run", "governance", "approval"]],
  why: [["run", "dropped", "[]", "why"]],
  answer: [["run", "decisions", "[]", "answer"]],
  effect: [["run", "decisions", "[]", "effect"]],
};

/** Lists that are the names of an object's properties rather than an enum. */
export const KEYS = { budgets: ["run", "governance", "ceilings"], flag: [...ROW, "flags"] };

/** The schema node at a path of property names, looking through `$ref` and a nullable `oneOf`. */
function nodeAt(schema, steps) {
  const deref = (n) => (n?.$ref ? deref(schema.$defs[n.$ref.replace("#/$defs/", "")]) : n);
  const some = (n, has) => (has(n) ? n : n.oneOf?.map(deref).find(has));
  let node = schema;
  for (const p of steps) {
    node = deref(node);
    node = deref(p === "[]" ? some(node, (o) => o.items)?.items : some(node, (o) => o.properties?.[p])?.properties[p]);
  }
  return deref(node);
}
const enumAt = (schema, steps) => {
  const node = nodeAt(schema, steps);
  if (!node?.enum) throw new Error(`no enum at ${steps.join(".")}`);
  return node.enum.filter((v) => v !== null);
};

/** Every list, from the contract's schemas read out of `dir`. */
export function generate(dir) {
  const read = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  const schemas = { row: read("row.schema.json"), run: read("run.schema.json") };
  const out = {};
  for (const [name, places] of Object.entries(PATHS)) {
    out[name] = [...new Set(places.flatMap(([which, ...p]) => enumAt(schemas[which], p)))];
  }
  for (const [name, [which, ...p]] of Object.entries(KEYS)) {
    const node = nodeAt(schemas[which], p);
    const object = node?.properties ? node : node?.oneOf?.find((o) => o.properties);
    if (!object) throw new Error(`no properties at ${p.join(".")}`);
    out[name] = Object.keys(object.properties);
  }
  return out;
}

// EMPIRICA_PLUGIN_ROOT names the plugin when this runs from a copy of the UI alone (make mutate's Stryker sandbox).
export const CONTRACT_DIR = process.env.EMPIRICA_PLUGIN_ROOT
  ? new URL("../../contracts/empirica-ui-view/v1/", pathToFileURL(`${process.env.EMPIRICA_PLUGIN_ROOT.replace(/\/$/, "")}/`))
  : new URL("../../../../contracts/empirica-ui-view/v1/", import.meta.url);
export const generateAll = () => generate(fileURLToPath(CONTRACT_DIR));
export const FILE = new URL("../enums.json", import.meta.url);
export const render = (enums) => `${JSON.stringify(enums, null, 2)}\n`;

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  fs.writeFileSync(FILE, render(generateAll()));
  console.log("enums.json written");
}
