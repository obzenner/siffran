// app/route.js: the selection value, its URL form, and the ladder of parents. Table-driven; each row has a negative.
import assert from "node:assert/strict";
import { test } from "node:test";
import catalogue from "../catalogue.json" with { type: "json" };
import { MARKED, markedOf, parentOf, parse, path, same, write } from "../app/route.js";

const q = (s) => new URLSearchParams(s);
const runs = new Map([["r1", { key: "r1", project: "p1" }]]);
const CASES = [
  ["", { kind: "none" }],
  ["project=p1", { kind: "project", project: "p1" }],
  ["run=r1", { kind: "run", run: "r1" }],
  ["run=r1&claim=C1", { kind: "claim", run: "r1", claim: "C1" }],
  ["run=r1&step=2", { kind: "step", run: "r1", step: 2 }],
  ["run=r1&file=src%2Fa.py", { kind: "file", run: "r1", file: "src/a.py" }],
];

test("each selection round-trips through the URL and only its own parameters are written", () => {
  for (const [query, sel] of CASES) {
    assert.deepEqual(parse(q(query)), sel, query);
    const out = q("data=demo&f.kind=ordinary&project=stale&run=stale&claim=X&step=9&file=f");
    write(out, sel);
    assert.equal(out.get("data"), "demo", "the data source stays");
    assert.equal(out.get("f.kind"), "ordinary", "the filters stay");
    assert.deepEqual(parse(out), sel, `round trip of ${query}`);
    assert.equal([...out.keys()].filter((k) => ["project", "run", "claim", "step", "file"].includes(k)).length, Object.keys(sel).length - 1, "no other level is left behind");
  }
});

test("a run with several children names the first of claim, step, file; a bad step names its run", () => {
  assert.deepEqual(parse(q("run=r1&file=a&step=2&claim=C1")), { kind: "claim", run: "r1", claim: "C1" });
  assert.deepEqual(parse(q("run=r1&file=a&step=2")), { kind: "step", run: "r1", step: 2 });
  for (const bad of ["0", "-1", "x", "1.5", "01"]) assert.deepEqual(parse(q(`run=r1&step=${bad}`)), { kind: "run", run: "r1" }, bad);
  assert.deepEqual(parse(q("claim=C1&step=2")), { kind: "none" }, "a child without its run is nothing");
  assert.deepEqual(parse(q("project=p1&run=r1")), { kind: "run", run: "r1" }, "the run wins over its project");
});

test("the parent of each level is the one above it; none has none", () => {
  const up = (query) => parentOf(parse(q(query)), runs);
  assert.deepEqual(up("run=r1&step=2"), { kind: "run", run: "r1" });
  assert.deepEqual(up("run=r1&file=a"), { kind: "run", run: "r1" });
  assert.deepEqual(up("run=r1&claim=C1"), { kind: "run", run: "r1" });
  assert.deepEqual(up("run=r1"), { kind: "project", project: "p1" });
  assert.deepEqual(up("project=p1"), { kind: "none" });
  assert.equal(up(""), null);
  assert.deepEqual(parentOf({ kind: "run", run: "gone" }, runs), { kind: "none" }, "an unknown run leads out");
});

test("the path lists every ancestor outermost first, ending in the selection", () => {
  assert.deepEqual(path(parse(q("run=r1&step=2")), runs).map((s) => s.kind), ["project", "run", "step"]);
  assert.deepEqual(path(parse(q("project=p1")), runs).map((s) => s.kind), ["project"]);
  assert.deepEqual(path({ kind: "none" }, runs), []);
  assert.ok(same(path(parse(q("run=r1")), runs)[0], { kind: "project", project: "p1" }));
});

// route.js reads the levels and the parents from catalogue.json; nothing here may disagree with them (R3 m1).
const sample = (kind) => ({ none: { kind: "none" }, project: { kind: "project", project: "p1" }, run: { kind: "run", run: "r1" },
  claim: { kind: "claim", run: "r1", claim: "C1" }, step: { kind: "step", run: "r1", step: 2 }, file: { kind: "file", run: "r1", file: "a" } })[kind];
const agrees = (parents) => catalogue.route.levels.every((l) => parentOf(sample(l), runs).kind === parents[l]);

test("the ladder of parents is the catalogue's, level by level", () => {
  assert.ok(agrees(catalogue.route.parent));
  assert.ok(!agrees({ ...catalogue.route.parent, step: "project" }), "control: a table that disagrees is caught");
  assert.deepEqual(catalogue.route.levels.filter((l) => !(l in catalogue.route.parent)), []);
  assert.deepEqual(catalogue.route.levels.map((l) => parse(q(write_(sample(l)))).kind), catalogue.route.levels);
});
function write_(sel) { const out = q(""); write(out, sel); return out.toString(); }

test("a step or a file has no mark: closing it lands on the run; every marked level is its own", () => {
  assert.deepEqual(markedOf(sample("step"), runs), { kind: "run", run: "r1" });
  assert.deepEqual(markedOf(sample("file"), runs), { kind: "run", run: "r1" });
  for (const l of MARKED) assert.deepEqual(markedOf(sample(l), runs), sample(l));
  assert.equal(markedOf(sample("none"), runs), null);
  assert.ok(MARKED.every((l) => catalogue.route.levels.includes(l)));
});
