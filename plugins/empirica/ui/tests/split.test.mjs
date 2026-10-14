// app/split.js: the run sheet's divider, as pure functions. Where the sheet's width is clamped (between its own minimum and
// the main area's), when it takes the full width, and where each key moves it.
import assert from "node:assert/strict";
import { test } from "vitest";
import fc from "fast-check";
import catalogue from "../catalogue.json" with { type: "json" };
import { splitClamp, splitKey, toPx } from "../app/split.js";

const RUNS = { seed: 20261014, numRuns: 200 };

test("the width is clamped between the sheet's minimum and what leaves the main area its minimum, rounded", () => {
  fc.assert(fc.property(fc.integer({ min: -500, max: 4000 }), fc.integer({ min: 1, max: 4000 }), fc.integer({ min: 100, max: 600 }), fc.integer({ min: 100, max: 800 }), (want, total, min, mainMin) => {
    const got = splitClamp(want, total, min, mainMin);
    assert.equal(got.narrow, total < min + mainMin, "below both minimums: narrow");
    assert.equal(got.max, Math.max(min, total - mainMin));
    assert.ok(Number.isInteger(got.w) && got.w >= Math.round(min) && got.w <= Math.round(got.max));
    if (want >= min && want <= got.max) assert.equal(got.w, Math.round(want), "a width that fits is kept");
    if (!got.narrow) assert.ok(total - got.w >= mainMin - 1, "the main area keeps its minimum");
  }), RUNS);
  assert.deepEqual(splitClamp(400, 0, 320, 352), { w: 400, max: 400, narrow: false }, "not laid out yet: the wish stands");
});

test("keys: left and right a step (the sheet grows leftwards), Page four, Home its narrowest, End its widest; others nothing", () => {
  const at = { min: 320, max: 900, step: 32 };
  assert.deepEqual(["ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End"].map((k) => splitKey(k, 500, at)),
    [532, 468, 500 + catalogue.split_page * 32, 500 - catalogue.split_page * 32, 320, 900]);
  for (const k of ["ArrowUp", "a", "Enter", "Tab"]) assert.equal(splitKey(k, 500, at), undefined, k);
});

test("a length reads in px: px as given, rem by the root's font size", () => {
  assert.deepEqual([toPx("456px", 16), toPx("28.5rem", 16), toPx(" 20rem ", 10), toPx("", 16)], [456, 456, 200, 0]);
});
