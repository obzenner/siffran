// app/client.js on a fake server: every outcome is a value, an unchanged answer is a 304, a stale cursor is a restart, and a run's sheet is asked once.
import assert from "node:assert/strict";
import { test } from "vitest";
import { Client, validPage, validSheet, validSummary } from "../app/client.js";
import { rows, sheets, summary, scenarios } from "./fixtures.mjs";

const reply = (status, body, etag = null) => ({ status, ok: status >= 200 && status < 300, headers: { get: (h) => (h === "ETag" ? etag : null) }, json: async () => body, arrayBuffer: async () => new ArrayBuffer(0) });
const page = { contract: "empirica-ui-view/v1", token: "t1", rows: rows.slice(0, 2), total: 29, next: "c1", pending: 0, query: {}, unsearched: 0 };
const key = scenarios.verification, one = sheets[key];

function server(answer) {
  const asked = [];
  const client = new Client("demo", async (url, init) => { asked.push({ url, headers: init?.headers ?? {} }); return answer(url, init, asked.length); }, () => 5000);
  return { client, asked };
}

test("the contract fixtures are valid at the boundary, and nothing else is", () => {
  assert.ok(validSummary(summary) && validSheet(one) && validPage({ ...page, rows: rows.slice(0, 2).map((r) => ({ key: r.key, id: r.id })) }));
  assert.ok(!validSummary({ ...summary, contract: "something-else/v9" }) && !validSheet({ ...one, row: null }) && !validPage({ ...page, rows: [{}] }) && !validSummary(null));
});
test("the summary is asked with its ETag; a 304 is the same summary, and the check time moves", async () => {
  let n = 0;
  const { client, asked } = server(() => (++n === 1 ? reply(200, summary, '"a"') : reply(304)));
  const first = await client.summary(null);
  assert.equal(first.status, "changed"); assert.equal(client.checked, 5000);
  const second = await client.summary(null);
  assert.deepEqual([second.status, second.summary === first.summary], ["same", true]);
  assert.equal(asked[1].headers["If-None-Match"], '"a"');
  assert.match(asked[0].url, /source=demo/); assert.doesNotMatch(asked[0].url, /since/);
  await client.summary(1700000000);
  assert.match(asked[2].url, /since=1700000000/); assert.equal(asked[2].headers["If-None-Match"], undefined);
});
test("every failure is a value: offline, an error, a missing source, something that is not the contract", async () => {
  const cases = [[() => { throw new Error("x"); }, "offline"], [() => reply(500, {}), "error"], [() => reply(404, {}), "missing"], [() => reply(200, { nope: 1 }), "invalid"]];
  for (const [answer, status] of cases) assert.equal((await server(answer).client.summary()).status, status);
  assert.equal((await server(() => reply(400, {})).client.runs({})).status, "refused");
  assert.equal(server(() => { throw new Error("x"); }).client.checked, null);
});
test("a page is asked with its filters and cursor; a stale cursor says so with the current token", async () => {
  const { client, asked } = server((url) => (/cursor=old/.test(url) ? reply(409, { token: "t2" }) : reply(200, { ...page, rows: [{ key: "k", id: "i" }] })));
  assert.equal((await client.runs({ standing: "proven", since: "5" }, null, 100)).status, "ok");
  assert.match(asked[0].url, /standing=proven/); assert.match(asked[0].url, /limit=100/); assert.doesNotMatch(asked[0].url, /cursor/);
  assert.deepEqual(await client.runs({}, "old"), { status: "stale", token: "t2" });
  await client.search("tile index", "c9");
  assert.match(asked[2].url, /\/api\/search\?.*q=tile\+index/);
});
test("the summary is asked in the viewer's time zone, the one the observer counts the calendar's days in (AST-3)", async () => {
  const asked = [];
  const client = new Client("demo", async (url) => { asked.push(url); return reply(200, summary, '"a"'); }, () => 1, "America/New_York");
  await client.summary(null); await client.summary(5);
  assert.match(asked[0], /tz=America%2FNew_York/); assert.match(asked[1], /since=5.*tz=America%2FNew_York|tz=America%2FNew_York.*since=5/);
  assert.ok(!validSummary({ ...summary, calendar: undefined }), "a summary without its calendar is not the contract");
});
test("a run's sheet is asked once however many ask, kept by its ETag, and a 404 forgets it", async () => {
  let n = 0;
  const { client, asked } = server(() => (++n === 1 ? reply(200, one, '"s"') : n === 2 ? reply(304) : reply(404, {})));
  const [a, b] = await Promise.all([client.sheet("abc"), client.sheet("abc")]);
  assert.equal(asked.length, 1); assert.equal(a, b); assert.equal(a.status, "ok");
  assert.equal((await client.sheet("abc")).sheet, one); assert.equal(asked[1].headers["If-None-Match"], '"s"');
  assert.equal((await client.sheet("abc")).status, "missing");
  assert.equal(asked.length, 3); assert.equal(asked[2].headers["If-None-Match"], '"s"');
  n = 0; await client.sheet("abc");
  assert.deepEqual(asked[3].headers, {}, "after a 404 nothing is held: the next ask carries no ETag");
});
test("what each data source serves is the server's word (/api/about), and nothing when it does not say", async () => {
  const { client, asked } = server(() => reply(200, { observer: "empirica-observer", identity: "x", sources: { store: "generated", demo: "fixtures" } }));
  assert.deepEqual(await client.about(), { store: "generated", demo: "fixtures" });
  assert.match(asked[0].url, /^\/api\/about/);
  assert.deepEqual(await server(() => reply(200, { observer: "empirica-observer" })).client.about(), {});
  assert.deepEqual(await server(() => reply(500, {})).client.about(), {});
  assert.deepEqual(await new Client("demo", async () => { throw new Error("down"); }).about(), {});
});
