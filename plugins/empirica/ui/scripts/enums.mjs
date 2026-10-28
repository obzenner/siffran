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
  spike_outcome: [[...SHEET_CLAIM, "proof", "[]", "result", "outcome"]],
  mode: [["run", "governance", "mode"]],
  approval: [["run", "governance", "approval"]],
  why: [["run", "dropped", "[]", "why"]],
  answer: [["run", "decisions", "[]", "answer"]],
  effect: [["run", "decisions", "[]", "effect"]],
  // the profiles a spike backend may be bound to: the backend's own list, which validates it (app/client.js); the
  // profiles a claim's request and result name must agree with it (tests/contract.test.mjs)
  profile: [["run", "spike_backend", "profile_id"]],
};

/** Lists that are the names of an object's properties rather than an enum. */
export const KEYS = { budgets: ["run", "governance", "ceilings"], flag: [...ROW, "flags"] };

/** Patterns, by name: each is the one `pattern` every listed place declares (two that differ fail generation). */
export const PATTERNS = {
  digest: [["run", "spike_backend", "qualification_record_digest"], ["run", "spike_backend", "execution_binding_digest"]],
};

/** Closed values the client checks exactly, as forms: the nullable alternatives of a `oneOf`, one per `kind` const, each
 * its properties in schema order and each property as `{const}`, `{list}` (a PATHS list generated at that very place)
 * or `{pattern}` (a PATTERNS name declared at that very place). The projection holds only where its assumptions do,
 * and checks them: every alternative is a closed object (`additionalProperties: false`) whose properties are all
 * required, so the exact keys are the whole check, and a property carries one of those three shapes and nothing more.
 * Any other shape fails generation instead of being dropped: this is a projection of these forms, not a schema
 * interpreter. */
export const FORMS = { spike_backend: ["run", "spike_backend"] };
const ALTERNATIVE = new Set(["type", "additionalProperties", "required", "properties", "title", "description"]);

/** The schema node at a path of property names, looking through `$ref` and a nullable `oneOf`. */
function nodeAt(schema, steps) {
  const deref = (n) => (n?.$ref ? deref(schema.$defs[n.$ref.replace("#/$defs/", "")]) : n);
  const some = (n, has) => (has(n) ? n : n.oneOf?.map(deref).map((o) => some(o, has)).find(Boolean));
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
  const at = (which, p) => [which, ...p].join(".");
  const placed = (table, place) => Object.keys(table).filter((n) => table[n].some(([w, ...p]) => at(w, p) === place));
  out.patterns = {};
  for (const [name, places] of Object.entries(PATTERNS)) {
    const found = [...new Set(places.map(([which, ...p]) => nodeAt(schemas[which], p)?.pattern))];
    if (found.length !== 1 || typeof found[0] !== "string") throw new Error(`pattern ${name}: the places declare ${found.length} patterns`);
    out.patterns[name] = found[0];
  }
  for (const [name, [which, ...p]] of Object.entries(FORMS)) {
    const schema = schemas[which];
    const deref = (n) => (n?.$ref ? deref(schema.$defs[n.$ref.replace("#/$defs/", "")]) : n);
    const flat = (n) => (n.oneOf ? n.oneOf.map(deref).flatMap(flat) : [n]);
    const alternatives = flat(nodeAt(schema, p));
    const form = { nullable: alternatives.some((a) => a.type === "null"), kinds: {} };
    for (const alt of alternatives.filter((a) => a.type !== "null")) {
      const where = `${name}: an alternative`;
      const extra = Object.keys(alt).filter((k) => !ALTERNATIVE.has(k));
      if (alt.type !== "object" || alt.additionalProperties !== false || extra.length) throw new Error(`${where} is not a closed object (${extra})`);
      const fields = Object.keys(alt.properties ?? {});
      if (fields.length !== (alt.required ?? []).length || fields.some((f) => !alt.required.includes(f))) throw new Error(`${where} has an optional property`);
      const kind = alt.properties.kind?.const;
      if (typeof kind !== "string" || kind in form.kinds) throw new Error(`${where} has no kind const of its own`);
      const own = {};
      for (const field of fields) {
        const prop = alt.properties[field], keys = Object.keys(prop).filter((k) => k !== "description").sort().join(",");
        const place = at(which, [...p, field]);
        if (keys === "const") own[field] = { const: prop.const };
        else if (keys === "enum" && placed(PATHS, place).length === 1) own[field] = { list: placed(PATHS, place)[0] };
        else if (keys === "pattern,type" && prop.type === "string" && placed(PATTERNS, place).length === 1) own[field] = { pattern: placed(PATTERNS, place)[0] };
        else throw new Error(`${name}.${kind}.${field}: a shape this projection does not check (${keys})`);
        if (own[field].list && JSON.stringify(out[own[field].list]) !== JSON.stringify(prop.enum.filter((v) => v !== null))) throw new Error(`${name}.${kind}.${field}: not its list`);
      }
      form.kinds[kind] = own;
    }
    out[name] = form;
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
