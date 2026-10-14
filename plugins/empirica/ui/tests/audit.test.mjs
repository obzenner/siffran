// Negative controls for scripts/audit.mjs: each rule must fire on a seeded violation.
import { test } from "vitest";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { audit, readTree } from "../scripts/audit.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const base = () => structuredClone(readTree(root));
const page = path.join("app", "app.js");
const meter = path.join("elements", "atoms", "ev-meter.js");
const has = (r, rule, text) => r.findings.some((f) => f.startsWith(rule) && (!text || f.includes(text)));

test("the real tree is clean", () => assert.deepEqual(audit(base()).findings, []));

test("an unused copy key is found even when a dynamic family shares its prefix", () => {
  const t = base(); t.copy["who.never-used"] = "x";
  assert.ok(has(audit(t), "C1 unused copy key", "who.never-used"));
});

test("a key a contract value needs but the copy lacks is missing (the families come from enums.json)", () => {
  const t = base(); delete t.copy["where.limit"];
  assert.ok(has(audit(t), "C1 missing copy key", "where.limit"));
  const u = base(); u.enums.who.push("robot");
  assert.ok(has(audit(u), "C1 missing copy key", "who.robot"));
});

test("an undeclared dynamic copy family is a finding", () => {
  const t = base(); t.code[page] += "\nconst x = t(`mystery.${y}`);";
  assert.ok(has(audit(t), "C1 undeclared dynamic copy family", "mystery.*"));
});

test("a missing key in a single-quoted t('…') call is found", () => {
  const t = base(); t.code[page] += "\nconst a = t('missing.copy.key');";
  assert.ok(has(audit(t), "C1 missing copy key", "missing.copy.key"));
});

test("single-quoted inline style, glued class interpolation and literal text are found", () => {
  const t = base();
  t.code[page] += "\nconst m = `<p style='color:red' class=\"text-${tone} p-s\">Hello there</p><b aria-label=\"Close it\"></b>`;";
  const r = audit(t);
  assert.ok(has(r, "E7 inline style attribute"));
  assert.ok(has(r, "E7 class built by concatenation", "text-${tone}"));
  assert.ok(has(r, "C1 literal text", "Hello there"));
  assert.ok(has(r, "C1 literal attribute", "Close it"));
  const u = base(); u.code[page] += "\nconst m = `<b aria-label='Literal close' class='text-${tone}'></b>`;";
  assert.ok(has(audit(u), "C1 literal attribute", "Literal close"));
});

test("a raw value in a class (a Tailwind arbitrary value) is found", () => {
  const t = base(); t.code[page] += "\nconst k = \"block h-[36rem] text-s\";";
  assert.ok(has(audit(t), "T1 raw value in a class", "h-[36rem]"));
});

test("an unused icon meaning and a missing one are found", () => {
  const t = base(); t.icons["never-drawn"] = "circle"; t.code[page] += '\nicon("not-declared");';
  const r = audit(t);
  assert.ok(has(r, "0.6 unused icon meaning", "never-drawn"));
  assert.ok(has(r, "S1 missing icon meaning", "not-declared"));
});

test("a value the UI does not know renders as the visible unknown entry, which has copy", () => {
  const t = base();
  assert.ok(t.states.unknown && t.icons[t.states.unknown.icon] && t.copy[`${t.states.unknown.copy}.label`]);
});

test("T1: a literal px or ms in code is found; a bare number is not", () => {
  const t = base(); t.code[meter] += '\nconst w = "12px"; const d = 150ms;';
  assert.ok(has(audit(t), "T1 raw unit", "12px") && has(audit(t), "T1 raw unit", "150ms"));
  const u = base(); u.code[meter] += "\nconst d = 150;";
  assert.ok(!has(audit(u), "T1 raw unit"));
});

test("T1: a bare delay in a timer is found; 0 and a catalogue read are not", () => {
  const t = base(); t.code[meter] += "\nsetTimeout(() => {}, 150);\nsetInterval(tick, 2.5);";
  assert.equal(audit(t).findings.filter((f) => f.startsWith("T1 bare duration")).length, 2);
  const ok = base(); ok.code[meter] += "\nsetTimeout(() => {}, 0);\nsetTimeout(() => {}, catalogue.copied_ms);";
  assert.ok(!has(audit(ok), "T1 bare duration"));
});

test("T1: unitless SVG geometry written as a literal is found, in an attribute and in an expression", () => {
  for (const [what, src] of [["attribute", '\nconst a = `<rect x="${x}" rx="7" class="a"></rect>`;'], ["stroke", '\nconst b = `<path d="M0 0" stroke-width="3"></path>`;'],
    ["expression", '\nconst c = `<rect x="${p.x + 4}" y="${p.y}" class="a"></rect>`;']]) {
    const t = base(); t.code[meter] += src;
    assert.ok(has(audit(t), "T1 unitless SVG literal"), what);
  }
});

test("T1: a hex colour in code or in app.css is found", () => {
  const t = base(); t.code[meter] += '\nconst c = "#ff0000";';
  assert.ok(has(audit(t), "T1 raw colour", "#ff0000"));
  const u = base(); u.css["styles/app.css"] += "\n.x { color: #123abc; }";
  assert.ok(has(audit(u), "T1 raw colour", "#123abc"));
  const ok = base(); ok.code[meter] += '\nconst e = `<use href="#sprite"></use>&#8250;`;';
  assert.ok(!has(audit(ok), "T1 raw colour"));
});

test("T1: a functional colour in code (the canvas's too) is found", () => {
  const t = base(); t.code[meter] += '\nctx.fillStyle = "rgba(0, 0, 0, 0.5)";';
  assert.ok(has(audit(t), "T1 raw colour", "rgba(0"));
  const u = base(); u.code[meter] += '\nconst c = `oklch(0.7 0.1 200)`;';
  assert.ok(has(audit(u), "T1 raw colour", "oklch(0"));
  const ok = base(); ok.code[meter] += '\nconst v = `var(--color-tone-human)`;';
  assert.ok(!has(audit(ok), "T1 raw colour"));
});

test("T8: an unused token, an unused semantic colour, an unused primitive and an unruled group are found", () => {
  const t = base(); t.tokens.spacing["9xl"] = { $value: "99rem" };
  assert.ok(has(audit(t), "T8 unused token", "spacing.9xl"));
  const u = base(); u.tokens.light.zz9 = { $value: "#000000" };
  assert.ok(has(audit(u), "T8 unused token", "light.zz9"));
  const v = base(); v.tokens.primitive.neutral.zz9 = { $value: "#010101" };
  assert.ok(has(audit(v), "T8 unused token", "primitive.neutral.zz9"));
  const w = base(); w.tokens.zz9 = { zz: { $value: "1px" } };
  assert.ok(has(audit(w), "T8 token group without a usage rule", "zz9"));
});

test("E10: an observed attribute nothing ever sets is found", () => {
  const t = base();
  t.code[meter] = t.code[meter].replace('"label", "used", "max", "show"', '"label", "used", "max", "show", "orphan"');
  assert.ok(has(audit(t), "E10 observed attribute never set", "orphan"));
});

test("C4: a placeholder the call does not supply, and a variable the copy does not use, are found", () => {
  const t = base(); t.copy["fact.when"] = "When {n}";
  t.code[page] += '\nconst z = t("fact.when", { total: 1 });';
  const r = audit(t);
  assert.ok(has(r, "C4 placeholder not supplied", "{n}") && has(r, "C4 variable the copy does not use", "total"));
});

