// scripts/lib/npm_fetch.mjs with an injected `run`: no network, no npm. Every failure mode of the
// fetcher must surface as an error, because a matrix cell that cannot fetch its package must fail.
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { installPackage, packPackage, registryVersions } from "../lib/npm_fetch.mjs";

const scratch = (t) => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "npm-fetch-test-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
};

test("install names the exact version, an explicit cache and prefix, and never runs scripts", (t) => {
  const dest = scratch(t);
  let seen;
  const where = installPackage({ name: "pi-subagents", version: "0.64.0", dest, cache: "/c", run: (command, args, options) => {
    seen = { command, args, options }; return { status: 0 };
  } });
  assert.equal(where, path.join(dest, "node_modules", "pi-subagents"));
  assert.equal(seen.command, "npm");
  assert.deepEqual(seen.args.slice(0, 2), ["install", "pi-subagents@0.64.0"]);
  for (const flag of ["--ignore-scripts", "--omit=peer", "--no-package-lock"]) assert.ok(seen.args.includes(flag), flag);
  assert.equal(seen.args[seen.args.indexOf("--cache") + 1], "/c");
  assert.equal(seen.args[seen.args.indexOf("--prefix") + 1], dest);
  assert.ok(seen.options.timeout > 0);
});

test("a range, a tag or a prerelease is refused before npm is run", (t) => {
  for (const version of ["^0.64.0", "latest", "0.64.0-beta.1", "0.64"]) {
    assert.throws(() => installPackage({ name: "x", version, dest: scratch(t), cache: "/c", run: () => assert.fail("npm must not run") }), /not an exact release/);
    assert.throws(() => packPackage({ name: "x", version, dest: scratch(t), cache: "/c", run: () => assert.fail("npm must not run") }), /not an exact release/);
  }
});

test("a timeout and a nonzero exit are errors that say which", (t) => {
  const timeout = () => ({ error: Object.assign(new Error("x"), { code: "ETIMEDOUT" }), status: null });
  assert.throws(() => installPackage({ name: "x", version: "1.0.0", dest: scratch(t), cache: "/c", run: timeout }), /timed out/);
  assert.throws(() => installPackage({ name: "x", version: "1.0.0", dest: scratch(t), cache: "/c", run: () => ({ status: 1, stderr: "E404\nnot found" }) }), /exited 1: E404 \| not found/);
});

test("pack fails when npm leaves no tarball, and unpacks under <dest>/package otherwise", (t) => {
  assert.throws(() => packPackage({ name: "x", version: "1.0.0", dest: scratch(t), cache: "/c", run: () => ({ status: 0 }) }), /produced no tarball/);
  const dest = scratch(t);
  const commands = [];
  const root = packPackage({ name: "x", version: "1.0.0", dest, cache: "/c", run: (command, args) => {
    commands.push(command); if (command === "npm") writeFileSync(path.join(dest, "x-1.0.0.tgz"), ""); return { status: 0 };
  } });
  assert.deepEqual(commands, ["npm", "tar"]);
  assert.equal(root, path.join(dest, "package"));
});

test("registry versions must be a list of strings", () => {
  const reply = (stdout) => () => ({ status: 0, stdout });
  assert.deepEqual(registryVersions({ name: "x", cache: "/c", run: reply('["1.0.0","1.1.0"]') }), ["1.0.0", "1.1.0"]);
  assert.throws(() => registryVersions({ name: "x", cache: "/c", run: reply('"1.0.0"') }), /list of versions/);
  assert.throws(() => registryVersions({ name: "x", cache: "/c", run: reply("[1,2]") }), /list of versions/);
  assert.throws(() => registryVersions({ name: "x", cache: "/c", run: reply("not json") }));
});
