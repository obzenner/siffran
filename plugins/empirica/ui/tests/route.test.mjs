// app/route.js: the selection is a run and optionally one claim; nothing else is in the URL's selection.
import assert from "node:assert/strict";
import { test } from "node:test";
import { NONE, parse, resolve, same, up, write } from "../app/route.js";

const q = (s) => new URLSearchParams(s);
const rows = new Map([["r1", { claims: [{ id: "C0" }, { id: "C1" }] }], ["r2", { claims: null }]]);

test("a run and a claim round-trip through the URL, beside the data source and the search", () => {
  const params = q("data=demo&q=tiles");
  write(params, { run: "a/b/1", claim: "C1" });
  assert.equal(params.toString(), "data=demo&q=tiles&run=a%2Fb%2F1&claim=C1");
  assert.deepEqual(parse(params), { run: "a/b/1", claim: "C1" });
  write(params, NONE);
  assert.equal(params.toString(), "data=demo&q=tiles");
});

test("a claim without a run is no selection; a blank run is none", () => {
  assert.deepEqual(parse(q("claim=C1")), NONE);
  assert.deepEqual(parse(q("run=")), NONE);
  assert.deepEqual(parse(q("run=r1")), { run: "r1", claim: null });
});

test("resolve drops a run the data lacks and a claim the run lacks, and keeps what is there", () => {
  assert.deepEqual(resolve({ run: "gone", claim: "C0" }, rows), NONE);
  assert.deepEqual(resolve({ run: "r1", claim: "C9" }, rows), { run: "r1", claim: null });
  assert.deepEqual(resolve({ run: "r1", claim: "C1" }, rows), { run: "r1", claim: "C1" });
  assert.deepEqual(resolve({ run: "r2", claim: "C0" }, rows), { run: "r2", claim: null }, "a run that was not read has no claims");
});

test("up leads from a claim to its run and from a run to the list; same compares both parts", () => {
  assert.deepEqual(up({ run: "r1", claim: "C1" }), { run: "r1", claim: null });
  assert.deepEqual(up({ run: "r1", claim: null }), NONE);
  assert.ok(same({ run: "r1", claim: null }, { run: "r1", claim: null }));
  assert.ok(!same({ run: "r1", claim: "C0" }, { run: "r1", claim: null }));
});
