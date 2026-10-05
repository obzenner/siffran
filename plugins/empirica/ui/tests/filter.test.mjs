// VIEW.md R5, rule by rule, against the observer's demo fixtures (the plugin's own runs).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { apply, showing } from "../app/filter.js";

const data = JSON.parse(readFileSync(new URL("../../vendor/contracts/empirica-ui-view/v1/fixtures/view.json", import.meta.url)));
const { filters } = JSON.parse(readFileSync(new URL("../catalogue.json", import.meta.url)));
const states = JSON.parse(readFileSync(new URL("../states.json", import.meta.url)));
const runs = data.projects.flatMap((p) => p.runs), claims = runs.flatMap((r) => r.claims);
const view = (choice) => apply(data, choice, filters);

test("no filter: everything stays, every project keeps its place number", () => {
  const v = view({});
  assert.deepEqual(v.counts, { projects: data.projects.length, runs: runs.length, claims: claims.length });
  assert.deepEqual(v.projects.map((p) => p.n), data.projects.map((_, i) => i + 1));
  assert.equal(v.projects.flatMap((p) => p.runs).reduce((n, r) => n + r.edges.length, 0), runs.reduce((n, r) => n + r.edges.length, 0));
});

test("a run filter keeps the matching runs with all their claims; values within it are alternatives", () => {
  const v = view({ "run-state": ["converged", "active"] });
  const want = runs.filter((r) => ["converged", "active"].includes(r.state));
  assert.deepEqual(v.projects.flatMap((p) => p.runs.map((r) => r.key)), want.map((r) => r.key));
  assert.equal(v.counts.claims, want.reduce((n, r) => n + r.claims.length, 0));
  assert.ok(v.counts.projects < data.projects.length, "a project with no run left goes");
});

test("a claim filter keeps the matching claims; a run with none left goes, and so do edges to removed claims", () => {
  const v = view({ "claim-state": ["open"] });
  const kept = v.projects.flatMap((p) => p.runs);
  assert.equal(v.counts.claims, claims.filter((c) => c.state === "open").length);
  assert.ok(kept.every((r) => r.claims.length > 0 && r.claims.every((c) => c.state === "open")));
  assert.ok(kept.every((r) => r.total === runs.find((x) => x.key === r.key).claims.length), "a run keeps its own claim count");
  for (const r of kept) { const ids = new Set(r.claims.map((c) => c.id)); assert.ok(r.edges.every((e) => ids.has(e.from) && ids.has(e.to))); }
});

test("different filters must all hold", () => {
  const v = view({ "run-state": ["stopped_residual"], kind: ["needs-experiment"] });
  const want = runs.filter((r) => r.state === "stopped_residual").flatMap((r) => r.claims).filter((c) => c.kind === "needs-experiment");
  assert.equal(v.counts.claims, want.length);
});

test("a flag filter keeps the claims whose field is set; unset, it filters nothing", () => {
  const blocked = claims.filter((c) => c.blocker !== null);
  assert.ok(blocked.length > 0 && blocked.length < claims.length);
  assert.equal(view({ blocks: true }).counts.claims, blocked.length);
  assert.equal(view({ blocks: false }).counts.claims, claims.length);
});

test("a field filter on a claim fact keeps only those values", () => {
  for (const [id, field, value] of [["evidence", "evidence", "spike"], ["review", "review", "unreviewed"]]) {
    const want = claims.filter((c) => c[field] === value).length;
    assert.ok(want > 0, `the fixtures have a claim with ${field} ${value}`);
    assert.equal(view({ [id]: [value] }).counts.claims, want);
  }
});

test("a project keeps its name, or none", () => {
  assert.deepEqual(view({}).projects.map((p) => p.name), data.projects.map((p) => p.name));
});

test("a previous attempt stays only while both runs stay", () => {
  const withPrev = runs.find((r) => r.previous);
  assert.ok(withPrev, "the fixtures have one run with a previous attempt");
  assert.equal(view({}).projects.flatMap((p) => p.runs).find((r) => r.key === withPrev.key).previous, withPrev.previous);
  const before = runs.find((r) => r.key === withPrev.previous);
  const v = view({ "run-state": [withPrev.state].filter((s) => s !== before.state) });
  const still = v.projects.flatMap((p) => p.runs).find((r) => r.key === withPrev.key);
  if (still) assert.equal(still.previous, null);
});

test("the data is not changed", () => {
  const copy = JSON.stringify(data);
  view({ "claim-state": ["open"], blocks: true });
  assert.equal(JSON.stringify(data), copy);
});


test("the legend counts what is showing, per value in states.json order, leaving out values with none", () => {
  const order = (domain) => Object.keys(states[domain]);
  const quiet = (domain) => order(domain).find((v) => states[domain][v].quiet);
  const k = showing(data.projects, order, quiet);
  assert.deepEqual(k.runs.map((x) => x.value), order("run").filter((v) => runs.some((r) => r.state === v)));
  assert.equal(k.runs.reduce((n, x) => n + x.n, 0), runs.filter((r) => r.state).length);
  assert.equal(k.claims.reduce((n, x) => n + x.n, 0) + k.unsettled.reduce((n, x) => n + x.n, 0), claims.length,
    "each claim counts once");
  assert.equal(k.attempts, runs.filter((r) => r.previous).length);
  assert.deepEqual(showing(apply(data, { "claim-state": ["open"] }, filters).projects, order, quiet).claims.map((x) => x.value), ["open"]);
});

test("the legend counts apart, by review, the approved claims that no passing audit reviewed", () => {
  const order = (domain) => Object.keys(states[domain]);
  const quiet = (domain) => order(domain).find((v) => states[domain][v].quiet);
  assert.deepEqual([quiet("claim"), quiet("review")], ["approved", "passed"]);
  const k = showing(data.projects, order, quiet);
  const unsettled = claims.filter((c) => c.state === "approved" && c.review !== "passed");
  assert.ok(unsettled.length > 0 && unsettled.length < claims.filter((c) => c.state === "approved").length,
    "the demo has approved claims both settled and not");
  assert.equal(k.unsettled.reduce((n, x) => n + x.n, 0), unsettled.length);
  assert.equal(k.claims.find((x) => x.value === "approved").n, claims.filter((c) => c.state === "approved").length - unsettled.length);
  assert.deepEqual(k.unsettled.map((x) => x.value), order("review").filter((v) => unsettled.some((c) => c.review === v)));
});
