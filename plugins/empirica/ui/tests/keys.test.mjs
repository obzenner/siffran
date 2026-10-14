// app/keys.js: a key is a command or nothing; typing in a field is never a command; g needs its second key.
import assert from "node:assert/strict";
import { test } from "vitest";
import { Keys } from "../app/keys.js";

const key = (k, extra = {}) => ({ key: k, target: { closest: () => null }, ...extra });
const typing = (k) => ({ key: k, target: { closest: () => ({}) } });
test("single keys are commands", () => {
  const keys = new Keys();
  assert.deepEqual(["j", "k", "/", "?", "Escape"].map((k) => keys.command(key(k))), ["next", "previous", "search", "words", "close"]);
  assert.equal(keys.command(key("x")), null);
});
test("g then n goes to what needs a human; g then anything else is nothing, and does not stay armed", () => {
  const keys = new Keys();
  assert.equal(keys.command(key("g")), null); assert.equal(keys.command(key("n")), "needs");
  keys.command(key("g")); assert.equal(keys.command(key("j")), null); assert.equal(keys.command(key("j")), "next");
  assert.equal(keys.command(key("n")), null);
});
test("typing in a field is never a command, except Esc; chorded keys are the browser's", () => {
  const keys = new Keys();
  assert.deepEqual(["j", "/", "g"].map((k) => keys.command(typing(k))), [null, null, null]);
  assert.equal(keys.command(typing("Escape")), "close");
  assert.equal(keys.command(key("j", { metaKey: true })), null);
  assert.equal(keys.command(key("j", { ctrlKey: true })), null);
});
test("an armed g is dropped by typing in a field and by a modifier chord: g, x in an input, n is nothing; g, Ctrl+k, n is nothing (CP-3)", () => {
  const keys = new Keys();
  keys.command(key("g")); assert.equal(keys.command(typing("x")), null); assert.equal(keys.command(key("n")), null);
  keys.command(key("g")); assert.equal(keys.command(key("k", { ctrlKey: true })), null); assert.equal(keys.command(key("n")), null);
  for (const mod of ["metaKey", "altKey"]) { keys.command(key("g")); keys.command(key("k", { [mod]: true })); assert.equal(keys.command(key("n")), null, mod); }
});
