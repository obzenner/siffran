// app/route.js: the URL is the state. Parse and write are inverses, defaults stay out of links, and the list's query is not the period's window.
import assert from "node:assert/strict";
import { test } from "vitest";
import { href, move, parse, query, same, switchTo, up, windowOf, work, write } from "../app/route.js";

const P = (s) => new URLSearchParams(s);
test("a place round-trips through its URL, with defaults left out", () => {
  const place = parse(P("view=work&standing=needs_human&project=p1&period=7&since=100&q=tile%20index&run=abc&claim=C2&words=1"));
  assert.deepEqual(place, { view: "work", period: "7", since: 100, q: "tile index", run: "abc", claim: "C2", words: true, zoom: null, hl: null, filters: { standing: "needs_human", project: "p1" } });
  assert.deepEqual(parse(write(new URLSearchParams(), place)), place);
  assert.equal(href(parse(P(""))), "?");
  assert.equal(href(parse(P("")), P("data=demo")), "?data=demo");
});
test("nonsense is a default, never an exception; a claim needs a run", () => {
  const place = parse(P("view=zzz&period=9&since=abc&claim=C1&standing="));
  assert.deepEqual(place, { view: "universe", period: "30", since: null, q: "", run: null, claim: null, words: false, zoom: null, hl: null, filters: {} });
});
test("the list's query is its filters and a chosen day; the period is the strip's window only", () => {
  const place = parse(P("view=work&standing=proven&period=7"));
  assert.deepEqual(query(place), { standing: "proven" });
  assert.deepEqual(query({ ...place, since: 50 }), { standing: "proven", since: "50" });
  const hour = 3_600_000, now = 40 * 24 * hour + 17 * 60_000;
  assert.equal(windowOf(place, now), 40 * 24 * 3600 - 7 * 86400);
  assert.equal(windowOf({ ...place, period: "all" }, now), null);
  assert.equal(windowOf({ ...place, since: 50 }, now), 50);
});
test("moving keeps what it is not asked to change; work() drops the search and the open run", () => {
  const place = parse(P("standing=proven&q=x&run=abc"));
  assert.deepEqual(move(place, { filters: { project: "p" } }).filters, { standing: "proven", project: "p" });
  const w = work(place, { ask: "decide_claim" });
  assert.deepEqual([w.view, w.q, w.run, w.filters], ["work", "", null, { ask: "decide_claim" }]);
});
test("Esc leads from a claim to its run, from a run to the list, and from the list nowhere", () => {
  const a = parse(P("run=abc&claim=C1")), b = up(a), c = up(b);
  assert.deepEqual([b.claim, b.run, c.run], [null, "abc", null]);
  assert.ok(same(up(c), c));
});
test("the Universe is the home; its zoom and highlight are in the URL and survive the switch to the Inbox and back", () => {
  const home = parse(P(""));
  assert.equal(home.view, "universe");
  const zoomed = parse(P("zoom=s256-abc&hl=failed_audit&run=r1"));
  assert.deepEqual([zoomed.view, zoomed.zoom, zoomed.hl, zoomed.run], ["universe", "s256-abc", "failed_audit", "r1"]);
  assert.equal(parse(P("hl=nonsense")).hl, null, "a highlight is a published flag or nothing");
  const inbox = switchTo(zoomed, "inbox");
  assert.equal(inbox.view, "inbox");
  assert.deepEqual(parse(new URLSearchParams(href(inbox).slice(1))), inbox, "the inbox's URL still holds the zoom, the highlight and the open run");
  assert.deepEqual(switchTo(inbox, "universe"), zoomed, "and switching back restores the Universe's place");
  assert.equal(href(parse(P("view=universe"))), "?", "the home is the default and stays out of links");
  assert.equal(href(parse(P("view=inbox"))), "?view=inbox");
});
test("Esc in the Universe steps out: a claim, then the run, then the island, then nothing", () => {
  const a = parse(P("zoom=p&run=r&claim=C1")), b = up(a), c = up(b), d = up(c);
  assert.deepEqual([b.claim, b.run, c.run, c.zoom, d.zoom], [null, "r", null, "p", null]);
  assert.ok(same(up(d), d));
  assert.equal(up(parse(P("view=work&zoom=p"))).zoom, "p", "the work view's Esc does not move the Universe it came from");
});
