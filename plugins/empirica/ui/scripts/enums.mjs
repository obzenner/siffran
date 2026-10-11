// The UI's value lists, generated from the observer contract (contracts/empirica-ui-view/v1) into enums.json, so no
// list of values is kept by hand: `node scripts/enums.mjs` writes it (make ui-enums), tests/contract.test.mjs
// regenerates it and fails when the file differs, and states.json / copy are checked against it. `generate(dir)` is pure.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** Where each list lives in the contract: [schema, ...property path]; `[]` steps into array items. A list that two
 * places share (where) is their union. */
const ROW = ["projects", "[]", "runs", "[]"], CLAIM = [...ROW, "claims", "[]"];
const DETAIL_CLAIM = ["claims", "[]"];
export const PATHS = {
  run: [["view", ...ROW, "state"]],
  diagnostic: [["view", ...ROW, "diagnostic"]],
  attention: [["view", ...ROW, "attention"]],
  audit: [["view", ...ROW, "audit", "verdict"]],
  independence: [["view", ...ROW, "audit", "independence"]],
  claim: [["view", ...CLAIM, "state"]],
  kind: [["view", ...CLAIM, "kind"]],
  evidence: [["view", ...CLAIM, "evidence"]],
  review: [["view", ...CLAIM, "review"]],
  assurance: [["view", ...CLAIM, "assurance"]],
  who: [["view", ...ROW, "next", "who"], ["run", "outstanding", "[]", "who"]],
  where: [["view", ...ROW, "next", "where"], ["run", "outstanding", "[]", "where"], ["run", ...DETAIL_CLAIM, "records", "[]", "wait", "where"]],
  step: [["run", "steps", "[]", "kind"]],
  outcome: [["run", "steps", "[]", "outcome"]],
  standing: [["run", "steps", "[]", "standing"]],
  file: [["run", "stale", "[]", "state"]],
  verification: [["run", ...DETAIL_CLAIM, "records", "[]", "status"]],
  record_class: [["run", ...DETAIL_CLAIM, "records", "[]", "class"]],
  decision: [["run", ...DETAIL_CLAIM, "decision", "status"]],
  spike: [["run", ...DETAIL_CLAIM, "proof", "[]", "state"]],
  spike_outcome: [["run", ...DETAIL_CLAIM, "proof", "[]", "outcome"], ["run", ...DETAIL_CLAIM, "proof", "[]", "gate"]],
  mode: [["run", "governance", "mode"]],
  approval: [["run", "governance", "approval"]],
};

/** Lists that are the names of an object's properties rather than an enum. */
export const KEYS = { budgets: ["run", "governance", "ceilings"] };

/** The enum at a path of property names, looking through `$ref` and a nullable `oneOf`, without null. */
function enumAt(schema, path) {
  const deref = (n) => (n?.$ref ? deref(schema.$defs[n.$ref.replace("#/$defs/", "")]) : n);
  const some = (n, has) => (has(n) ? n : n.oneOf?.map(deref).find(has));
  let node = schema;
  for (const p of path) {
    node = deref(node);
    node = deref(p === "[]" ? some(node, (o) => o.items)?.items : some(node, (o) => o.properties?.[p])?.properties[p]);
  }
  if (!node?.enum) throw new Error(`no enum at ${path.join(".")}`);
  return node.enum.filter((v) => v !== null);
}

/** Every list, from the two contract schemas read out of `dir`. */
export function generate(dir) {
  const read = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
  const schemas = { view: read("view.schema.json"), run: read("run.schema.json") };
  const out = {};
  for (const [name, places] of Object.entries(PATHS)) {
    out[name] = [...new Set(places.flatMap(([which, ...p]) => enumAt(schemas[which], p)))];
  }
  for (const [name, [which, ...p]] of Object.entries(KEYS)) {
    let node = schemas[which];
    for (const step of p) node = (node.oneOf ? node.oneOf.find((o) => o.properties) : node).properties[step];
    out[name] = Object.keys(node.properties);
  }
  return out;
}

export const CONTRACT_DIR = new URL("../../../../contracts/empirica-ui-view/v1/", import.meta.url);
export const FILE = new URL("../enums.json", import.meta.url);
export const render = (enums) => `${JSON.stringify(enums, null, 2)}\n`;

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  fs.writeFileSync(FILE, render(generate(fileURLToPath(CONTRACT_DIR))));
  console.log("enums.json written");
}
