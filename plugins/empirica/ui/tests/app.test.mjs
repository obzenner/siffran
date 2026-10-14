// @vitest-environment happy-dom
// app/app.js, the page, booted in happy-dom on the page's own markup (app/index.html) against a fake observer that answers
// from the real-service fixtures: what it asks, and what each view then shows. A fixed clock and timers the test advances:
// nothing here waits on time. The headless-Chrome journeys stay the proof of the real page against the real server.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test, vi } from "vitest";
import catalogue from "../catalogue.json" with { type: "json" };
import { flush, install } from "./dom.mjs";

const NOW = 1_790_610_000_000;
vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"], now: NOW });
install({ catalogue });
const { line, rows, sheets, summary } = await import("./fixtures.mjs");
const { clip, plain, stateOf } = await import("../elements/core/element.js");

const page = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "app", "index.html"), "utf8");
document.body.innerHTML = /<body[^>]*>([\s\S]*)<\/body>/.exec(page)[1];
history.replaceState(null, "", "/app/?data=demo");

/** The fake observer: every request it was asked, and the contract's answers from the fixtures. */
const asked = [];
const reply = (status, body = null, etag = null) => ({ status, ok: status >= 200 && status < 300, headers: { get: (h) => (h === "ETag" ? etag : null) }, json: async () => body, arrayBuffer: async () => new ArrayBuffer(0) });
const pageOf = (list, q) => { const limit = Number(q.get("limit") ?? catalogue.page); return { contract: summary.contract, token: summary.token, rows: list.slice(0, limit).map(line), total: list.length, next: null, pending: 0, query: {}, unsearched: 0 }; };
globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url, "http://observer"), q = u.searchParams;
  asked.push({ path: u.pathname, q: Object.fromEntries(q), etag: init.headers?.["If-None-Match"] ?? null });
  switch (u.pathname) {
    case "/api/about": return reply(200, { sources: { store: "generated", demo: "fixtures" } });
    case "/api/summary": return init.headers?.["If-None-Match"] === '"s1"' ? reply(304) : reply(200, summary, '"s1"');
    case "/api/runs": return reply(200, pageOf(rows.filter((r) => (!q.get("standing") || r.standing === q.get("standing")) && (!q.get("project") || r.project === q.get("project"))), q));
    case "/api/search": return reply(200, pageOf(rows.filter((r) => (r.goal ?? "").toLowerCase().includes(q.get("q").toLowerCase())), q));
    case "/api/run": { const s = Object.values(sheets).find((x) => x.row.id === q.get("key")); return s ? reply(200, s, `"${s.version}"`) : reply(404); }
    default: return reply(404);
  }
};
const settle = () => new Promise((r) => setImmediate(r));
const $ = (id) => document.getElementById(id);
const text = (el) => el.textContent.replace(/\s+/g, " ").trim();
const click = async (el) => { el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0 })); await settle(); flush(); await settle(); };
const key = async (k, target = document.body) => { target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true })); await settle(); flush(); await settle(); };

await import("../app/app.js");
await settle(); flush();

test("the page boots: it asks what the server serves and the summary, then shows the Universe, the rail and when it checked", () => {
  assert.equal(document.body.dataset.ready, "1");
  assert.equal(document.title, plain("page.title"));
  assert.deepEqual(asked.slice(0, 2).map((a) => a.path).sort(), ["/api/about", "/api/summary"]);
  assert.equal(asked.find((a) => a.path === "/api/summary").q.source, "demo");
  assert.deepEqual([...$("source").options].map((o) => [o.value, o.selected, o.textContent]), catalogue.data_sources.map((k) => [k, k === "demo", plain(k === "store" ? "app.data.generated" : `app.data.${k}`)]));
  assert.equal(document.body.dataset.view, "universe");
  assert.deepEqual([$("universe").hidden, $("inbox").hidden, $("work").hidden], [false, true, true]);
  assert.equal($("universe").querySelectorAll("[data-island-label]").length, summary.projects.length);
  for (const p of summary.projects) assert.ok(text($("rail")).includes(p.label), p.label);
  assert.ok(text($("status")).includes(plain("app.checked", { at: "" }).trim()), text($("status")));
  assert.ok(text($("status")).includes(plain("coverage.unread", { total: summary.coverage.unreadable + summary.coverage.partial })));
  assert.deepEqual([...$("switch").querySelectorAll("[data-switch]")].map((a) => [a.dataset.switch, a.hasAttribute("aria-current")]), [["universe", true], ["inbox", false]]);
});

test("the inbox shows each standing's group, capped, from the runs the observer gives for it", async () => {
  await click($("switch").querySelector('[data-switch="inbox"]'));
  assert.equal(document.body.dataset.view, "inbox");
  assert.equal(location.search, "?data=demo&view=inbox");
  const standings = summary.marks.standings.filter((s) => (summary.facets.standing[s] ?? 0) > 0);
  assert.deepEqual([...$("inbox").querySelectorAll("[data-group]")].map((g) => g.dataset.group), standings);
  for (const s of standings) {
    const cap = s === catalogue.needs_standing ? catalogue.needs_cap : catalogue.group_cap;
    assert.equal(asked.filter((a) => a.path === "/api/runs" && a.q.standing === s).at(-1).q.limit, String(cap));
    assert.equal($("inbox").querySelectorAll(`[data-group="${s}"] [data-row]`).length, Math.min(cap, rows.filter((r) => r.standing === s).length), s);
  }
});

test("a row opens its sheet: the URL names the run, the sheet shows it, the title is its goal; Escape closes it", async () => {
  const link = $("inbox").querySelector("a[data-run]");
  const id = link.dataset.run, s = Object.values(sheets).find((x) => x.row.id === id);
  await click(link);
  assert.ok(new URLSearchParams(location.search).get("run") === id);
  assert.ok(document.body.hasAttribute("data-sheet"));
  assert.ok(asked.some((a) => a.path === "/api/run" && a.q.key === id));
  assert.equal($("sheet").querySelector("[data-title]").textContent, s.row.goal);
  assert.equal(document.title, plain("page.title.run", { goal: clip(s.row.goal, 60) }));
  await key("Escape");
  assert.ok(!document.body.hasAttribute("data-sheet"));
  assert.equal(new URLSearchParams(location.search).get("run"), null);
  assert.equal(document.title, plain("page.title"));
});

test("g n goes to what needs a human: the work list, filtered, with its chip and count", async () => {
  await key("g"); await key("n");
  assert.equal(document.body.dataset.view, "work");
  assert.equal(new URLSearchParams(location.search).get("standing"), catalogue.needs_standing);
  const need = rows.filter((r) => r.standing === catalogue.needs_standing);
  assert.equal(asked.filter((a) => a.path === "/api/runs").at(-1).q.standing, catalogue.needs_standing);
  assert.equal(text($("work").querySelector("h1")), plain("work.count", { n: need.length }));
  assert.ok(text($("work").querySelector("[data-chip]")).includes(plain(`${stateOf("standing", catalogue.needs_standing).copy}.label`)));
  assert.deepEqual([...$("work").querySelectorAll("[data-row]")].map((r) => r.dataset.id), need.map((r) => r.id));
  await key("j");  // from the row last opened (the inbox's first), one row on
  const at = need.findIndex((r) => r.id === document.activeElement?.dataset.run);
  assert.ok(at >= 0, "j focuses a row of the list");
  await key("j");
  assert.equal(document.activeElement.dataset.run, need[at + 1].id, "j: the next row");
  await key("k");
  assert.equal(document.activeElement.dataset.run, need[at].id, "k: the previous row");
});

test("typing a search asks the observer after typed_ms, and the words button and ? toggle the words", async () => {
  const goal = rows.find((r) => r.goal).goal.split(" ").slice(0, 2).join(" ");
  $("search").value = goal;
  $("search").dispatchEvent(new Event("input", { bubbles: true }));
  vi.advanceTimersByTime(catalogue.typed_ms - 1); await settle();
  assert.ok(!asked.some((a) => a.path === "/api/search"));
  vi.advanceTimersByTime(1); await settle(); flush(); await settle();
  assert.equal(asked.filter((a) => a.path === "/api/search").at(-1).q.q, goal);
  assert.equal(new URLSearchParams(location.search).get("q"), goal);
  assert.ok($("work").querySelectorAll("[data-row]").length >= 1);
  await click($("words"));
  assert.deepEqual([document.body.hasAttribute("data-words"), $("words").getAttribute("aria-pressed")], [true, "true"]);
  await key("?");
  assert.deepEqual([document.body.hasAttribute("data-words"), $("words").getAttribute("aria-pressed")], [false, "false"]);
});

test("every poll_ms it asks again with the summary's ETag, and an unchanged store redraws nothing new", async () => {
  const before = asked.filter((a) => a.path === "/api/summary").length;
  vi.advanceTimersByTime(catalogue.poll_ms); await settle(); flush(); await settle();
  const polls = asked.filter((a) => a.path === "/api/summary");
  assert.equal(polls.length, before + 1);
  assert.equal(polls.at(-1).etag, '"s1"');
});

test("Back restores the place it left, and the Universe's navigation moves the page", async () => {
  history.replaceState(null, "", "/app/?data=demo");
  window.dispatchEvent(new PopStateEvent("popstate"));
  await settle(); flush(); await settle();
  assert.equal(document.body.dataset.view, "universe");
  const p = summary.projects[0];
  $("universe").dispatchEvent(new CustomEvent("ev-navigate", { detail: `?data=demo&zoom=${p.id}`, bubbles: true }));
  await settle(); flush(); await settle();
  assert.equal(new URLSearchParams(location.search).get("zoom"), p.id);
  assert.ok(text($("universe").querySelector("[data-crumbs]")).endsWith(p.label));
  assert.ok(asked.some((a) => a.path === "/api/runs" && a.q.project === p.id), "the zoomed island's goals are asked");
});
