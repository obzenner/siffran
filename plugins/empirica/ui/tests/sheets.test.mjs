// app/sheets.js on a fake clock: a failed sheet is asked again later each time and not at all while hidden; Retry asks at once.
import assert from "node:assert/strict";
import { test } from "vitest";
import { Sheets } from "../app/sheets.js";

function harness({ answers = [], hidden = false } = {}) {
  const h = { asked: [], timers: [], hidden, changes: 0, answers: [...answers] };
  h.client = { sheet: async (id) => { h.asked.push(id); return h.answers.length ? h.answers.shift() : { status: "ok", sheet: { v: h.asked.length } }; } };
  h.sheets = new Sheets({ client: h.client, retries: 2, base: 100, cap: 400, hidden: () => h.hidden, onChange: () => h.changes++,
    setTimer: (fn, ms) => { const t = { fn, ms }; h.timers.push(t); return t; }, clearTimer: (t) => { h.timers = h.timers.filter((x) => x !== t); } });
  h.fire = async () => { const t = h.timers.shift(); t.fn(); await Promise.resolve(); await Promise.resolve(); };
  return h;
}
const settle = () => new Promise((r) => setTimeout(r, 0));

test("a sheet is asked once, shown while it is asked again, and a refresh keeps what is shown", async () => {
  const h = harness();
  h.sheets.want("a"); h.sheets.want("a"); await settle();
  assert.deepEqual([h.asked.length, h.sheets.state("a").status], [1, "ok"]);
  h.sheets.refresh("a");
  assert.deepEqual([h.sheets.state("a").status, h.sheets.state("a").sheet.v], ["pending", 1]);
  await settle(); assert.equal(h.sheets.state("a").sheet.v, 2);
});
test("a failure is asked again later each time, then left to the Retry button", async () => {
  const h = harness({ answers: [{ status: "error" }, { status: "error" }, { status: "error" }] });
  h.sheets.want("a"); await settle();
  assert.deepEqual([h.sheets.state("a").status, h.sheets.state("a").failure, h.timers[0].ms], ["failed", "error", 100]);
  await h.fire(); await settle(); assert.equal(h.timers[0].ms, 200);
  await h.fire(); await settle(); assert.equal(h.timers.length, 0);
  h.sheets.want("a"); await settle(); assert.equal(h.asked.length, 3);
  h.sheets.retry("a"); await settle(); assert.equal(h.sheets.state("a").status, "ok");
});
test("a missing run is not asked again by itself", async () => {
  const h = harness({ answers: [{ status: "missing" }] });
  h.sheets.want("a"); await settle(); assert.equal(h.timers.length, 0);
});
test("nothing is asked while hidden; what came due is asked when shown", async () => {
  const h = harness({ answers: [{ status: "offline" }] });
  h.sheets.want("a"); await settle();
  h.hidden = true; await h.fire(); assert.equal(h.asked.length, 1);
  h.sheets.wake(); assert.equal(h.asked.length, 1);
  h.hidden = false; h.sheets.wake(); await settle(); assert.deepEqual([h.asked.length, h.sheets.state("a").status], [2, "ok"]);
});
test("keeping one run forgets the others, and an answer that arrives late for a forgotten run is dropped", async () => {
  const h = harness();
  h.sheets.want("a"); h.sheets.keep("b"); await settle();
  assert.equal(h.sheets.state("a").status, "idle");
});
