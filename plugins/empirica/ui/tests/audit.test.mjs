// Negative controls for scripts/audit.mjs: each rule must fire on a seeded violation (07 review, finding 8).
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { audit, readTree } from "../scripts/audit.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const base = () => structuredClone(readTree(root));
const gallery = path.join("gallery", "gallery.js");
const has = (r, rule, text) => r.findings.some((f) => f.startsWith(rule) && (!text || f.includes(text)));

test("the real tree is clean", () => assert.deepEqual(audit(base()).findings, []));

test("an unused copy key is found even when a dynamic family shares its prefix", () => {
  const t = base(); t.copy["gallery.never-used"] = "x";
  assert.ok(has(audit(t), "C1 unused copy key", "gallery.never-used"));
});

test("a key removed from the catalogue but produced by a dynamic family is missing", () => {
  const t = base(); delete t.copy["filter.blocks"];
  assert.ok(has(audit(t), "C1 missing copy key", "filter.blocks"));
});

test("an undeclared dynamic copy family is a finding", () => {
  const t = base(); t.code[gallery] += "\nconst x = t(`mystery.${y}`);";
  assert.ok(has(audit(t), "C1 undeclared dynamic copy family", "mystery.*"));
});

test("single-quoted inline style, glued class interpolation and literal text are found", () => {
  const t = base();
  t.code[gallery] += "\nconst m = `<p style='color:red' class=\"text-${tone} p-s\">Hello there</p><b aria-label=\"Close it\"></b>`;";
  const r = audit(t);
  assert.ok(has(r, "E7 inline style attribute"));
  assert.ok(has(r, "E7 class built by concatenation", "text-${tone}"));
  assert.ok(has(r, "C1 literal text", "Hello there"));
  assert.ok(has(r, "C1 literal attribute", "Close it"));
});

test("a tag mentioned only in a comment does not count as gallery coverage", () => {
  const t = base();
  t.code[gallery] = t.code[gallery].replaceAll("<ev-view", "<div").replaceAll('"ev-view"', '"x"') + '\n// "ev-view"';
  assert.ok(has(audit(t), "G1 element missing from gallery", "ev-view"));
});

test("a raw value in a class (a Tailwind arbitrary value) is found", () => {
  const t = base(); t.code[gallery] += "\nconst k = \"block h-[36rem] text-s\";";
  assert.ok(has(audit(t), "T1 raw value in a class", "h-[36rem]"));
  assert.ok(!has(audit(base()), "T1 raw value in a class", ""), "the tree itself has none");
});

test("an unused icon meaning and a missing one are found", () => {
  const t = base(); t.icons["never-drawn"] = "circle"; t.code[gallery] += '\nicon("not-declared");';
  const r = audit(t);
  assert.ok(has(r, "0.6 unused icon meaning", "never-drawn"));
  assert.ok(has(r, "S1 missing icon meaning", "not-declared"));
});

// Syntax variants from the 08 revalidation.
test("a missing key in a single-quoted t('…') call is found", () => {
  const t = base(); t.code[gallery] += "\nconst a = t('missing.copy.key');";
  assert.ok(has(audit(t), "C1 missing copy key", "missing.copy.key"));
});

test("single-quoted literal attributes and glued single-quoted classes are found", () => {
  const t = base(); t.code[gallery] += "\nconst m = `<b aria-label='Literal close' class='text-${tone}'></b>`;";
  const r = audit(t);
  assert.ok(has(r, "C1 literal attribute", "Literal close"));
  assert.ok(has(r, "E7 class built by concatenation", "text-${tone}"));
});

test("a trailing line comment does not count as gallery coverage", () => {
  const t = base();
  t.code[gallery] = t.code[gallery].replaceAll("<ev-view", "<div").replaceAll('"ev-view"', '"x"') + '\nconst x = 1; // "ev-view"';
  assert.ok(has(audit(t), "G1 element missing from gallery", "ev-view"));
});

test("a value the UI does not know renders as the visible unknown entry, which has copy", () => {
  const t = base();
  assert.ok(t.states.unknown && t.icons[t.states.unknown.icon]);
  for (const k of [`${t.states.unknown.copy}.label`, `${t.states.unknown.copy}.hint`]) assert.ok(t.copy[k], k);
});

test("an element shown only inside another is covered only while the gallery shows that other", () => {
  const t = base();
  t.code[gallery] = t.code[gallery].replaceAll("<ev-step ", "<div ");
  assert.ok(!has(audit(t), "G1 element missing from gallery", "ev-step"), "ev-steps draws ev-step");
  t.code[gallery] = t.code[gallery].replaceAll("<ev-steps", "<div");
  assert.ok(has(audit(t), "G1 element missing from gallery", "ev-step"));
});
