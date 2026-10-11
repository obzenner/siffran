// app/client.js against a fake server: polling with the ETag, detail at the view's version, failures as values, the
// contract enforced at the boundary, requests coalesced, the cache bounded.
import assert from "node:assert/strict";
import { test } from "node:test";
import { Client, validDetail, validView } from "../app/client.js";

const run = (key, version = "v1") => ({ key, version, edges: [], claims: [] });
const view = (token, ...keys) => ({ contract: "empirica-ui-view/v1", token, coverage: { runs: keys.length, read: keys.length, unreadable: 0, partial: 0 }, projects: keys.length ? [{ id: "p", label: "p", families: [], runs: keys.map((k) => run(k)) }] : [] });
const doc = (key, version = "v1") => ({ contract: "empirica-ui-view/v1", key, version, residuals: [], outstanding: [], stale: [], steps: [], claims: [] });

function server() {
  const s = { calls: [], view: view("t1"), details: {}, down: false, status: {} };
  s.fetch = async (url, init = {}) => {
    s.calls.push([url, init.headers ?? {}]);
    if (s.down) throw new TypeError("network");
    const u = new URL(url, "http://x");
    const reply = (status, body, etag) => ({ status, ok: status < 300, headers: { get: (h) => (h === "ETag" ? etag : null) },
      arrayBuffer: async () => { s.drained = (s.drained ?? 0) + 1; return new ArrayBuffer(0); },
      json: async () => { if (s.garbled) throw new SyntaxError("not JSON"); return body; } });
    if (s.status[u.pathname]) return reply(s.status[u.pathname], null);
    if (u.pathname === "/api/view") {
      const etag = `"${s.view?.token}"`;
      return init.headers?.["If-None-Match"] === etag ? reply(304, null, etag) : reply(200, s.view, etag);
    }
    await s.hold;
    const d = s.details[u.searchParams.get("key")];
    return d !== undefined ? reply(200, d) : reply(404, null);
  };
  return s;
}

test("the first view is fetched; an unchanged store answers 304 and nothing is re-read", async () => {
  const s = server(), c = new Client("demo", s.fetch);
  assert.deepEqual(await c.view(), { status: "changed", view: s.view });
  assert.deepEqual(await c.view(), { status: "same" });
  assert.deepEqual(s.calls.map(([u, h]) => [u, h["If-None-Match"] ?? null]), [["/api/view?source=demo", null], ["/api/view?source=demo", '"t1"']]);
  s.view = view("t2");
  assert.equal((await c.view()).status, "changed");
});

test("a 304 or a failure has its body read, so the browser does not report the poll as cancelled", async () => {
  const s = server(), c = new Client("demo", s.fetch);
  await c.view();
  assert.equal(s.drained ?? 0, 0, "a reply with data is read by json()");
  await c.view();
  assert.equal(s.drained, 1, "the 304");
  s.status["/api/view"] = 500;
  await c.view();
  assert.equal(s.drained, 2, "a failure");
});

test("a run's detail is fetched once per version", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.details.k = doc("k");
  assert.equal((await c.detail(run("k"))).detail.version, "v1");
  assert.equal((await c.detail(run("k"))).detail.version, "v1");
  assert.equal(s.calls.length, 1);
  assert.equal(c.cached(run("k", "v2")), null, "a newer version in the view is not the cached one");
  s.details.k = doc("k", "v2");
  assert.equal((await c.detail(run("k", "v2"))).detail.version, "v2");
  assert.equal(s.calls.length, 2);
});

test("a detail the store already moved past is missing, not shown as the view's version", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.details.k = doc("k", "v3");
  assert.deepEqual(await c.detail(run("k", "v2")), { status: "missing" });
});

test("every detail outcome is a typed value: ok, missing, offline, invalid", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.details.k = doc("k");
  assert.equal((await c.detail(run("k"))).status, "ok");
  assert.deepEqual(await c.detail(run("gone")), { status: "missing" }, "404");
  s.status["/api/run"] = 500;
  assert.deepEqual(await c.detail(run("k", "v9")), { status: "error" }, "500: the server answered, it is not unreachable");
  delete s.status["/api/run"];
  s.down = true;
  assert.deepEqual(await c.detail(run("k", "v8")), { status: "offline" }, "no answer");
  s.down = false;
  s.garbled = true;
  assert.deepEqual(await c.detail(run("k", "v7")), { status: "invalid" }, "not JSON");
  s.garbled = false;
  for (const bad of [null, [], {}, { ...doc("k", "v6"), contract: "other/v1" }, { ...doc("k", "v6"), claims: undefined }, doc("other", "v6")]) {
    s.details.k = bad;
    assert.deepEqual(await c.detail(run("k", "v6")), { status: "invalid" }, JSON.stringify(bad));
  }
});

test("an unreachable server or a refused source is a state, not an exception", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.down = true;
  assert.deepEqual(await c.view(), { status: "offline" });
  s.down = false;
  s.status["/api/view"] = 500;
  assert.deepEqual(await c.view(), { status: "error" });
  s.status["/api/view"] = 404;
  assert.deepEqual(await c.view(), { status: "missing" }, "a source the server does not serve is not 'unreachable'");
});

test("a view that is not the contract is invalid: not JSON, null, an array, the wrong contract, missing parts", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.garbled = true;
  assert.deepEqual(await c.view(), { status: "invalid" });
  s.garbled = false;
  const good = view("t9", "k");
  const bad = [null, [], {}, { ...good, contract: "nope/v9" }, { ...good, projects: undefined }, { ...good, token: 7 },
    { ...good, projects: [{ id: "p", runs: [] }] }, { ...good, projects: [{ id: "p", label: "p", runs: [{ key: "k", version: "v" }] }] }];
  for (const b of bad) {
    s.view = b;
    assert.deepEqual(await c.view(), { status: "invalid" }, JSON.stringify(b));
  }
  s.view = good;
  assert.equal((await c.view()).status, "changed", "the client recovers on the next good answer");
  assert.ok(validView(good) && !validView({}) && validDetail(doc("k")) && !validDetail({}));
});

test("concurrent requests for one run and version are one request", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.details.k = doc("k");
  let release;
  s.hold = new Promise((r) => { release = r; });
  const three = [c.detail(run("k")), c.detail(run("k")), c.detail(run("k"))];
  release();
  const results = await Promise.all(three);
  assert.deepEqual(results.map((r) => r.status), ["ok", "ok", "ok"]);
  assert.equal(s.calls.length, 1);
  s.details.k = doc("k", "v2");
  await c.detail(run("k", "v2"));
  assert.equal(s.calls.length, 2, "another version is another request");
});

test("the cache keeps only the runs of the current view", async () => {
  const s = server(), c = new Client("store", s.fetch);
  for (const k of ["a", "b"]) { s.details[k] = doc(k); await c.detail(run(k)); }
  s.view = view("t3", "a");
  await c.view();
  assert.ok(c.cached(run("a")) && !c.cached(run("b")), "b left the store, so its detail left the cache");
});

test("the last good check moves on a good answer or an unchanged one, never on a failure", async () => {
  const s = server(); let clock = 1000;
  const c = new Client("demo", s.fetch, () => clock);
  assert.equal(c.checked, null);
  await c.view();
  assert.equal(c.checked, 1000);
  clock = 2000; await c.view();  // 304
  assert.equal(c.checked, 2000);
  clock = 3000; s.down = true;
  assert.equal((await c.view()).status, "offline");
  s.down = false; s.status["/api/view"] = 500;
  assert.equal((await c.view()).status, "error");
  assert.equal(c.checked, 2000, "what the page shows is as old as the last good answer");
});

test("a detail that failed is asked again, not remembered as failed (the Retry button and the backoff rely on it)", async () => {
  const s = server(), c = new Client("demo", s.fetch);
  const r = run("a"); s.details.a = doc("a");
  s.status["/api/run"] = 500;
  assert.equal((await c.detail(r)).status, "error");
  delete s.status["/api/run"];
  assert.equal((await c.detail(r)).status, "ok");
  assert.equal(s.calls.filter(([u]) => u.startsWith("/api/run")).length, 2);
});

test("a view whose project lacks its families is not the contract", async () => {
  assert.equal(validView({ ...view("t", "a"), projects: [{ id: "p", label: "p", runs: [run("a")] }] }), false);
});
