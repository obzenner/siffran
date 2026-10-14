// elements/views/rows.js: one run as one line. Property tests over generated page lines (fast-check, fixed seed, bounded)
// for each part the row says: the ask chip and its host and session, the goal clipped or unread, the time (asked or
// updated), the attempt badge, the link, the current and the keyboard's row, the spacers of a window. Strings in, no DOM.
import assert from "node:assert/strict";
import { test } from "vitest";
import fc from "fast-check";
globalThis.HTMLElement ??= class {};
const { rowHtml, windowHtml } = await import("../elements/views/rows.js");
const { ago, esc, stateHtml, t } = await import("../elements/core/element.js");
const copy = (await import("../copy/en.json", { with: { type: "json" } })).default;
const enums = (await import("../enums.json", { with: { type: "json" } })).default;

const RUNS = { seed: 20261015, numRuns: 120 };
const NOW = Date.UTC(2026, 9, 13, 12);
const standings = enums.standing;
const asks = Object.keys(copy).filter((k) => /^ask\.[a-z_]+$/.test(k)).map((k) => k.slice(4));
const word = fc.stringMatching(/^[A-Za-z0-9 <>&"'·.-]{0,200}$/);
const id = fc.stringMatching(/^[0-9a-f]{12}$/);
const line = fc.record({
  id, key: fc.stringMatching(/^[a-z]{1,8}\/r[0-9]{1,3}\/[1-3]$/), standing: fc.constantFrom(...standings),
  ask: fc.option(fc.constantFrom(...asks)), goal: fc.option(word), host: fc.option(fc.constantFrom("claude", "pi", "other-host")),
  session: fc.option(word), generation: fc.integer({ min: 1, max: 5 }), project_label: word,
  asked_at: fc.option(fc.integer({ min: (NOW / 1000) - 9e6, max: NOW / 1000 })), updated: fc.option(fc.integer({ min: (NOW / 1000) - 9e6, max: NOW / 1000 })),
});
const ctx = (extra = {}) => ({ href: (x) => `?run=${x}`, now: NOW, open: null, here: null, ...extra });
const chip = (html) => /<span class="shrink-0 rounded-m bg-tone-human-tint px-xs text-xs font-medium text-tone-human">([^<]*)<\/span>/.exec(html)?.[1] ?? null;
const hostPart = (html) => /<span class="ev-optional hidden shrink-0 text-xs text-muted-foreground md:inline">([^<]*)<\/span>/.exec(html)?.[1] ?? null;
const goalPart = (html) => /<span class="min-w-0 flex-1 truncate" data-goal>(.*?)<\/span>\s*\n/s.exec(html)?.[1];
const timePart = (html) => /<span class="w-time [^"]*" title="[^"]*" data-time>([^<]*)<\/span>/.exec(html)?.[1];
const timeTitle = (html) => /<span class="w-time [^"]*" title="([^"]*)" data-time>/.exec(html)?.[1];

test("only a run filed under Needs a human with an ask shows the ask chip, in the ask's words, then its host and session", () => {
  fc.assert(fc.property(line, (r) => {
    const html = rowHtml(r, ctx()), asked = r.standing === "needs_human" && r.ask !== null;
    assert.equal(chip(html), asked ? t(`ask.${r.ask}`) : null);
    // the host's name (an unknown host by its id), then the session, each left out when there is none
    const said = [r.host === null ? null : (copy[`host.${r.host}`] ?? esc(r.host)), r.session ? esc(r.session) : null].filter(Boolean).join(copy["list.join"]);
    assert.equal(hostPart(html), asked ? said : null);
    assert.equal(/bg-tone-human-tint/.test(html), asked);
  }), RUNS);
});

test("an asking run with no host recorded says only its session; with neither, nothing beside its ask", () => {
  const r = { id: "c".repeat(12), key: "k", standing: "needs_human", ask: asks[0], goal: "g", host: null, session: "s1", generation: 1, project_label: "p", asked_at: null, updated: null };
  assert.equal(hostPart(rowHtml(r, ctx())), "s1");
  assert.equal(hostPart(rowHtml({ ...r, session: null }, ctx())), "");
  assert.equal(hostPart(rowHtml({ ...r, host: "pi" }, ctx())), `${copy["host.pi"]}${copy["list.join"]}s1`);
});

test("the time is when it was asked for a run asking, else when it was last updated", () => {
  fc.assert(fc.property(line, (r) => {
    const asked = r.standing === "needs_human" && r.ask !== null;
    const at = asked ? r.asked_at : r.updated, when = typeof at === "number" ? ago(at, NOW) : t("time.unknown.short");
    assert.equal(timePart(rowHtml(r, ctx())), esc(asked ? t("row.asked", { when }) : when));
    assert.equal(timeTitle(rowHtml(r, ctx())), esc(asked ? t("row.asked", { when: ago(at, NOW) }) : ago(at, NOW)), "the whole time in its title");
  }), RUNS);
  const r = { id: "a".repeat(12), key: "k", standing: "needs_human", ask: asks[0], goal: "g", host: null, session: null, generation: 1, project_label: "p", asked_at: NOW / 1000 - 7200, updated: NOW / 1000 - 60 };
  assert.equal(timePart(rowHtml(r, ctx())), esc(t("row.asked", { when: ago(NOW / 1000 - 7200, NOW) })));
  assert.notEqual(ago(NOW / 1000 - 7200, NOW), ago(NOW / 1000 - 60, NOW), "the two times differ, so the row says the one it should");
  assert.equal(timePart(rowHtml({ ...r, standing: "proven" }, ctx())), esc(ago(NOW / 1000 - 60, NOW)));
  assert.equal(timePart(rowHtml({ ...r, ask: null }, ctx())), esc(ago(NOW / 1000 - 60, NOW)), "needs a human but nothing asked: the update");
});

test("a time not recorded is a short word in the fixed time column, the whole sentence in its title", () => {
  const r = { id: "a".repeat(12), key: "k", standing: "needs_human", ask: asks[0], goal: "g", host: null, session: null, generation: 1, project_label: "p", asked_at: null, updated: null };
  const html = rowHtml(r, ctx());
  assert.equal(timePart(html), esc(t("row.asked", { when: t("time.unknown.short") })));
  assert.equal(timeTitle(html), esc(t("row.asked", { when: t("time.unknown") })));
  assert.ok(timePart(html).length <= "asked 10 days ago".length, "no longer than a relative time");
  assert.ok(timePart(rowHtml({ ...r, standing: "proven" }, ctx())) === esc(t("time.unknown.short")));
});

test("the goal is escaped and clipped to 140 characters with an ellipsis; an unread goal says so", () => {
  fc.assert(fc.property(line, (r) => {
    const g = goalPart(rowHtml(r, ctx()));
    if (r.goal === null) { assert.equal(g, `<span class="text-muted-foreground">${t("goal.unread")}</span>`); return; }
    const plain = r.goal.replace(/\s+/g, " ").trim();
    const want = plain.length > 140 ? `${plain.slice(0, 139).trimEnd()}…` : plain;
    assert.equal(g, esc(want));
  }), RUNS);
  assert.equal(goalPart(rowHtml({ id: "b".repeat(12), key: "k", standing: "proven", ask: null, goal: "x".repeat(141), host: null, session: null, generation: 1, project_label: "p", asked_at: null, updated: null }, ctx())), `${"x".repeat(139)}…`);
  assert.equal(goalPart(rowHtml({ id: "b".repeat(12), key: "k", standing: "proven", ask: null, goal: "x".repeat(140), host: null, session: null, generation: 1, project_label: "p", asked_at: null, updated: null }, ctx())), "x".repeat(140));
});

test("an attempt after the first says its number; the first says nothing", () => {
  fc.assert(fc.property(line, (r) => {
    const html = rowHtml(r, ctx()), badge = /<span class="[^"]*" data-attempt-badge>([^<]*)<\/span>/.exec(html)?.[1] ?? null;
    assert.equal(badge, r.generation > 1 ? t("row.attempt.short", { n: r.generation }) : null);
  }), RUNS);
});

test("a row is one link to its run, its glyph first, current when open, in the tab order only when the keyboard is on it", () => {
  fc.assert(fc.property(line, fc.boolean(), fc.boolean(), (r, open, here) => {
    const html = rowHtml(r, ctx({ open: open ? r.id : "zzz", here: here ? r.id : "zzz" }));
    assert.match(html, new RegExp(`^<li data-row="${esc(r.key).replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}" data-id="${r.id}" class="border-b border-border">`));
    assert.equal((html.match(/<a /g) ?? []).length, 1);
    assert.ok(html.includes(`href="?run=${r.id}"`) && html.includes(`data-run="${r.id}"`));
    const tag = new RegExp(`data-run="${r.id}" ${open ? 'aria-current="true"' : ""} ${here ? "" : 'tabindex="-1"'}>`);
    assert.match(html, tag, "current when open, out of the tab order unless the keyboard is on it, nothing else");
    assert.ok(html.indexOf(stateHtml("standing", r.standing, "glyph")) < html.indexOf("data-goal"), "the glyph leads");
    assert.ok(html.includes(`data-project>${esc(r.project_label)}</span>`));
    assert.doesNotMatch(html, /undefined|null|NaN/);
  }), RUNS);
});

test("a window is its rows between two spacers, in order", () => {
  fc.assert(fc.property(fc.uniqueArray(line, { selector: (r) => r.id, maxLength: 8 }), (rs) => {
    const html = windowHtml(rs, ctx());
    assert.ok(html.startsWith(`<ul class="flex flex-col" data-rows><li aria-hidden="true" data-before></li>`) && html.endsWith(`<li aria-hidden="true" data-after></li></ul>`));
    assert.deepEqual([...html.matchAll(/data-id="([0-9a-f]{12})"/g)].map((m) => m[1]), rs.map((r) => r.id));
    assert.equal(html, `<ul class="flex flex-col" data-rows><li aria-hidden="true" data-before></li>${rs.map((r) => rowHtml(r, ctx())).join("")}<li aria-hidden="true" data-after></li></ul>`, "each row exactly, nothing between");
  }), RUNS);
});
