// The UI's value lists against the observer contract (contracts/empirica-ui-view/v1): enums.json is generated from the
// contract and must equal what is generated now, and every state vocabulary in states.json is keyed by exactly one
// contract list, no more and no fewer values, so a value the plugin adds or drops fails here before it renders as
// "Unknown".
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
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
  const copyOnly = ["independence", "kind", "who", "where", "record_class", "mode", "approval", "budgets", "spike", "spike_outcome", "ask", "effect", "flag", "step_standing", "profile"];
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

// The spike backend the run sheet carries: its profiles, the closed forms of its alternatives and its digest pattern are
// the contract's, projected by scripts/enums.mjs, so app/client.js validates with no copied fact.
const runSchema = read(new URL("run.schema.json", CONTRACT_DIR));
const alternatives = runSchema.$defs.spikeBackend.oneOf;
const declaredProfiles = (node, at = "") => (node === null || typeof node !== "object" ? [] : [
  ...(at.endsWith("/properties/profile_id") && node.enum ? [node.enum] : []),
  ...Object.entries(node).flatMap(([k, v]) => declaredProfiles(v, `${at}/${k}`))]);

test("the validating profile list is the backend's own, and every other profile the contract declares agrees with it", () => {
  const backend = alternatives.flatMap((alt) => alt.properties.profile_id?.enum ?? []);
  assert.deepEqual(enums.profile, backend);
  const all = declaredProfiles(runSchema);
  assert.ok(all.length >= 3, "the request and the result declare a profile too");
  for (const list of all) assert.deepEqual([...list].sort(), [...enums.profile].sort());
});

test("the spike backend's forms are its alternatives: one per kind, exactly their properties, nullable as the run says", () => {
  const forms = enums.spike_backend;
  assert.equal(forms.nullable, runSchema.properties.spike_backend.oneOf.some((o) => o.type === "null"));
  assert.deepEqual(Object.keys(forms.kinds).sort(), alternatives.map((alt) => alt.properties.kind.const).sort());
  for (const alt of alternatives) {
    const form = forms.kinds[alt.properties.kind.const];
    assert.deepEqual(Object.keys(form), Object.keys(alt.properties));
    assert.deepEqual(Object.keys(form).sort(), [...alt.required].sort());
    for (const [field, schema] of Object.entries(alt.properties)) {
      if ("const" in schema) assert.deepEqual(form[field], { const: schema.const }, field);
      else if (schema.enum) assert.deepEqual(enums[form[field].list], schema.enum, field);
      else assert.equal(enums.patterns[form[field].pattern], schema.pattern, field);
    }
  }
  assert.deepEqual(Object.keys(enums.patterns), ["digest"]);
});

/** generate() over a copy of the contract whose run schema `change` edits: it must refuse each shape it does not project. */
function generateEdited(change) {
  const dir = mkdtempSync(path.join(tmpdir(), "enums-"));
  try {
    const run = structuredClone(runSchema);
    change(run.$defs.spikeBackend.oneOf.find((alt) => alt.properties.kind.const === "qualified"), run);
    writeFileSync(path.join(dir, "run.schema.json"), JSON.stringify(run));
    writeFileSync(path.join(dir, "row.schema.json"), readFileSync(new URL("row.schema.json", CONTRACT_DIR)));
    return generate(dir);
  } finally { rmSync(dir, { recursive: true, force: true }); }
}

test("the projection's assumptions are checked: a backend shape it does not project fails generation (negative controls)", () => {
  assert.deepEqual(generateEdited(() => {}), generateAll(), "the unedited copy generates the same file");
  const refused = {
    "an open object": (alt) => { alt.additionalProperties = true; },
    "an optional property": (alt) => { alt.required = alt.required.filter((f) => f !== "backend"); },
    "a constraint it does not project": (alt) => { alt.properties.qualification_record_digest.maxLength = 71; },
    "an unsupported field shape": (alt) => { alt.properties.backend = { type: "string" }; },
    "a kind with no const": (alt) => { alt.properties.kind = { enum: ["qualified"] }; },
    "two digest patterns": (alt) => { alt.properties.execution_binding_digest.pattern = "^sha256:[0-9a-f]{40}$"; },
    "a keyword on the alternative": (alt) => { alt.minProperties = 1; },
    "an enum with no generated list": (alt) => { alt.properties.backend = { enum: ["docker"] }; },
  };
  for (const [what, change] of Object.entries(refused)) assert.throws(() => generateEdited(change), Error, what);
});
