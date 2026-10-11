import assert from "node:assert/strict";
import { test } from "node:test";
import { attempts, groups, haystack, search } from "../app/search.js";
import { byKey, projectOf, rows, row, scenarios, view } from "./fixtures.mjs";

const goalOf = (s) => row(s).goal;

test("a blank query finds every run; a goal fragment finds the run with that goal (T6)", () => {
  assert.equal(search(view.projects, "").entries.length, rows.length);
  const found = search(view.projects, "NIGHTLY import").entries.map((e) => e.row.key);
  assert.ok(found.includes(scenarios.budget));
  assert.ok(found.length < rows.length);
});

test("claim text, project label and session are searched too, every word must match", () => {
  const withClaims = rows.find(({ row: r }) => r.claims?.length);
  const claim = withClaims.row.claims[0].text;
  assert.ok(search(view.projects, claim).entries.some((e) => e.row.key === withClaims.row.key));
  assert.ok(search(view.projects, withClaims.project.label).entries.length >= 1);
  assert.ok(search(view.projects, "atlas docs session").entries.some((e) => e.row.key === scenarios.verification));
  assert.equal(search(view.projects, `${goalOf("budget")} zzzzzz`).entries.length, 0);
});

test("runs whose claims were not read are counted, never taken for runs without matching claims", () => {
  const unread = rows.filter(({ row: r }) => r.claims === null).length;
  assert.ok(unread > 0);
  assert.equal(search(view.projects, "").unsearched, 0);
  assert.equal(search(view.projects, "zzzzzz").unsearched, unread);
  assert.ok(!haystack(rows.find(({ row: r }) => r.claims === null).row, view.projects[0]).includes("undefined"));
});

test("runs are grouped by the core's attention, in the contract's order, unread last, newest first", () => {
  const g = groups(search(view.projects, "").entries);
  assert.deepEqual(g.map((x) => x.attention), ["needs_you", "not_ended", "ended", null]);
  assert.equal(g.flatMap((x) => x.entries).length, rows.length);
  for (const x of g) {
    assert.ok(x.entries.every((e) => e.row.attention === x.attention));
    const times = x.entries.map((e) => e.row.updated ?? -Infinity);
    assert.deepEqual(times, [...times].sort((a, b) => b - a));
  }
});

test("a run's attempts come in the observer's order of generations, never by time (T5)", () => {
  const latest = row("attempts");
  const family = attempts(projectOf.get(latest.key), latest, byKey);
  assert.deepEqual(family.map((r) => r.generation), [1, 2, 3]);
  assert.equal(family.at(-1).key, latest.key);
  const alone = row("converged");
  assert.deepEqual(attempts(projectOf.get(alone.key), alone, byKey).map((r) => r.key), [alone.key]);
});
