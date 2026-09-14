// Proves the adapter registers with Pi: the /empirica command, the tool_call
// gate handler, and the resources_discover handler that contributes the real
// Empirica skill directory. No removed surfaces (agent_settled, tool_result,
// /empirica-status, /report-convergence commands, empirica_knowledge tool).

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

test("registers the /empirica command with a description", () => {
  const pi = register();
  const command = pi.commands.get("empirica");
  assert.ok(command, "expected an 'empirica' command to be registered");
  assert.ok((command.description ?? "").length > 0);
  assert.equal(typeof command.handler, "function");
});

test("does NOT register removed commands", () => {
  const pi = register();
  assert.equal(pi.commands.get("empirica-status"), undefined);
  assert.equal(pi.commands.get("report-convergence"), undefined);
});

test("registers only the tool_call gate (no tool_result, no agent_settled)", () => {
  const pi = register();
  assert.equal(typeof pi.handlers.get("tool_call"), "function");
  assert.equal(pi.handlers.get("tool_result"), undefined);
  assert.equal(pi.handlers.get("agent_settled"), undefined);
});

test("registers exactly two tools: report_convergence and empirica_status", () => {
  const pi = register();
  assert.deepEqual([...pi.tools.keys()].sort(), ["empirica_status", "report_convergence"]);
});

test("does NOT register empirica_knowledge tool", () => {
  const pi = register();
  assert.equal(pi.tools.get("empirica_knowledge"), undefined);
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

test("every registered tool declares a JSON-Schema object parameters block", () => {
  const host = new FakePi();
  createEmpiricaExtension({ dispatch: noopDispatch })(host);
  for (const def of host.tools.values()) {
    const schema = def.parameters as { type?: unknown; properties?: unknown };
    assert.equal(schema.type, "object", `${def.name}: parameters.type must be "object"`);
    assert.equal(typeof schema.properties, "object", `${def.name}: parameters.properties missing`);
  }
});
