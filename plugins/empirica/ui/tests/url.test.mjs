// app/url.js: the filter choice survives the URL, and a URL keeps only what is on offer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { choiceFrom, writeChoice } from "../app/url.js";

const { filters } = JSON.parse(readFileSync(new URL("../catalogue.json", import.meta.url)));
const flag = filters.find((f) => f.flag), field = filters.find((f) => !f.flag);
const options = { [field.id]: [{ value: "a" }, { value: "b" }] };

test("a choice written into the URL reads back the same", () => {
  const q = new URLSearchParams("run=k"), choice = { [field.id]: ["a", "b"], [flag.id]: true };
  writeChoice(q, choice, filters);
  assert.deepEqual(choiceFrom(q, filters, options), choice);
  assert.equal(q.get("run"), "k", "the selection is not the filters' to touch");
});

test("a URL keeps only the values on offer, and a flag only as 1", () => {
  const q = new URLSearchParams({ [`f.${field.id}`]: "a,gone", [`f.${flag.id}`]: "yes" });
  assert.deepEqual(choiceFrom(q, filters, options), { [field.id]: ["a"] });
});

test("a filter the choice no longer sets leaves the URL", () => {
  const q = new URLSearchParams({ [`f.${field.id}`]: "a", [`f.${flag.id}`]: "1" });
  writeChoice(q, {}, filters);
  assert.equal(q.toString(), "");
});
