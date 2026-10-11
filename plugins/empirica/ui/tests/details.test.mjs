// app/details.js on a fake clock and a fake client: every run document asked for (the selected one or a sibling) has a
// request state; a failure is retried a bounded number of times, later each time; nothing is retried while the tab is
// hidden (a retry that came due then runs when it is shown again); a manual Retry asks at once.
import assert from "node:assert/strict";
import { test } from "node:test";
import { Details } from "../app/details.js";

function harness({ hidden = false, answers = {} } = {}) {
  const h = { now: 0, timers: [], asked: [], hidden, answers, changed: 0, cache: new Map() };
  const client = {
    cached: (row) => h.cache.get(`${row.key}@${row.version}`) ?? null,
    detail: async (row) => {
      h.asked.push([h.now, row.key]);
      await null;
      const next = (h.answers[row.key] ?? []).shift() ?? "ok";
      if (next === "ok") { const d = { key: row.key }; h.cache.set(`${row.key}@${row.version}`, d); return { status: "ok", detail: d }; }
      return { status: next };
    },
  };
  const details = new Details({
    client, retries: 3, base: 100, cap: 400, hidden: () => h.hidden, onChange: () => { h.changed++; },
    setTimer: (fn, ms) => { const t = { fn, at: h.now + ms }; h.timers.push(t); return t; },
    clearTimer: (t) => { h.timers = h.timers.filter((x) => x !== t); },
  });
  h.details = details;
  h.advance = async (until) => {
    for (;;) {
      const next = h.timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at)[0];
      if (!next) break;
      h.timers = h.timers.filter((t) => t !== next); h.now = next.at; next.fn();
      await new Promise((r) => setImmediate(r));
    }
    h.now = until;
  };
  h.settle = () => new Promise((r) => setImmediate(r));
  return h;
}
const A = { key: "a", version: "1" }, B = { key: "b", version: "1" };

test("a run's document is pending while it is asked for, then ok, and a run nobody asked about is idle", async () => {
  const h = harness();
  assert.equal(h.details.state(A).status, "idle");
  h.details.want(A);
  assert.equal(h.details.state(A).status, "pending");
  await h.settle();
  assert.equal(h.details.state(A).status, "ok");
  assert.deepEqual(h.details.state(A).detail, { key: "a" });
});

test("a sibling that fails is a failed request with its own state, retried by itself (not fire-and-forget)", async () => {
  const h = harness({ answers: { b: ["error", "error", "ok"] } });
  h.details.want(A); h.details.want(B);
  await h.settle();
  assert.equal(h.details.state(B).status, "failed");
  assert.equal(h.details.state(B).failure, "error");
  assert.equal(h.details.state(A).status, "ok");
  await h.advance(100); assert.equal(h.details.state(B).status, "failed");
  await h.advance(300); assert.equal(h.details.state(B).status, "ok");
  assert.deepEqual(h.asked.filter(([, k]) => k === "b").map(([t]) => t), [0, 100, 300], "later each time");
});

test("a failure is retried only a few times, then waits for a manual Retry, which asks at once", async () => {
  const h = harness({ answers: { a: ["offline", "offline", "offline", "offline", "offline", "ok"] } });
  h.details.want(A);
  await h.settle();
  await h.advance(5000);
  assert.equal(h.asked.length, 4, "the first ask and three retries");
  assert.equal(h.details.state(A).status, "failed");
  h.details.retry(A);
  await h.settle();
  assert.equal(h.asked.length, 5);
  assert.equal(h.details.state(A).status, "failed", "a manual retry that fails starts the bounded retries again");
});

test("nothing is retried while hidden, an unselected sibling's included; showing the tab runs what came due", async () => {
  const h = harness({ answers: { a: ["error", "ok"], b: ["error", "ok"] } });
  h.details.want(A); h.details.want(B);
  await h.settle();
  h.hidden = true;
  await h.advance(2000);
  assert.equal(h.asked.length, 2, "neither retry was asked");
  assert.equal(h.timers.length, 0, "and no timer is left running");
  h.hidden = false;
  h.details.wake();
  await h.settle();
  assert.equal(h.asked.length, 4);
  assert.deepEqual([h.details.state(A).status, h.details.state(B).status], ["ok", "ok"]);
});

test("a new version of a run is a new request; the state of the old version is dropped", async () => {
  const h = harness();
  h.details.want(A);
  await h.settle();
  assert.equal(h.details.state({ key: "a", version: "2" }).status, "idle");
  h.details.want({ key: "a", version: "2" });
  await h.settle();
  assert.equal(h.details.state({ key: "a", version: "2" }).status, "ok");
});

test("want does not ask twice for a run that is pending, ok, or waiting for its retry", async () => {
  const h = harness({ answers: { a: ["error"] } });
  h.details.want(A); h.details.want(A);
  await h.settle();
  h.details.want(A);
  assert.equal(h.asked.length, 1);
});
