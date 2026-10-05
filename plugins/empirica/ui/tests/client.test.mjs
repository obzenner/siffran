// app/client.js against a fake server: polling with the ETag, detail at the view's version, failures as states.
import assert from "node:assert/strict";
import { test } from "node:test";
import { Client } from "../app/client.js";

function server() {
  const s = { calls: [], view: { token: "t1", projects: [] }, details: {}, down: false };
  s.fetch = async (url, init = {}) => {
    s.calls.push([url, init.headers ?? {}]);
    if (s.down) throw new TypeError("network");
    const u = new URL(url, "http://x");
    const reply = (status, body, etag) => ({ status, ok: status < 300, headers: { get: (h) => (h === "ETag" ? etag : null) },
      json: async () => { if (s.garbled) throw new SyntaxError("not JSON"); return body; } });
    if (u.pathname === "/api/view" && s.missing) return reply(404, null);
    if (u.pathname === "/api/view") {
      const etag = `"${s.view.token}"`;
      return init.headers?.["If-None-Match"] === etag ? reply(304, null, etag) : reply(200, s.view, etag);
    }
    const d = s.details[u.searchParams.get("key")];
    return d ? reply(200, d) : reply(404, null);
  };
  return s;
}

test("the first view is fetched; an unchanged store answers 304 and nothing is re-read", async () => {
  const s = server(), c = new Client("demo", s.fetch);
  assert.deepEqual(await c.view(), { status: "changed", view: s.view });
  assert.deepEqual(await c.view(), { status: "same" });
  assert.deepEqual(s.calls.map(([u, h]) => [u, h["If-None-Match"] ?? null]), [["/api/view?source=demo", null], ["/api/view?source=demo", '"t1"']]);
  s.view = { token: "t2", projects: [] };
  assert.equal((await c.view()).status, "changed");
});

test("a run's detail is fetched once per version", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.details.k = { key: "k", version: "v1" };
  assert.equal((await c.detail({ key: "k", version: "v1" })).version, "v1");
  assert.equal((await c.detail({ key: "k", version: "v1" })).version, "v1");
  assert.equal(s.calls.length, 1);
  assert.equal(c.cached({ key: "k", version: "v2" }), null, "a newer version in the view is not the cached one");
  s.details.k = { key: "k", version: "v2" };
  assert.equal((await c.detail({ key: "k", version: "v2" })).version, "v2");
  assert.equal(s.calls.length, 2);
});

test("a detail the store already moved past is not shown as the view's version", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.details.k = { key: "k", version: "v3" };
  assert.equal(await c.detail({ key: "k", version: "v2" }), null);
});

test("an unreachable server or an unknown run is a state, not an exception", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.down = true;
  assert.deepEqual(await c.view(), { status: "error" });
  assert.equal(await c.detail({ key: "k", version: "v1" }), null);
  s.down = false;
  assert.equal(await c.detail({ key: "gone", version: "v1" }), null);
  s.missing = true;
  assert.deepEqual(await c.view(), { status: "missing" }, "a source the server does not serve is not 'unreachable'");
});

test("a reply that is not JSON is not 'unreachable': the view says invalid, a detail throws", async () => {
  const s = server(), c = new Client("store", s.fetch);
  s.details.k = { key: "k", version: "v1" };
  s.garbled = true;
  assert.deepEqual(await c.view(), { status: "invalid" });
  await assert.rejects(c.detail({ key: "k", version: "v1" }), SyntaxError);
});
