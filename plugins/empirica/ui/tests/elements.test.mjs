// @vitest-environment happy-dom
// The custom elements (elements/atoms, elements/organisms) in happy-dom, from the real-service fixtures: what each shows, by
// its words and counts, and the Universe by what it tells its canvas to draw, against app/geometry.js's own answers (the
// canvas is a recording stub, tests/dom.mjs). The headless-Chrome journeys stay the proof of real rendering and layout.
import assert from "node:assert/strict";
import { beforeEach, test, vi } from "vitest";
import catalogue from "../catalogue.json" with { type: "json" };
import enums from "../enums.json" with { type: "json" };
import { colours, drawn, flush, install } from "./dom.mjs";

install({ catalogue });
const { clip, plain, stateOf, t } = await import("../elements/core/element.js");
await import("../elements/index.js");
const { railHtml, stripHtml, calendarHtml, groupsHtml } = await import("../elements/views/inbox.js");
const { sheetHtml } = await import("../elements/views/sheet.js");
const { windowFor, revealTop } = await import("../app/list.js");
const { layout, shapePath } = await import("../app/universe.js");
const { bounds, cardBox, claimGraph, fitView, footprint, keyBox, markRadius, toScreen, viewInset } = await import("../app/geometry.js");
const { line, rows, scenarios, sheets, summary } = await import("./fixtures.mjs");

const G = catalogue.universe, TURN = 2 * Math.PI;
const href = (changes) => `?x=${encodeURIComponent(JSON.stringify(changes))}`;
const place = { view: "inbox", period: catalogue.default_period, since: null, q: "", run: null, claim: null, words: false, zoom: null, hl: null, filters: {} };
/** HTML as the DOM serialises it, so a view's string and an element's markup compare. */
const norm = (html) => { const x = document.createElement("template"); x.innerHTML = html; return x.innerHTML; };
/** What a person reads in `el`: its text pieces, in order, one space apart. */
const text = (el) => {
  const out = [], walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) { const w = n.textContent.replace(/\s+/g, " ").trim(); if (w) out.push(w); }
  return out.join(" ");
};
function mount(tag, data, attrs = {}) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (data !== undefined) el.data = data;
  document.body.append(el);
  return el;
}
beforeEach(() => { document.body.innerHTML = ""; drawn.length = 0; flush(); });

// ---- atoms ----------------------------------------------------------------------------------------------------------------
test("ev-meter says how much of a budget is used, and marks it full only at its max", () => {
  const el = mount("ev-meter", undefined, { label: "budget.passes", used: "3", max: "10" });
  assert.equal(text(el), `${plain("budget.passes")} ${plain("budget.of", { used: 3, max: 10 })} ${plain("budget.of", { used: 3, max: 10 })}`);
  const meter = el.querySelector("meter");
  assert.deepEqual([meter.getAttribute("value"), meter.getAttribute("max"), meter.hasAttribute("data-full")], ["3", "10", false]);
  assert.equal(meter.getAttribute("aria-label"), `${plain("budget.passes")}: ${plain("budget.of", { used: 3, max: 10 })}`);
  el.setAttribute("used", "10");
  assert.ok(el.querySelector("meter").hasAttribute("data-full"));
  el.setAttribute("show", "amount");
  assert.equal(el.querySelector("span span").children.length, 1, "the amount alone");
  assert.ok(!mount("ev-meter", undefined, { used: "0", max: "0" }).querySelector("meter").hasAttribute("data-full"), "no budget is never full");
});

test("ev-copy shows its value, copies it, says so for copied_ms, and says when the browser refused", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  try {
    const wrote = [];
    navigator.clipboard.writeText = async (v) => { wrote.push(v); };
    const el = mount("ev-copy", undefined, { text: "a <b> & c", label: "fact.session" });
    assert.equal(el.querySelector("[data-value]").textContent, "a <b> & c");
    const button = () => el.querySelector("[data-copy]");
    assert.equal(button().getAttribute("aria-label"), `${plain("copy.button")}: ${plain("fact.session")}`);
    assert.equal(button().textContent, plain("copy.button"));
    button().click(); await Promise.resolve(); await Promise.resolve();
    assert.deepEqual([wrote, button().textContent], [["a <b> & c"], plain("copy.done")]);
    vi.advanceTimersByTime(catalogue.copied_ms - 1); assert.equal(button().textContent, plain("copy.done"));
    vi.advanceTimersByTime(1); assert.equal(button().textContent, plain("copy.button"));
    navigator.clipboard.writeText = async () => { throw new Error("denied"); };
    button().click(); await Promise.resolve(); await Promise.resolve();
    assert.equal(button().textContent, plain("copy.failed"));
    el.remove(); vi.advanceTimersByTime(catalogue.copied_ms);  // its timer went with it
    assert.equal(mount("ev-copy", undefined, { text: "x" }).querySelector("[data-copy]").getAttribute("aria-label"), plain("copy.button"));
  } finally { vi.useRealTimers(); }
});

test("ev-more offers Show all only when the text overflows two lines, and opens and folds in place", () => {
  const tall = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function () { return this.hasAttribute("data-text") ? 80 : 0; });
  const box = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get() { return this.hasAttribute("data-text") ? 40 : box.get.call(this); } });
  try {
    const el = mount("ev-more", undefined, { text: "line one\nline <two>", label: "compare.goal" });
    assert.equal(el.querySelector("[data-text]").textContent, "line one\nline <two>");
    assert.match(text(el), new RegExp(`^${plain("compare.goal")}`));
    const more = () => el.querySelector("[data-more]");
    assert.deepEqual([more().hidden, more().textContent, more().getAttribute("aria-expanded")], [false, plain("more.all"), "false"]);
    assert.match(el.querySelector("[data-text]").className, /line-clamp-2/);
    more().click();
    assert.deepEqual([more().hidden, more().textContent, more().getAttribute("aria-expanded")], [false, plain("more.less"), "true"]);
    assert.doesNotMatch(el.querySelector("[data-text]").className, /line-clamp-2/);
    el.setAttribute("text", "redrawn");  // the open state survives a redraw
    assert.equal(more().textContent, plain("more.less"));
    more().click();
    assert.equal(more().textContent, plain("more.all"));
    tall.mockImplementation(function () { return this.hasAttribute("data-text") ? 41 : 0; });
    el.setAttribute("text", "short");
    assert.ok(more().hidden, "a text that fits shows no control");
    assert.equal(mount("ev-more", undefined, { text: "x" }).querySelectorAll("span").length, 1, "no caption without a label");
  } finally { tall.mockRestore(); Object.defineProperty(HTMLElement.prototype, "clientHeight", box); }
});

// ---- organisms --------------------------------------------------------------------------------------------------------------
test("ev-rail and ev-inbox show the summary's buckets, the strip, the calendar and the loaded groups, and nothing before it", () => {
  const go = (f) => href(f);
  const rail = mount("ev-rail", null);
  assert.equal(rail.innerHTML, "");
  rail.data = { summary, place, go };
  assert.equal(rail.innerHTML, norm(railHtml(summary, place, go)));
  for (const p of summary.projects) assert.ok(text(rail).includes(p.label), p.label);
  const standings = summary.marks.standings.filter((s) => (summary.facets.standing[s] ?? 0) > 0);
  const loaded = new Map(standings.map((s) => [s, rows.filter((r) => r.standing === s).map(line)]));
  const ctx = { href: (id) => `?run=${id}`, now: 0, open: null, here: null, all: (s) => `?standing=${s}` };
  const d = { summary, place, loaded, ctx, statGo: go, periodHref: go, calendarGo: go, nowMs: 1_790_610_000_000, empty: "list.empty.demo" };
  const inbox = mount("ev-inbox", null);
  assert.equal(inbox.innerHTML, "");
  inbox.data = d;
  assert.equal(inbox.querySelector("h1").textContent, plain("inbox.title"));
  for (const part of [stripHtml(summary, place, { go, periodHref: go }), calendarHtml(summary, place, { nowMs: d.nowMs, go }), groupsHtml(summary, loaded, { ...ctx, empty: d.empty })]) assert.ok(inbox.innerHTML.includes(norm(part)));
  assert.deepEqual([...inbox.querySelectorAll("[data-group]")].map((g) => g.dataset.group), standings);
  assert.equal(inbox.querySelectorAll("[data-row]").length, Math.min(rows.length, [...loaded.values()].flat().length));
  for (const s of standings) assert.ok(text(inbox).includes(plain(`${stateOf("standing", s).copy}.label`)), s);
  assert.ok(inbox.querySelector("[data-calendar]") && inbox.querySelector("[data-stat]"));
  inbox.data = { ...d, summary: { ...summary, facets: { ...summary.facets, standing: {} } } };
  assert.ok(text(inbox).includes(plain("list.empty.demo")), "an empty store says so");
});

test("ev-sheet says it is loading, or why the sheet is not here, and then shows the run's sheet", () => {
  const el = mount("ev-sheet", null);
  assert.equal(el.innerHTML, "");
  el.data = { pending: true, notice: null };
  assert.equal(el.querySelector("[data-title]").textContent, plain("sheet.loading"));
  assert.ok(el.querySelector("[data-close]"));
  el.data = { pending: true, notice: "detail.offline" };
  assert.equal(el.querySelector("[data-title]").textContent, plain("sheet.unavailable"));
  assert.ok(text(el).includes(plain("detail.offline")));
  const s = sheets[scenarios.verification];
  const ctx = { row: s.row, project: summary.projects.find((p) => p.id === s.row.project), detail: s, attempts: s.attempts, notice: null, claim: null, now: 0, href };
  el.data = ctx;
  const want = document.createElement("template"); want.innerHTML = sheetHtml(ctx);
  const sections = (root) => [...root.querySelectorAll("[data-section]")].map((x) => x.dataset.section);
  assert.ok(sections(el).length > 2);
  assert.deepEqual(sections(el), sections(want.content));
  assert.equal(el.querySelector("[data-title]").textContent, s.row.goal);
  assert.ok(text(el).includes(s.row.goal.slice(0, 40)));
  assert.equal(el.querySelectorAll("[data-claim]").length > 0, (s.row.claims ?? []).length > 0);
});

test("ev-work draws a window of rows, counts them, says what it waits for, and reveals a row", () => {
  const many = Array.from({ length: 120 }, (_, i) => ({ ...line(rows[i % rows.length]), key: `k${i}`, id: `r${i}` }));
  const walk = { rows: many, total: 400, next: "c", loading: false, failure: null, unsearched: 2 };
  const chips = [{ label: "Project: atlas", href: "?a" }];
  const ctx = { href: (id) => `?run=${id}`, now: 0, open: null, here: null };
  const el = mount("ev-work", { walk, chips, ctx, empty: "list.empty.filter" });
  const { start, end } = windowFor({ top: 0, view: 300, rowHeight: 30, count: many.length, overscan: catalogue.overscan, max: catalogue.max_rows });
  assert.deepEqual([...el.querySelectorAll("[data-row]")].map((r) => r.dataset.id), many.slice(start, end).map((r) => r.id));
  assert.equal(el.querySelector("[data-after]").style.height, `${(many.length - end) * 30}px`);
  assert.equal(el.querySelector("h1").textContent, plain("work.count", { n: "400" }));
  assert.equal(el.querySelector("[data-chip]").getAttribute("href"), "?a");
  assert.ok(text(el.querySelector("[data-foot]")).startsWith(plain("work.more", { shown: "120", total: "400" })));
  assert.ok(text(el.querySelector("[data-foot]")).includes(plain("coverage.unsearched", { n: "2" })));
  const box = el.querySelector("[data-scroll]");
  const link = el.reveal(80);
  assert.equal(box.scrollTop, revealTop(80, 0, 300, 30));
  assert.equal(link.dataset.run, "r80");
  el.data = { walk: { ...walk, total: null, loading: true, unsearched: 0 }, chips: [], ctx, empty: "list.empty.filter" };
  assert.equal(el.querySelector("h1").textContent, plain("work.title"));
  assert.equal(text(el.querySelector("[data-foot]")), plain("work.loading"));
  el.data = { walk: { ...walk, failure: "offline", unsearched: 0 }, chips: [], ctx, empty: "list.empty.filter" };
  assert.ok(el.querySelector("[data-retry-list]"));
  assert.equal(text(el.querySelector("[data-foot]")), plain("app.status.offline"));
  el.data = { walk: { rows: [], total: 0, next: null, loading: false, failure: null, unsearched: 0 }, chips: [], ctx, empty: "list.empty.filter" };
  assert.equal(text(el.querySelector("[data-foot]")), plain("list.empty.filter"));
  assert.equal(el.querySelectorAll("[data-row]").length, 0);
  el.data = null;
  assert.equal(el.innerHTML, "");
});

// ---- the Universe -------------------------------------------------------------------------------------------------------------
const W = 1440, H = 900;
const uplace = (p = {}) => ({ ...place, view: "universe", ...p });
const udata = (p = {}, extra = {}) => ({ summary, place: uplace(p), sheet: null, goals: new Map(), since: null, nowMs: 0, href, list: (id) => `?project=${id}`, needsAll: "?needs", empty: "list.empty.demo", ...extra });
/** The world as the element lays it out: the same pure layout of the same summary. */
function world(s = summary) {
  const L = layout(s, G);
  return { L, bounds: () => L.bounds, island: (id) => L.islands[s.projects.findIndex((p) => p.id === id)], mark: (m, i) => ({ x: L.x[i], y: L.y[i] }) };
}
const view = (keyOpen = false, card = false) => ({ w: W, h: H, inset: viewInset(keyOpen, G, card) });
const near = (a, b) => Math.abs(a - b) < 1e-6;
const arcs = () => drawn.filter((c) => c[0] === "arc");
const hasArc = (sx, sy, r) => arcs().some((c) => near(c[1], sx) && near(c[2], sy) && near(c[3], r) && c[4] === 0 && near(c[5], TURN));
const tone = (s) => colours.get(`--tone-${stateOf("standing", s).tone}`);
function universe(p, extra) { const el = mount("ev-universe", udata(p, extra)); flush(); return el; }

test("ev-universe at store zoom draws each island's shore and each mark where geometry puts them, in its standing's colour", () => {
  const el = universe();
  const L = world(), cam = fitView(L.bounds(), view(), Infinity, G), m = summary.marks;
  for (const p of summary.projects) {
    const c = L.island(p.id), q = toScreen(cam, view(), c.x, c.y);
    assert.ok(hasArc(q.sx, q.sy, c.r * cam.s), `the shore of ${p.label}`);
    assert.ok(hasArc(q.sx, q.sy, c.r * cam.s * G.glow_outer), `the glow of ${p.label}`);
  }
  const r = markRadius("store", cam.s, G);
  for (let i = 0; i < m.id.length; i++) {
    const { x, y } = L.mark(m, i), q = toScreen(cam, view(), x, y);
    const at = drawn.findIndex((c) => c[0] === "arc" && near(c[1], q.sx) && near(c[2], q.sy) && near(c[3], r));
    assert.ok(at > 0, `mark ${m.id[i]}`);
    const fill = drawn.slice(0, at).reverse().find((c) => c[0] === "=fillStyle");
    assert.equal(fill[1], tone(m.standings[m.standing[i]]), `mark ${m.id[i]}'s colour`);
  }
  const b = L.bounds(), a = toScreen(cam, view(), b.x0, b.y0), z = toScreen(cam, view(), b.x1, b.y1);
  assert.equal(el.dataset.fit, `${[a.sx, a.sy, z.sx, z.sy].map((v) => v.toFixed(1)).join(" ")} ${cam.s.toFixed(4)}`);
  assert.equal(el.dataset.markPx, (2 * r).toFixed(1));
  assert.equal(el.dataset.motion, "reduced");
});

test("ev-universe names every island with its counts, calls out the runs that need a human, and counts the rest", () => {
  const el = universe();
  const names = [...el.querySelectorAll("[data-island-label]")];
  assert.equal(names.length, Math.min(summary.projects.length, G.labels));
  for (const a of names) {
    const p = summary.projects.find((x) => x.id === a.dataset.islandLabel);
    assert.equal(a.getAttribute("href"), `?project=${p.id}`);
    assert.ok(text(a).startsWith(`${p.label} ${plain(p.runs === 1 ? "universe.runs.one" : "universe.runs", { n: p.runs })}`), p.label);
    assert.equal(text(a).includes(plain("universe.need_you", { n: p.needs })), p.needs > 0, p.label);
    assert.equal(el.querySelector(`[data-island-label="${p.id}"]`).style.left, `${a.dataset.x}px`);
  }
  const called = [...el.querySelectorAll("[data-callout]")];
  assert.ok(called.length > 0 && called.length <= G.callouts);
  for (const c of called) {
    const n = summary.needs.find((x) => x.id === c.dataset.callout);
    assert.ok(text(c).includes(plain(`ask.${n.ask}`)), n.id);
    assert.equal(c.getAttribute("href"), href({ run: n.id, claim: null }));
  }
  assert.equal(el.querySelectorAll("[data-leaders] line").length, called.length);
  const needs = summary.facets.standing.needs_human;
  assert.equal(text(el.querySelector("[data-needs-pill]")), plain("universe.pill", { n: needs }));
  const more = el.querySelector("[data-more-needs]");
  assert.equal(more ? text(more) : "", needs > called.length ? plain("universe.more", { n: needs - called.length }) : "");
  // the pulse rings: each called-out run's ring, at its mark, in the needs colour, under the marks
  const L = world(), cam = fitView(L.bounds(), view(), Infinity, G), r = markRadius("store", cam.s, G), m = summary.marks;
  for (const c of called) {
    const { x, y } = L.mark(m, m.id.indexOf(c.dataset.callout)), q = toScreen(cam, view(), x, y);
    const ring = drawn.findIndex((d) => d[0] === "arc" && near(d[1], q.sx) && near(d[2], q.sy) && d[3] > r);
    const dot = drawn.findIndex((d) => d[0] === "arc" && near(d[1], q.sx) && near(d[2], q.sy) && near(d[3], r));
    assert.ok(ring >= 0 && ring < dot, `${c.dataset.callout}: a ring under its mark`);
    assert.equal(drawn.slice(0, ring).reverse().find((d) => d[0] === "=strokeStyle")[1], tone("needs_human"));
  }
  assert.equal(text(el.querySelector("[data-crumbs]")), plain("universe.all", { n: summary.coverage.runs }));
});

test("ev-universe's bars, key and mirror show the observer's counts; the key starts folded, and hiding it refits the camera", () => {
  const el = universe();
  // the key starts folded to its button in the bottom bar; the button opens it
  assert.equal(el.querySelector("[data-key]").innerHTML, "");
  el.querySelector("[data-bottom] [data-key-toggle]").click(); flush();
  const a = summary.aggregates;
  const counts = { failed_audit: a.runs.failed_audits, refuted: a.runs.refutations, decided: a.runs.decisions, changed_since_audit: a.by.standing.changed_since_audit ?? 0 };
  for (const chip of el.querySelectorAll("[data-hl]")) assert.equal(text(chip), `${counts[chip.dataset.hl]} ${plain(`flag.${chip.dataset.hl}`)}`);
  assert.deepEqual([...el.querySelectorAll("[data-period]")].map((p) => p.dataset.period), catalogue.periods);
  assert.equal(el.querySelector('[data-period][aria-current="true"]').dataset.period, place.period);
  const shown = summary.marks.standings.filter((s) => (summary.facets.standing[s] ?? 0) > 0);
  const keyRows = [...el.querySelectorAll("[data-key-row]")];
  assert.deepEqual(keyRows.map(text), shown.map((s) => `${plain(`${stateOf("standing", s).copy}.label`)} ${summary.facets.standing[s]}`));
  assert.equal(keyRows[0].querySelector("path").getAttribute("d"), shapePath(stateOf("standing", shown[0]).shape, 5));
  const kb = keyBox(shown.length, view(true), G), key = el.querySelector("[data-key]");
  assert.deepEqual([key.style.left, key.style.top, key.style.width], [`${kb.x}px`, `${kb.y}px`, `${kb.w}px`]);
  assert.deepEqual([...el.querySelectorAll("[data-mirror-island]")].map((x) => x.dataset.mirrorIsland), summary.projects.map((p) => p.id));
  assert.ok(el.querySelectorAll("[data-mirror-run]").length > 0);
  // hide the key: it is gone, its toggle is in the bottom bar, and the camera fits the larger free part
  el.querySelector("[data-legend] [data-key-toggle]").click(); flush();
  assert.equal(el.querySelector("[data-key]").innerHTML, "");
  assert.ok(el.querySelector("[data-bottom] [data-key-toggle]"));
  const cam = fitView(world().bounds(), view(false), Infinity, G);
  assert.ok(el.dataset.fit.endsWith(cam.s.toFixed(4)));
  el.querySelector("[data-bottom] [data-key-toggle]").click(); flush();
  assert.ok(el.querySelector("[data-legend]"));
});

test("ev-universe zoomed on an island draws each of its marks as its standing's shape at geometry's place, and dims the rest", () => {
  const p = summary.projects[2];
  const el = universe({ zoom: p.id });
  const L = world(), c = L.island(p.id), m = summary.marks, k = summary.projects.indexOf(p);
  const cam = fitView(bounds([footprint(c.x, c.y, c.r, G)]), view(false, true), G.island_scale_max, G), r = markRadius("project", cam.s, G);
  const fills = drawn.map((x, i) => [x, i]).filter(([x]) => x[0] === "fill" && x[1]?.d !== undefined);
  assert.ok(fills.length > 0, "the island's marks are drawn as shapes");
  for (let i = 0; i < m.id.length; i++) {
    const { x, y } = L.mark(m, i), q = toScreen(cam, view(), x, y);
    if (q.sx < -r || q.sy < -r || q.sx > W + r || q.sy > H + r) continue;
    const at = drawn.findIndex((d) => d[0] === "setTransform" && near(d[5], q.sx) && near(d[6], q.sy));
    assert.ok(at >= 0, `mark ${m.id[i]} placed`);
    const shape = drawn.slice(at).find((d) => d[0] === "fill");
    assert.equal(shape[1].d, shapePath(stateOf("standing", m.standings[m.standing[i]]).shape, r), `mark ${m.id[i]}'s shape`);
    const alpha = drawn.slice(0, at).reverse().find((d) => d[0] === "=globalAlpha");
    assert.equal(alpha[1], m.project[i] === k ? 1 : G.dim, `mark ${m.id[i]} lit only on its island`);
  }
  assert.deepEqual([...el.querySelectorAll("[data-island-label]")].map((a) => a.dataset.islandLabel), [p.id]);
  assert.ok(text(el.querySelector("[data-crumbs]")).endsWith(p.label));
  for (const c2 of el.querySelectorAll("[data-callout]")) assert.equal(summary.projects[m.project[m.id.indexOf(c2.dataset.callout)]].id, p.id);
  assert.equal(el.querySelectorAll("[data-mirror-run]").length, Math.min(G.mirror_cap, m.project.filter((x) => x === k).length));
});

test("ev-universe on a run never lays a claim's label over a claim's or an attempt's link, for every fixture run", () => {
  const rect = (e) => ({ x: +e.dataset.x, y: +e.dataset.y, w: +e.dataset.w, h: +e.dataset.h });
  const meet = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const met = [];
  for (const [key, s] of Object.entries(sheets)) {
    if (!summary.marks.id.includes(s.row.id) || !(s.row.claims ?? []).length) continue;
    document.body.innerHTML = "";
    const el = universe({ run: s.row.id }, { sheet: s });
    const links = [...el.querySelectorAll("[data-orbit], [data-chain]")];
    for (const l of el.querySelectorAll("[data-orbit-label]")) for (const b of links) if (meet(rect(l), rect(b))) met.push(`${key} ${l.dataset.orbitLabel} over ${b.dataset.orbit ?? `attempt ${b.dataset.chain}`}`);
  }
  assert.deepEqual(met, []);
});

test("ev-universe on a run is its own scene: the run large with its word, its claims a graph from geometry's claimGraph, its attempts a chain", () => {
  const key = Object.keys(sheets).find((x) => (sheets[x].row.claims ?? []).length > 1 && sheets[x].attempts.length > 1 && summary.marks.id.includes(sheets[x].row.id))
    ?? Object.keys(sheets).find((x) => (sheets[x].row.claims ?? []).length > 1 && summary.marks.id.includes(sheets[x].row.id));
  const s = sheets[key], row = s.row;
  const el = universe({ run: row.id }, { sheet: s });
  const W0 = world(), m = summary.marks, i = m.id.indexOf(row.id), own = W0.island(summary.projects[m.project[i]].id), at = W0.mark(m, i);
  const cam = { ...fitView(bounds([footprint(own.x, own.y, own.r, G)]), view(), G.island_scale_max, G), x: at.x, y: at.y };
  const c = toScreen(cam, view(), at.x, at.y), claims = row.claims.slice(0, G.claims_cap);
  const nodes = [...el.querySelectorAll("[data-orbit]")];
  assert.deepEqual(nodes.map((n) => n.dataset.orbit), claims.map((x) => x.id), "every claim once");
  const said = plain(`${stateOf("standing", row.standing).copy}.label`), ww = said.length * G.claim_char_px + G.claim_pad_px;
  const word = { x: -ww / 2, y: G.run_px + G.claim_gap_px, w: ww, h: G.word_px }, v = view();
  const label = (cl) => `${cl.id} · ${clip(cl.text, G.claim_chars)}`;
  const widths = new Map(claims.map((cl) => [cl.id, label(cl).length * G.claim_char_px + G.claim_pad_px]));
  const size = { ring: G.orbit_ring, node: G.claim_px, hit: G.claim_hit_px, gap: G.claim_gap_px, run: G.run_px + G.focus_px, label_h: G.claim_label_px, cap: G.loop_cap };
  const inside = { x: v.inset.left - c.sx, y: v.inset.top - c.sy, w: W - v.inset.left - v.inset.right, h: H - v.inset.top - v.inset.bottom };
  const g = claimGraph(row.root, claims, row.edges, widths, size, [word], inside);
  const labels = [...el.querySelectorAll("[data-orbit-label]")];
  assert.deepEqual(labels.map((l) => l.dataset.orbitLabel), claims.filter((cl) => g.labels.get(cl.id)).map((cl) => cl.id), "the labels geometry could place, no other");
  for (const l of labels) {
    const b = g.labels.get(l.dataset.orbitLabel), cl = claims.find((x) => x.id === l.dataset.orbitLabel);
    assert.deepEqual([+l.dataset.x, +l.dataset.y, +l.dataset.w, +l.dataset.h], [Math.round(c.sx + b.x), Math.round(c.sy + b.y), Math.round(b.w), Math.round(b.h)]);
    assert.equal(text(l), label(cl)); assert.equal(l.title, cl.text);
  }
  const wd = el.querySelector("[data-run-word]");
  assert.deepEqual([+wd.dataset.x, +wd.dataset.y], [Math.round(c.sx + word.x), Math.round(c.sy + word.y)], "the word under the run");
  if (g.root) {
    const root = g.at.get(g.root), link = [...el.querySelectorAll("[data-graph] line:not([data-edge])")][0];
    assert.deepEqual([+link.getAttribute("x1"), +link.getAttribute("y1"), +link.getAttribute("x2"), +link.getAttribute("y2")].map(Math.round), [c.sx, c.sy, c.sx + root.x, c.sy + root.y].map(Math.round), "the run linked to its root");
  }
  for (const n of nodes) {
    const p = g.at.get(n.dataset.orbit);
    assert.deepEqual([+n.dataset.cx, +n.dataset.cy], [Math.round(c.sx + p.x), Math.round(c.sy + p.y)], `${n.dataset.orbit} at its orbit place`);
    assert.equal(n.getAttribute("href"), href({ claim: n.dataset.orbit }));
    assert.ok(n.querySelector("path").getAttribute("d") === shapePath(stateOf("claim", claims.find((x) => x.id === n.dataset.orbit).state).shape, G.claim_px - 1), "its state's shape");
  }
  const published = new Set((row.edges ?? []).filter((e) => g.at.has(e.from) && g.at.has(e.to)).map((e) => `${e.from} ${e.to}`));
  const lines = [...el.querySelectorAll("[data-graph] line[data-edge]")].map((l) => l.dataset.edge);
  assert.deepEqual(new Set(lines), published, "the SupportedBy edges, each once"); assert.equal(lines.length, published.size);
  assert.equal(text(el.querySelector("[data-run-word]")), plain(`${stateOf("standing", row.standing).copy}.label`));
  assert.equal(el.querySelectorAll("[data-island-label]").length + el.querySelectorAll("[data-callout]").length, 0);
  assert.ok(text(el.querySelector("[data-crumbs]")).includes(row.goal.slice(0, 20)));
  assert.ok(drawn.some((d) => d[0] === "fill" && d[1]?.d === shapePath(stateOf("standing", row.standing).shape, G.run_px)), "the run's own mark drawn run_px large");
  const chain = [...el.querySelectorAll("[data-chain]")];
  assert.deepEqual(chain.map((x) => x.dataset.chain), s.attempts.length > 1 ? s.attempts.map((a) => a.id) : [], "each attempt a glyph, in order");
  if (chain.length) {
    assert.equal(el.querySelector('[data-chain][aria-current="true"]').dataset.chain, row.id);
    chain.forEach((a, n) => { assert.equal(a.getAttribute("href"), href({ run: s.attempts[n].id, claim: null }));
      if (n) assert.equal(+a.dataset.x - +chain[n - 1].dataset.x, G.chain_step_px, "one step apart"); });
    assert.equal(el.querySelectorAll("[data-graph] line.stroke-muted-foreground").length, chain.length - 1, "linked in order");
  }
  // the other marks are a faint texture
  const dims = drawn.filter((d) => d[0] === "=globalAlpha").map((d) => d[1]);
  assert.ok(dims.includes(G.graph_dim));
});

test("ev-universe on an island shows its card: name, repository, runs and its runs by standing, the observer's counts", () => {
  const p = summary.projects.find((x) => Object.keys(x.standings).length > 1);
  const el = universe({ zoom: p.id }), card = el.querySelector(`[data-card="${p.id}"]`);
  assert.ok(card);
  assert.ok(text(card).startsWith(p.label) && text(card).includes(p.repo) && text(card).includes(plain("universe.runs", { n: p.runs })));
  const rows = [...card.querySelectorAll("[data-card-row]")];
  assert.deepEqual(rows.map((r) => r.dataset.cardRow), summary.marks.standings.filter((k) => p.standings[k]), "in the contract's standing order");
  for (const r of rows) assert.ok(text(r).endsWith(String(p.standings[r.dataset.cardRow])));
  const b = cardBox(rows.length, view(false, true), G);
  assert.deepEqual([+card.dataset.x, +card.dataset.y, +card.dataset.w, +card.dataset.h], [b.x, b.y, b.w, b.h].map(Math.round));
  assert.ok(!universe().querySelector("[data-card]"), "the store has no card");
});

test("ev-universe's keyboard moves between islands, says where it is, and opens or leaves a zoom", () => {
  const el = universe();
  const canvas = el.querySelector("canvas");
  const went = [];
  el.addEventListener("ev-navigate", (e) => went.push(e.detail));
  canvas.focus();
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  const said = el.querySelector("[data-said]").textContent;
  const p = summary.projects.find((x) => said === plain("universe.island", { name: x.label, n: x.runs }));
  assert.ok(p, said);
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  assert.deepEqual(went, [href({ zoom: p.id, run: null, claim: null })]);
  flush();
  assert.ok(drawn.some((d) => d[0] === "=strokeStyle" && d[1] === colours.get("--ring")), "the focus ring is drawn");
  el.data = udata({ zoom: p.id }); flush();
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "-", bubbles: true }));
  assert.equal(went.at(-1), href({ zoom: null }));
  const click = new MouseEvent("click", { bubbles: true, clientX: 0, clientY: 0 });
  canvas.dispatchEvent(click);  // no island at the corner
  assert.equal(went.length, 2);
});

test("ev-universe says an empty store is empty, and counts the flights it begins only with motion", () => {
  const empty = { ...summary, projects: [], needs: [], marks: { ...summary.marks, id: [], project: [], standing: [], flags: [], updated: [], attempt: [], place: [], started: [] } };
  const el = universe({}, { summary: empty });
  assert.equal(text(el.querySelector("[data-empty]")), plain("list.empty.demo"));
  el.data = udata({ zoom: summary.projects[0].id }, { summary: empty }); flush();
  assert.equal(el.dataset.flights, undefined, "reduced motion: no flight");
  document.body.innerHTML = "";
  install({ catalogue, reduced: false });
  try {
    const moving = universe();
    assert.equal(moving.dataset.motion, "full");
    moving.data = udata({ zoom: summary.projects[0].id });
    assert.equal(moving.dataset.flights, "1");
  } finally { install({ catalogue }); }
});

test("ev-universe lights only the highlighted flag's marks and the period's, and dims the islands it is not zoomed on", () => {
  const m = summary.marks, L = world();
  const alphaAt = (q, r) => { const at = drawn.findIndex((d) => d[0] === "arc" && near(d[1], q.sx) && near(d[2], q.sy) && near(d[3], r)); return drawn.slice(0, at).reverse().find((d) => d[0] === "=globalAlpha")[1]; };
  const flag = m.flag_bits.find((f) => m.flags.some((x) => x & (1 << m.flag_bits.indexOf(f))) && m.flags.some((x) => !(x & (1 << m.flag_bits.indexOf(f)))));
  const since = [...m.updated].filter((u) => u !== null).sort((a, b) => a - b)[Math.floor(m.updated.length / 2)];
  for (const [p, extra, lit] of [[{ hl: flag }, {}, (i) => (m.flags[i] & (1 << m.flag_bits.indexOf(flag))) !== 0], [{}, { since }, (i) => m.updated[i] !== null && m.updated[i] >= since]]) {
    document.body.innerHTML = ""; drawn.length = 0;
    universe(p, extra);
    const cam = fitView(L.bounds(), view(), Infinity, G), r = markRadius("store", cam.s, G);
    const lits = m.id.map((_, i) => alphaAt(toScreen(cam, view(), L.mark(m, i).x, L.mark(m, i).y), r));
    assert.deepEqual(lits, m.id.map((_, i) => (lit(i) ? 1 : G.dim)), JSON.stringify(p));
    assert.ok(lits.includes(1) && lits.includes(G.dim), "both lit and dimmed marks");
  }
  document.body.innerHTML = ""; drawn.length = 0;
  const z = summary.projects[1];
  universe({ zoom: z.id });
  const c = L.island(z.id), cam = fitView(bounds([footprint(c.x, c.y, c.r, G)]), view(false, true), G.island_scale_max, G);
  for (const p of summary.projects) {
    const o = L.island(p.id), q = toScreen(cam, view(), o.x, o.y);
    assert.equal(alphaAt(q, o.r * cam.s * G.glow_outer), p.id === z.id ? 1 : G.dim, p.label);
  }
});

test("ev-universe's pointer: a mark's tip says its goal and standing, a click opens it, the wheel zooms in and out once per flight", () => {
  const p = summary.projects[2], m = summary.marks, L = world(), k = summary.projects.indexOf(p);
  const i = m.project.indexOf(k), id = m.id[i];
  const el = universe({ zoom: p.id }, { goals: new Map([[id, "The goal of this run"]]) });
  const canvas = el.querySelector("canvas"), went = [];
  el.addEventListener("ev-navigate", (e) => went.push(e.detail));
  const c = L.island(p.id), cam = fitView(bounds([footprint(c.x, c.y, c.r, G)]), view(false, true), G.island_scale_max, G);
  const q = toScreen(cam, view(), L.mark(m, i).x, L.mark(m, i).y);
  canvas.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: q.sx, clientY: q.sy })); flush();
  const tip = el.querySelector("[data-tip]");
  assert.ok(text(tip).startsWith("The goal of this run"), text(tip));
  assert.ok(text(tip).includes(plain(`${stateOf("standing", m.standings[m.standing[i]]).copy}.label`)));
  canvas.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: 0, clientY: 0 })); flush();
  assert.ok(!el.querySelector("[data-tip]"), "off every mark, no tip");
  canvas.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: q.sx, clientY: q.sy }));
  assert.deepEqual(went, [href({ run: id, claim: null })]);
  canvas.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: q.sx, clientY: q.sy, button: 1 }));
  assert.equal(went.length, 1, "only the main button opens");
  const clock = vi.spyOn(performance, "now").mockReturnValue(10_000);
  try {
    const wheel = (deltaY, x, y) => { const e = new MouseEvent("wheel", { bubbles: true, cancelable: true, clientX: x, clientY: y }); Object.defineProperty(e, "deltaY", { value: deltaY }); canvas.dispatchEvent(e); return e; };
    assert.ok(wheel(1, 0, 0).defaultPrevented);
    assert.equal(went.at(-1), href({ zoom: null }), "the wheel back: out of the island");
    clock.mockReturnValue(10_100); wheel(1, 0, 0);
    assert.equal(went.length, 2, "within one flight's duration a second turn does nothing");
    clock.mockReturnValue(10_300); wheel(-1, q.sx, q.sy);
    assert.equal(went.at(-1), href({ run: id, claim: null }), "the wheel forward: into the mark under it");
  } finally { clock.mockRestore(); }
  // the store: a click on an island zooms on it
  document.body.innerHTML = "";
  const store = universe(), shore = L.island(summary.projects[0].id), cs = fitView(L.bounds(), view(), Infinity, G), at = toScreen(cs, view(), shore.x, shore.y);
  const opened = [];
  store.addEventListener("ev-navigate", (e) => opened.push(e.detail));
  store.querySelector("canvas").dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: at.sx, clientY: at.sy }));
  assert.deepEqual(opened, [href({ zoom: summary.projects[0].id, run: null, claim: null })]);
});

test("ev-universe's keyboard on an island says each run, and on a run moves between its claims", () => {
  const p = summary.projects[2], m = summary.marks, k = summary.projects.indexOf(p);
  const goals = new Map(m.id.filter((_, i) => m.project[i] === k).map((x) => [x, `goal of ${x}`]));
  const el = universe({ zoom: p.id }, { goals });
  const canvas = el.querySelector("canvas"), went = [];
  el.addEventListener("ev-navigate", (e) => went.push(e.detail));
  canvas.focus();
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  const said = el.querySelector("[data-said]").textContent, run = [...goals.keys()].find((x) => said.startsWith(`goal of ${x}`));
  assert.ok(run, said);
  assert.ok(said.endsWith(plain(`${stateOf("standing", m.standings[m.standing[m.id.indexOf(run)]]).copy}.label`)));
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "+", bubbles: true }));
  assert.deepEqual(went, [href({ run, claim: null })]);
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, metaKey: true }));
  assert.equal(el.querySelector("[data-said]").textContent, said, "a modified key is not the canvas's");
  const key = Object.keys(sheets).find((x) => (sheets[x].row.claims ?? []).length > 1 && m.id.includes(sheets[x].row.id));
  const s = sheets[key];
  el.data = udata({ run: s.row.id }, { sheet: s }); flush();
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  const claim = s.row.claims.find((c) => c.text === el.querySelector("[data-said]").textContent);
  assert.ok(claim, el.querySelector("[data-said]").textContent);
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  assert.equal(went.at(-1), href({ claim: claim.id }));
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "-", bubbles: true }));
  assert.equal(went.at(-1), href({ run: null }));
  el.data = udata({ run: s.row.id, claim: claim.id }, { sheet: s }); flush();
  canvas.dispatchEvent(new KeyboardEvent("keydown", { key: "-", bubbles: true }));
  assert.equal(went.at(-1), href({ claim: null }));
  assert.equal(el.querySelector(`[data-orbit="${claim.id}"]`).getAttribute("aria-current"), "true");
});

test("ev-universe draws every placed mark on screen and no other: drawn() is each mark's screen place, culled at the view's edge", () => {
  const W0 = world(), m = summary.marks;
  const expect = (cam, v, r) => m.id.flatMap((id, i) => { const q = toScreen(cam, v, W0.mark(m, i).x, W0.mark(m, i).y);
    return q.sx >= -r && q.sy >= -r && q.sx <= W + r && q.sy <= H + r ? [`${id} ${q.sx.toFixed(1)} ${q.sy.toFixed(1)}`] : []; });
  const store = universe(), cs = fitView(W0.bounds(), view(), Infinity, G);
  assert.deepEqual(store.drawn().marks, expect(cs, view(), markRadius("store", cs.s, G)));
  assert.equal(store.drawn().fit, store.dataset.fit);
  assert.equal(store.drawn().marks.length, m.id.length, "the store shows them all");
  document.body.innerHTML = "";
  const p = summary.projects.find((x) => x.runs > 2), c = W0.island(p.id), el = universe({ zoom: p.id });
  const cz = fitView(bounds([footprint(c.x, c.y, c.r, G)]), view(false, true), G.island_scale_max, G), got = el.drawn().marks;
  assert.deepEqual(got, expect(cz, view(false, true), markRadius("project", cz.s, G)));
  assert.ok(got.length < m.id.length, "zoomed in, the marks off screen are not drawn");
  // a mark the observer has not placed is drawn nowhere
  document.body.innerHTML = "";
  const s = structuredClone(summary); s.marks.place[0] = null;
  assert.ok(!universe({}, { summary: s }).drawn().marks.some((x) => x.startsWith(`${m.id[0]} `)));
});

test("ev-universe's camera flies only when the place changes; a new summary, the same place or a first paint snaps", () => {
  document.body.innerHTML = "";
  install({ catalogue, reduced: false });
  try {
    const el = universe(), first = el.dataset.fit;
    assert.equal(el.dataset.flights, undefined, "a first paint (a load) does not fly");
    el.data = udata({}, { summary: structuredClone(summary) }); flush();
    assert.equal(el.dataset.flights, undefined, "a new summary at the same place: no flight");
    assert.equal(el.dataset.fit, first, "and the same picture");
    const p = summary.projects[1], c = world().island(p.id), to = fitView(bounds([footprint(c.x, c.y, c.r, G)]), view(false, true), G.island_scale_max, G);
    el.data = udata({ zoom: p.id });
    assert.equal(el.dataset.flights, "1", "a navigation flies");
    flush(performance.now()); // the first frame of the flight: not there yet
    const mid = el.dataset.fit;
    assert.notEqual(mid, first);
    el.data = udata({ zoom: p.id }, { summary: structuredClone(summary) });
    assert.equal(el.dataset.flights, "1", "a poll in the air retargets the flight, it does not start another");
    flush(performance.now() + 60_000); flush(performance.now() + 60_000);
    assert.ok(el.dataset.fit.endsWith(to.s.toFixed(4)), "it lands where the place wants it");
    el.data = udata({ zoom: p.id }); flush(performance.now() + 120_000);
    assert.equal(el.dataset.flights, "1", "landed, the same place: still");
    el.data = udata({}); assert.equal(el.dataset.flights, "2", "back out flies");
  } finally { install({ catalogue }); }
});

test("ev-universe at a run's zoom fades every island and every other mark to the background; at the store none is dimmed", () => {
  const key = Object.keys(sheets).find((x) => (sheets[x].row.claims ?? []).length > 1 && summary.marks.id.includes(sheets[x].row.id));
  universe({ run: sheets[key].row.id }, { sheet: sheets[key] });
  const alphas = drawn.filter((d) => d[0] === "=globalAlpha").map((d) => d[1]);
  assert.ok(alphas.filter((a) => a === G.graph_dim).length >= summary.projects.length, "each island at graph_dim");
  document.body.innerHTML = ""; drawn.length = 0;
  universe();
  assert.ok(!drawn.some((d) => d[0] === "=globalAlpha" && d[1] === G.graph_dim));
});

test("ev-universe's crumbs: the store whole, then the island, then the run's goal, each a link up, the whole path in the title", () => {
  const key = Object.keys(sheets).find((x) => (sheets[x].row.claims ?? []).length > 1 && summary.marks.id.includes(sheets[x].row.id));
  const s = sheets[key], i = summary.marks.id.indexOf(s.row.id), p = summary.projects[summary.marks.project[i]], all = plain("universe.all", { n: summary.coverage.runs });
  const crumbs = (el) => [...el.querySelectorAll("[data-crumb]")].map((c) => [c.dataset.crumb, text(c), c.getAttribute("href")]);
  assert.deepEqual(crumbs(universe()), [["store", all, href({ zoom: null, run: null, claim: null })]]);
  document.body.innerHTML = "";
  const z = universe({ zoom: p.id });
  assert.deepEqual(crumbs(z), [["store", all, href({ zoom: null, run: null, claim: null })], ["project", p.label, href({ zoom: p.id, run: null, claim: null })]]);
  assert.equal(z.querySelector("[data-crumbs]").title, `${all} › ${p.label}`);
  document.body.innerHTML = "";
  const r = universe({ run: s.row.id }, { sheet: s });
  assert.deepEqual(crumbs(r).map((c) => c[0]), ["store", "project", "run"], "the run's island from its mark, not the place's zoom");
  assert.equal(crumbs(r)[1][1], p.label);
  assert.equal(r.querySelector("[data-crumbs]").title, `${all} › ${p.label} › ${s.row.goal}`);
  assert.equal(r.querySelectorAll("[data-crumbs] > span[aria-hidden]").length, 2, "a separator between crumbs");
  document.body.innerHTML = "";
  assert.ok(text(universe({ run: s.row.id }, { sheet: null }).querySelector('[data-crumb="run"]')).startsWith(s.row.id.slice(0, 6)), "no sheet yet: the run's id");
});

test("ev-universe's bottom bar: each period a link to it (the current one marked), each flag chip toggles its highlight", () => {
  const el = universe({ hl: "refuted" });
  for (const a of el.querySelectorAll("[data-period]")) {
    const k = a.dataset.period;
    assert.equal(a.getAttribute("href"), href({ period: k, since: null })); assert.equal(text(a), plain(`period.${k}`));
    assert.equal(a.getAttribute("aria-current"), k === place.period ? "true" : null);
  }
  const chips = [...el.querySelectorAll("[data-hl]")];
  assert.deepEqual(chips.map((c) => c.dataset.hl), enums.flag, "one chip per flag the contract declares");
  for (const c of chips) {
    const on = c.dataset.hl === "refuted";
    assert.equal(c.getAttribute("href"), href({ hl: on ? null : c.dataset.hl })); assert.equal(c.getAttribute("aria-pressed"), String(on));
  }
  document.body.innerHTML = "";
  const since = universe({ since: 5 });
  assert.ok(!since.querySelector('[data-period][aria-current="true"]'), "a calendar day picked: no period is current");
});

test("ev-universe's mirror: the islands as links in, the runs as links to each, an island past the cap ends with a link to its list", () => {
  const el = universe();
  for (const a of el.querySelectorAll("[data-mirror-island]")) {
    const p = summary.projects.find((x) => x.id === a.dataset.mirrorIsland);
    assert.equal(a.getAttribute("href"), href({ zoom: p.id, run: null, claim: null })); assert.equal(text(a), plain("universe.island", { name: p.label, n: p.runs }));
  }
  const runs = [...el.querySelectorAll("[data-mirror-run]")];
  assert.ok(runs.length > 0);
  for (const a of runs) {
    const n = summary.needs.find((x) => x.id === a.dataset.mirrorRun);
    assert.equal(a.getAttribute("href"), href({ run: n.id, claim: null }));
    assert.ok(text(a).includes(plain("standing.needs_human.label")) && text(a).includes(plain(`ask.${n.ask}`)), text(a));
  }
  assert.ok(!el.querySelector("[data-mirror-rest]"));
  // an island of more marks than the cap
  document.body.innerHTML = "";
  const big = structuredClone(summary), m = big.marks, n = G.mirror_cap + 3;
  for (const k of Object.keys(m)) if (Array.isArray(m[k]) && k !== "standings" && k !== "flag_bits") m[k] = [];
  for (let i = 0; i < n; i++) { m.id.push(`x${i}`); m.project.push(0); m.place.push(i); m.attempt.push(i ? 1 : 2); m.standing.push(m.standings.indexOf("proven")); m.updated.push(null); m.started.push(null); m.flags.push(0); }
  big.projects = big.projects.map((p, k) => ({ ...p, runs: k ? 0 : n }));
  const z = universe({ zoom: big.projects[0].id }, { summary: big, goals: new Map([["x0", "the first goal"]]) });
  assert.equal(z.querySelectorAll("[data-mirror-run]").length, G.mirror_cap);
  const rest = z.querySelector("[data-mirror-rest]");
  assert.equal(rest.getAttribute("href"), `?project=${big.projects[0].id}`); assert.equal(text(rest), plain("universe.all_in", { n }));
  assert.equal(text(z.querySelector('[data-mirror-run="x0"]')), `the first goal${plain("list.join")}${plain("standing.proven.label")}${plain("list.join")}${plain("row.attempt.short", { n: 2 })}`);
  assert.equal(text(z.querySelector('[data-mirror-run="x1"]')), plain("standing.proven.label"), "attempt 1 says no attempt");
});

test("ev-universe puts each overlay element at the box its data attributes name, and sizes the key's rows", () => {
  const el = universe({ zoom: summary.projects[0].id });
  const placed = [...el.querySelectorAll("[data-overlay] [data-x]")];
  assert.ok(placed.length > 2);
  for (const e of placed) {
    assert.deepEqual([e.style.left, e.style.top], [`${e.dataset.x}px`, `${e.dataset.y}px`]);
    if (e.dataset.w) assert.deepEqual([e.style.width, e.style.height], [`${e.dataset.w}px`, `${e.dataset.h}px`]);
  }
  el.querySelector("[data-bottom] [data-key-toggle]").click(); flush();
  const key = el.querySelector("[data-key]"), kb = keyBox(el.querySelectorAll("[data-key-row]").length, view(true, true), G);
  assert.equal(key.style.height, `${kb.h}px`);
  for (const li of el.querySelectorAll("[data-key-row]")) assert.equal(li.style.height, `${G.key_row_px}px`);
  const p = el.querySelector("[data-key-row] svg");
  assert.equal(p.getAttribute("viewBox"), "-10 -10 20 20");
});

test("ev-universe's tip goes when the pointer leaves the canvas, and the keyboard on a run reaches its attempts", () => {
  const p = summary.projects[2], m = summary.marks, k = summary.projects.indexOf(p), i = m.project.indexOf(k), W0 = world();
  const el = universe({ zoom: p.id }), canvas = el.querySelector("canvas");
  const c = W0.island(p.id), cam = fitView(bounds([footprint(c.x, c.y, c.r, G)]), view(false, true), G.island_scale_max, G), q = toScreen(cam, view(false, true), W0.mark(m, i).x, W0.mark(m, i).y);
  canvas.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: q.sx, clientY: q.sy })); flush();
  assert.ok(el.querySelector("[data-tip]"));
  el.querySelector("[data-overlay]").dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: q.sx, clientY: q.sy })); flush();
  assert.ok(!el.querySelector("[data-tip]"), "off the canvas: no tip");
  document.body.innerHTML = "";
  const key = Object.keys(sheets).find((x) => sheets[x].attempts.length > 1 && (sheets[x].row.claims ?? []).length > 0 && m.id.includes(sheets[x].row.id));
  const s = sheets[key], r = universe({ run: s.row.id }, { sheet: s }), went = [];
  r.addEventListener("ev-navigate", (e) => went.push(e.detail));
  const cv = r.querySelector("canvas"), other = s.attempts.find((a) => a.id !== s.row.id);
  const word = plain(`${stateOf("standing", m.standings[m.standing[m.id.indexOf(other.id)]]).copy}.label`);
  cv.focus();
  let found = false;
  for (let n = 0; n < 12 && !found; n++) for (const dir of [n < 6 ? "ArrowDown" : "ArrowRight"]) {
    cv.dispatchEvent(new KeyboardEvent("keydown", { key: dir, bubbles: true }));
    if (r.querySelector("[data-said]").textContent.endsWith(word) && !s.row.claims.some((c) => c.text === r.querySelector("[data-said]").textContent)) { found = true; break; }
  }
  assert.ok(found, "the arrows go down from the claims to the attempts, and along them");
  cv.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  assert.equal(went.at(-1), href({ run: other.id, claim: null }), "Enter opens that attempt");
});

test("ev-universe on an island counts the island's needs it could not call out in +N more, and its callouts keep off its disc", () => {
  const p = summary.projects.find((x) => x.needs > 0), el = universe({ zoom: p.id }), called = el.querySelectorAll("[data-callout]").length;
  const more = el.querySelector("[data-more-needs]");
  assert.equal(more ? text(more) : "", p.needs > called ? plain("universe.more", { n: p.needs - called }) : "");
  const c = world().island(p.id), v = view(false, true), cam = fitView(bounds([footprint(c.x, c.y, c.r, G)]), v, G.island_scale_max, G), q = toScreen(cam, v, c.x, c.y);
  for (const b of el.querySelectorAll("[data-callout]")) {
    const x = +b.dataset.x, y = +b.dataset.y, w = +b.dataset.w, h = +b.dataset.h, nx = Math.min(Math.max(q.sx, x), x + w), ny = Math.min(Math.max(q.sy, y), y + h);
    assert.ok(Math.hypot(nx - q.sx, ny - q.sy) >= c.r * cam.s - 1, `${b.dataset.callout} off the disc`);
  }
  // more needs than marks drawn for them: the count is the observer's less those called out
  const s = structuredClone(summary), k = s.projects.indexOf(s.projects.find((x) => x.id === p.id));
  s.projects[k] = { ...s.projects[k], needs: p.needs + 4 };
  document.body.innerHTML = "";
  const more4 = universe({ zoom: p.id }, { summary: s }).querySelector("[data-more-needs]");
  assert.equal(text(more4), plain("universe.more", { n: p.needs + 4 - called }), "the observer's count less those drawn");
});
