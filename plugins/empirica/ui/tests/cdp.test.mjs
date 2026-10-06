// tests/cdp.mjs launch arguments: Chrome runs with the real HOME, so it must never ask the login keychain.
import assert from "node:assert/strict";
import { test } from "node:test";
import { chromeArgs } from "./cdp.mjs";

test("Chrome always launches with the mock keychain and the basic password store", () => {
  const args = chromeArgs("/tmp/profile", 1500, 950);
  assert.ok(args.includes("--use-mock-keychain"));
  assert.ok(args.includes("--password-store=basic"));
  assert.ok(args.includes("--user-data-dir=/tmp/profile"));
  assert.ok(args.includes("--window-size=1500,950"));
});

test("the launch arguments do not depend on the size", () => {
  for (const flag of ["--use-mock-keychain", "--password-store=basic", "--headless=new"]) assert.ok(chromeArgs("/d", 1, 2).includes(flag));
});
