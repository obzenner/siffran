// app/poll.js on a fake clock: paused while hidden, resumed (at once) when shown, later after each failure, a cap.
import assert from "node:assert/strict";
import { test } from "vitest";
import { Poller, backoff } from "../app/poll.js";

function harness({ answers = [], hidden = false } = {}) {
  const h = { now: 0, timers: [], asked: [], hidden, answers: [...answers] };
  const poller = new Poller({
    interval: 100, cap: 800, hidden: () => h.hidden,
    tick: async () => { h.asked.push(h.now); return h.answers.length ? h.answers.shift() : true; },
    setTimer: (fn, ms) => { const t = { fn, at: h.now + ms }; h.timers.push(t); return t; },
    clearTimer: (t) => { h.timers = h.timers.filter((x) => x !== t); },
  });
  h.poller = poller;
  /** Run the timers due up to `until`, letting each tick settle. */
  h.advance = async (until) => {
    for (;;) {
      const next = h.timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at)[0];
      if (!next) break;
      h.timers = h.timers.filter((t) => t !== next); h.now = next.at; next.fn();
      await new Promise((r) => setImmediate(r));
    }
    h.now = until;
  };
  return h;
}

test("backoff doubles the wait for each failure in a row and stops at the cap", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 9].map((n) => backoff(n, 100, 800)), [100, 200, 400, 800, 800, 800]);
});

test("a good answer is asked again after the interval; a failed one later each time, and a good one resets", async () => {
  const h = harness({ answers: [true, false, false, true, true] });
  h.poller.start();
  await h.advance(2000);
  assert.deepEqual(h.asked, [100, 200, 400, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000]);
});

test("nothing is asked while the tab is hidden; showing it again asks at once, then on the interval", async () => {
  const h = harness();
  h.poller.start();
  await h.advance(250);
  assert.deepEqual(h.asked, [100, 200]);
  h.hidden = true;
  await h.advance(2000);
  assert.deepEqual(h.asked, [100, 200], "the pending timer fired and asked nothing, and no timer is left");
  assert.equal(h.timers.length, 0);
  h.hidden = false;
  h.poller.wake();
  await new Promise((r) => setImmediate(r));
  assert.equal(h.asked.length, 3);
  assert.equal(h.asked[2], 2000);
  await h.advance(2200);
  assert.equal(h.asked.length, 5);
});

test("started in a hidden tab it sets no timer at all", () => {
  const h = harness({ hidden: true });
  h.poller.start();
  assert.equal(h.timers.length, 0);
});

test("waking while a question is out or a timer is pending asks nothing extra; a stopped poller never asks", async () => {
  const h = harness();
  h.poller.start();
  h.poller.wake();
  await h.advance(50);
  assert.equal(h.asked.length, 0);
  h.poller.stop();
  await h.advance(1000);
  h.poller.wake();
  assert.equal(h.asked.length, 0);
});

test("a tick that throws counts as a failure and the loop goes on", async () => {
  // waits on the state (the fourth failed tick), not on time
  let calls = 0, fourth;
  const timers = [], reached = new Promise((r) => { fourth = r; });
  const p = new Poller({ interval: 10, cap: 40, hidden: () => false, tick: async () => { if (++calls === 4) fourth(); throw new Error("x"); },
    setTimer: (fn, ms) => { timers.push(ms); return setTimeout(fn, 0); }, clearTimer: clearTimeout });
  p.start();
  await reached;
  await new Promise((r) => setImmediate(r));  // the fourth failure is counted and the next wait scheduled
  p.stop();
  assert.equal(calls, 4);
  assert.deepEqual(timers.slice(0, 5), [10, 20, 40, 40, 40]);
});
