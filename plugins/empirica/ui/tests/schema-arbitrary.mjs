// fast-check arbitraries read from the observer read model's v1 JSON Schemas (contracts/empirica-ui-view/v1), T1 B: one
// arbitrary per schema node, derived from its keywords alone, so every value the contract declares is a value the UI is
// given. The keywords are the ones the contract uses; any other is refused, so a schema that grows one fails here
// instead of being generated loosely. `seen` (optional) records, by schema#pointer, the enumeration values generated,
// which is how a test proves every one producible. The Python twin is scripts/ui_view_strategies.py.
import fc from "fast-check";
import { readFileSync } from "node:fs";
import { CONTRACTS as dir } from "./paths.mjs";

export const schemas = Object.fromEntries(["summary", "row", "page", "run"].map((n) => [n, JSON.parse(readFileSync(new URL(`${n}.schema.json`, dir), "utf8"))]));
const byId = Object.fromEntries(Object.values(schemas).map((s) => [s.$id, s]));
const KNOWN = new Set(["$schema", "$id", "$defs", "$ref", "title", "description", "type", "enum", "const", "oneOf", "properties", "required", "additionalProperties", "items", "minItems", "maxItems", "minimum", "maximum", "minLength", "pattern"]);
const MAX_ITEMS = 3;
const name = (doc) => doc.$id.split("/").pop();

/** Every enumeration the schemas declare, by `schema#pointer` (the pointer of the node in its own document). */
export function declared() {
  const out = {};
  const walk = (node, doc, pointer) => {
    if (Array.isArray(node)) return node.forEach((n, i) => walk(n, doc, `${pointer}/${i}`));
    if (node === null || typeof node !== "object") return;
    if (node.enum) out[`${name(doc)}#${pointer}`] = node.enum;
    for (const [k, v] of Object.entries(node)) walk(v, doc, `${pointer}/${k}`);
  };
  for (const doc of Object.values(schemas)) walk(doc, doc, "");
  return out;
}

const PATTERNS = {
  "^[0-9a-f]{12}$": () => fc.string({ unit: fc.constantFrom(..."0123456789abcdef"), minLength: 12, maxLength: 12 }),
  "^[0-9]{4}-[0-9]{2}-[0-9]{2}$": () => fc.date({ min: new Date(0), max: new Date(4102444800000), noInvalidDate: true }).map((d) => d.toISOString().slice(0, 10)),
  "^sha256:[0-9a-f]{64}$": () => fc.string({ unit: fc.constantFrom(..."0123456789abcdef"), minLength: 64, maxLength: 64 }).map((h) => `sha256:${h}`),
  "^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$": () => fc.string({ unit: fc.constantFrom(..."0123456789abcdef"), minLength: 64, maxLength: 64 }).map((h) => `example/probe@sha256:${h}`),
  "^[A-Za-z_][A-Za-z0-9_]{0,127}$": () => fc.string({ unit: fc.constantFrom(..."ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_"), minLength: 1, maxLength: 12 }),
};

/** The arbitraries of one set of schemas. `full`: every optional property present, every array non-empty and a null
 * alternative never taken where another exists (one document then reaches every enumeration node). */
export function arbitraries({ full = false, seen = null } = {}) {
  const memo = new Map();
  const node = (n, doc, pointer) => {
    const unknown = Object.keys(n).filter((k) => !KNOWN.has(k));
    if (unknown.length) throw new Error(`${doc.$id}#${pointer}: keywords this generator does not derive: ${unknown}`);
    if (n.$ref) {
      const [url, fragment] = n.$ref.split("#");
      const target = url ? byId[url] : doc;
      const key = `${target.$id}#${fragment}`;
      if (!memo.has(key)) {
        let at = target;
        for (const p of fragment.split("/").filter(Boolean)) at = at[p];
        memo.set(key, fc.constant(null).chain(() => node(at, target, fragment)));  // lazy, so a reference may recur
      }
      return memo.get(key);
    }
    if ("const" in n) return fc.constant(n.const);
    if (n.enum) {
      const at = `${name(doc)}#${pointer}`;
      const pick = fc.constantFrom(...n.enum);
      return seen ? pick.map((v) => { (seen[at] ??= new Set()).add(v); return v; }) : pick;
    }
    if (n.oneOf) {
      let branches = n.oneOf.map((b, i) => [b, i]);
      if (full && branches.length > 1) branches = branches.filter(([b]) => b.type !== "null");
      return fc.oneof(...branches.map(([b, i]) => node(b, doc, `${pointer}/oneOf/${i}`)));
    }
    if (n.type === undefined) throw new Error(`${doc.$id}#${pointer}: a node with no type`);
    let types = [n.type].flat();
    if (full && types.length > 1) types = types.filter((t) => t !== "null");
    return fc.oneof(...types.map((t) => typed(t, n, doc, pointer)));
  };
  const typed = (kind, n, doc, pointer) => {
    switch (kind) {
      case "null": return fc.constant(null);
      case "boolean": return fc.boolean();
      case "integer": return fc.integer({ min: n.minimum ?? -1e6, max: n.maximum ?? 1e9 });
      case "number": return fc.oneof(fc.integer({ min: n.minimum ?? -1e6, max: n.maximum ?? 2e9 }), fc.double({ min: n.minimum ?? -1e6, max: n.maximum ?? 2e9, noNaN: true, noDefaultInfinity: true }));
      case "string":
        if (n.pattern) {
          if (!PATTERNS[n.pattern]) throw new Error(`${doc.$id}#${pointer}: pattern ${n.pattern}`);
          return PATTERNS[n.pattern]();
        }
        return fc.string({ unit: "grapheme", minLength: n.minLength ?? 0, maxLength: Math.max(12, n.minLength ?? 0) });
      case "array": {
        const min = Math.max(n.minItems ?? 0, full ? 1 : 0);
        return fc.array(node(n.items, doc, `${pointer}/items`), { minLength: min, maxLength: Math.min(n.maxItems ?? MAX_ITEMS, Math.max(min, MAX_ITEMS)) });
      }
      case "object": {
        const props = n.properties ?? {}, required = new Set(n.required ?? []);
        const record = {};
        for (const [k, v] of Object.entries(props)) record[k] = node(v, doc, `${pointer}/properties/${k}`);
        const whole = fc.record(record, full ? {} : { requiredKeys: [...required] });
        if (n.additionalProperties && typeof n.additionalProperties === "object") {
          const more = fc.dictionary(fc.string({ maxLength: 8 }).filter((k) => !(k in props) && k !== "__proto__"), node(n.additionalProperties, doc, `${pointer}/additionalProperties`), { maxKeys: MAX_ITEMS });
          return fc.tuple(whole, more).map(([w, m]) => ({ ...m, ...w }));
        }
        return whole;
      }
      default: throw new Error(`${doc.$id}#${pointer}: type ${kind}`);
    }
  };
  const top = (n) => node(schemas[n], schemas[n], "");
  return {
    summary: top("summary"), rows: top("row"), page: top("page"), run: top("run"),
    row: node(schemas.row.$defs.row, schemas.row, "/$defs/row"),
  };
}
