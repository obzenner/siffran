// Proves the adapter registers with Pi: the commands, the tool_call and
// agent_settled handlers, and the resources_discover handler that contributes the
// real Empirica skill directory.

import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { createEmpiricaExtension, DEFAULT_SKILLS_DIR } from "../src/index.ts";
import type { Response } from "../src/contract.ts";
import { FakePi } from "./fakes.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function noopDispatch(): Response {
  throw new Error("dispatch should not be called during registration");
}

function register(): FakePi {
  const pi = new FakePi();
  createEmpiricaExtension({ dispatch: noopDispatch })(pi);
  return pi;
}

test("registers the empirica commands with descriptions", () => {
  const pi = register();
  for (const name of ["empirica", "empirica-status", "report-convergence"]) {
    const command = pi.commands.get(name);
    assert.ok(command, `expected a '${name}' command to be registered`);
    assert.ok((command.description ?? "").length > 0);
    assert.equal(typeof command.handler, "function");
  }
});

test("registers the tool_call gate and the agent_settled observer", () => {
  const pi = register();
  assert.equal(typeof pi.handlers.get("tool_call"), "function");
  assert.equal(typeof pi.handlers.get("agent_settled"), "function");
});

test("resources_discover contributes the empirica skills directory", async () => {
  const pi = register();
  const result = await pi.resourcesDiscover()(
    { cwd: HERE, reason: "startup" },
    { ui: undefined as never },
  );
  assert.deepEqual(result.skillPaths, [DEFAULT_SKILLS_DIR]);
});

test("the contributed skills directory actually holds the empirica skill", () => {
  // The path must resolve to real resources, not just be well-formed.
  assert.ok(existsSync(DEFAULT_SKILLS_DIR), `${DEFAULT_SKILLS_DIR} must exist`);
  assert.ok(
    existsSync(path.join(DEFAULT_SKILLS_DIR, "empirica", "SKILL.md")),
    "expected empirica/SKILL.md under the contributed skills dir",
  );
});

test("a custom skillsDir overrides the default", async () => {
  const pi = new FakePi();
  createEmpiricaExtension({ dispatch: noopDispatch, skillsDir: "/tmp/x" })(pi);
  const result = await pi.resourcesDiscover()(
    { cwd: HERE, reason: "reload" },
    { ui: undefined as never },
  );
  assert.deepEqual(result.skillPaths, ["/tmp/x"]);
});
