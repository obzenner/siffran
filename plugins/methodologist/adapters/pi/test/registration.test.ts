// Proves the adapter registers with Pi: the `/think` command and the
// `resources_discover` handler that contributes the real methodology skill dir.

import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import {
  createMethodologistExtension,
  DEFAULT_SKILLS_DIR,
} from "../src/index.ts";
import type { Response } from "../src/contract.ts";
import { FakePi } from "./fakes.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function noopDispatch(): Response {
  throw new Error("dispatch should not be called during registration");
}

test("registers a /think command with a description", () => {
  const pi = new FakePi();
  createMethodologistExtension({ dispatch: noopDispatch })(pi);

  const command = pi.commands.get("think");
  assert.ok(command, "expected a 'think' command to be registered");
  assert.ok(command.description.length > 0);
  assert.equal(typeof command.handler, "function");
});

test("registers the model-to-bridge selection tool", () => {
  const pi = new FakePi();
  createMethodologistExtension({ dispatch: noopDispatch })(pi);

  const tool = pi.tools.get("methodologist_select");
  assert.ok(tool, "expected the semantic selection bridge tool");
  assert.match(tool.description, /shared registry/);
});

test("resources_discover contributes the methodology skills directory", async () => {
  const pi = new FakePi();
  createMethodologistExtension({ dispatch: noopDispatch })(pi);

  const result = await pi.resourcesDiscover()(
    { cwd: HERE, reason: "startup" },
    { ui: undefined as never },
  );

  assert.deepEqual(result.skillPaths, [DEFAULT_SKILLS_DIR]);
});

test("the contributed skills directory actually holds the think skill", () => {
  // The path must resolve to real resources, not just be well-formed.
  assert.ok(existsSync(DEFAULT_SKILLS_DIR), `${DEFAULT_SKILLS_DIR} must exist`);
  assert.ok(
    existsSync(path.join(DEFAULT_SKILLS_DIR, "think", "SKILL.md")),
    "expected think/SKILL.md under the contributed skills dir",
  );
});

test("a custom skillsDir overrides the default", async () => {
  const pi = new FakePi();
  createMethodologistExtension({ dispatch: noopDispatch, skillsDir: "/tmp/x" })(pi);

  const result = await pi.resourcesDiscover()(
    { cwd: HERE, reason: "reload" },
    { ui: undefined as never },
  );
  assert.deepEqual(result.skillPaths, ["/tmp/x"]);
});
