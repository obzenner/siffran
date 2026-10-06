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

// ---- the conventions debt of S3 (Q3a-8, Q3a-10, QA2 F8): each rule fires on a seeded violation -------------------
test("L3: an organism importing a template, and a page importing an organism, are found", () => {
  const t = base();
  t.code[path.join("elements", "organisms", "ev-detail.js")] += '\nimport { x } from "../templates/details.js";';
  assert.ok(has(audit(t), "L3 import against the levels", "ev-detail.js"));
  const u = base();
  u.code[path.join("app", "app.js")] += '\nimport { optionLabel } from "../elements/organisms/ev-filters.js";';
  assert.ok(has(audit(u), "L3 page imports below the elements' index", "ev-filters.js"));
  const v = base();
  v.code[path.join("elements", "atoms", "ev-state.js")] += '\nimport "../molecules/ev-file.js";';
  assert.ok(has(audit(v), "L3 import against the levels", "ev-state.js"));
});

test("T1: a literal px or ms in code is found; a bare number is not", () => {
  const t = base(); t.code[path.join("elements", "atoms", "ev-meter.js")] += '\nconst w = "12px"; const d = 150ms;';
  assert.ok(has(audit(t), "T1 raw unit", "12px") && has(audit(t), "T1 raw unit", "150ms"));
  const u = base(); u.code[path.join("elements", "atoms", "ev-meter.js")] += "\nconst d = 150;";
  assert.ok(!has(audit(u), "T1 raw unit"));
});

test("T8: a token nothing uses is found", () => {
  const t = base(); t.tokens.spacing["9xl"] = { $value: "99rem" };
  assert.ok(has(audit(t), "T8 unused token", "spacing.9xl"));
});

test("E10: an observed attribute nothing ever sets is found", () => {
  const t = base(); const f = path.join("elements", "atoms", "ev-meter.js");
  t.code[f] = t.code[f].replace('"label", "used", "max", "show"', '"label", "used", "max", "show", "orphan"');
  assert.ok(has(audit(t), "E10 observed attribute never set", "orphan"));
});

test("G4: a vocabulary the gallery does not show is found", () => {
  const t = base(); t.catalogue.gallery_state_domains = t.catalogue.gallery_state_domains.filter((d) => d !== "route");
  assert.ok(has(audit(t), "G4 state vocabulary missing", "route"));
});

test("C4: a placeholder the call does not supply, and a variable the copy does not use, are found", () => {
  const t = base(); t.copy["fact.runs"] = "Runs {n}";
  t.code[path.join("elements", "templates", "details.js")] += '\nconst z = t("fact.runs", { total: 1 });';
  const r = audit(t);
  assert.ok(has(r, "C4 placeholder not supplied", "{n}") && has(r, "C4 variable the copy does not use", "total"));
});

test("V5: two values of one vocabulary with the same glyph are found unless one is declared an alias", () => {
  const t = base(); t.states.audit.failed.icon = t.states.audit.passed.icon;
  assert.ok(has(audit(t), "V5 two values share a glyph"));
  t.states.audit.failed.alias = "passed";
  assert.ok(!has(audit(t), "V5 two values share a glyph"));
});

// ---- the blind spots R3 found (m2, D1): each fires on a seeded violation ------------------------------------------
test("L3: a dynamic import() across levels is found", () => {
  const t = base(); t.code[path.join("elements", "atoms", "ev-state.js")] += '\nconst late = () => import("../organisms/ev-view.js");';
  assert.ok(has(audit(t), "L3 import against the levels", "ev-state.js"));
  const ok = base(); ok.code[path.join("elements", "atoms", "ev-state.js")] += '\nconst fine = () => import("../core/element.js");';
  assert.ok(!has(audit(ok), "L3"));
});

test("T1: a bare delay in a timer is found; 0 and a token read are not", () => {
  const t = base(); t.code[path.join("elements", "atoms", "ev-meter.js")] += "\nsetTimeout(() => {}, 150);\nsetInterval(tick, 2.5);";
  assert.equal(audit(t).findings.filter((f) => f.startsWith("T1 bare duration")).length, 2);
  const ok = base(); ok.code[path.join("elements", "atoms", "ev-meter.js")] += "\nsetTimeout(() => {}, 0);\nsetTimeout(() => {}, grace());";
  assert.ok(!has(audit(ok), "T1 bare duration"));
});

test("T1: unitless SVG geometry written as a literal is found, in an attribute and in an expression", () => {
  const f = path.join("elements", "atoms", "ev-meter.js");
  for (const [what, src] of [["attribute", '\nconst a = `<rect x="${x}" rx="7" class="a"></rect>`;'], ["stroke", '\nconst b = `<path d="M0 0" stroke-width="3"></path>`;'],
    ["expression", '\nconst c = `<rect x="${p.x + 4}" y="${p.y}" class="a"></rect>`;'], ["decimal", '\nconst d = `<text y="${cy + t * 0.35}">x</text>`;']]) {
    const t = base(); t.code[f] += src;
    assert.ok(has(audit(t), "T1 unitless SVG literal"), what);
  }
  const ok = base(); ok.code[f] += '\nconst e = `<rect x="${p.x - 1}" y="${p.y / 2}" width="${U.w}" rx="${U.rx}" stroke-width="1"></rect><svg viewBox="0 0 24 24"></svg>`;';
  assert.ok(!has(audit(ok), "T1 unitless SVG literal", "ev-meter"), "0, 1, 2, named geometry and a viewBox are not geometry literals");
});

test("T8: an unused duration, an unused semantic colour, an unused primitive and an unruled group are found", () => {
  const t = base(); t.tokens.duration.zz9 = { $value: "1ms" };
  assert.ok(has(audit(t), "T8 unused token", "duration.zz9"));
  const u = base(); u.tokens.light.zz9 = { $value: "#000000" };
  assert.ok(has(audit(u), "T8 unused token", "light.zz9"));
  const v = base(); v.tokens.primitive.gray.zz9 = { $value: "#010101" };
  assert.ok(has(audit(v), "T8 unused token", "primitive.gray.zz9"));
  const w = base(); w.tokens.zz9 = { zz: { $value: "1px" } };
  assert.ok(has(audit(w), "T8 token group without a usage rule", "zz9"));
});

test("T1: a hex colour in code or in app.css is found", () => {
  const t = base(); t.code[path.join("elements", "atoms", "ev-meter.js")] += '\nconst c = "#ff0000";';
  assert.ok(has(audit(t), "T1 raw colour", "#ff0000"));
  const u = base(); u.css["styles/app.css"] += "\n.x { color: #123abc; }";
  assert.ok(has(audit(u), "T1 raw colour", "#123abc"));
  const ok = base(); ok.code[path.join("elements", "atoms", "ev-meter.js")] += '\nconst e = `<use href="#sprite"></use>&#8250;`;';
  assert.ok(!has(audit(ok), "T1 raw colour"));
});
