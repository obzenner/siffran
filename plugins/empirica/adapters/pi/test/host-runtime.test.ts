// The host-recorded runtime provenance of a StartRun (Empirica 4.1, D7), TypeScript side.
// The accept/reject cases are the shared fixture the Python application also runs
// (plugins/empirica/tests/fixtures/host-runtime-cases.json); here they prove the adapter's rule is the
// same rule, that it is read from the contract's policy, and that /empirica records exactly what it
// observed and refuses to start when it would not be accepted.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { SUBAGENTS_COMPATIBILITY, type SubagentsCompatibility } from "../src/host-profile.ts";
import { buildHostRuntime, hostRuntimeRejection } from "../src/host-runtime.ts";
import { recordedInvocation } from "./owner-fixture.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CASES = JSON.parse(readFileSync(path.join(HERE, "..", "..", "..", "tests", "fixtures", "host-runtime-cases.json"), "utf8")).cases as Array<{
  id: string; profile: "external" | "none"; host_runtime: unknown; expect: string | null; why: string;
}>;

test("every shared case is decided as the fixture states (the Python application decides the same)", () => {
  for (const row of CASES) {
    const policy = row.profile === "external" ? SUBAGENTS_COMPATIBILITY : undefined;
    assert.equal(hostRuntimeRejection(policy, row.host_runtime) ?? null, row.expect, `${row.id}: ${row.why}`);
  }
});

test("the rule reads the policy it is given, not a literal", () => {
  const good = CASES.find((row) => row.id === "newest-reviewed")!.host_runtime;
  const narrowed: SubagentsCompatibility = { ...SUBAGENTS_COMPATIBILITY, reviewed_versions: ["0.75.1"] };
  assert.equal(hostRuntimeRejection(narrowed, good), "version");
  assert.equal(hostRuntimeRejection(SUBAGENTS_COMPATIBILITY, good), undefined);
});

test("buildHostRuntime records a copy of the observed provenance under the policy id, and refuses what would be rejected", () => {
  const recorded = recordedInvocation().host_runtime;
  const built = buildHostRuntime(SUBAGENTS_COMPATIBILITY, recorded.subagents);
  assert.deepEqual(built, recorded);
  assert.notEqual(built.subagents, recorded.subagents, "a copy, not an alias");
  assert.throws(() => buildHostRuntime(SUBAGENTS_COMPATIBILITY, { ...recorded.subagents, version: "9.9.9" }), /cannot be recorded \(version\)/);
  assert.throws(() => buildHostRuntime(SUBAGENTS_COMPATIBILITY, { ...recorded.subagents, preflight_path: "/elsewhere/preflight.js" }), /\(paths\)/);
});
