// Contract-owned host facts: the audit launch policy and the thinking levels (Empirica 4.1, D3/D8).
// One copy lives in contracts/empirica/v2/host-profiles.json; this proves the adapter reads it (not a
// literal of its own), validates it at the boundary, and that Python reads the same values.

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { auditLaunchInput, preflightLaunch } from "../src/audit-launch.ts";
import {
  AUDIT_LAUNCH_POLICY, HOST_PROFILE_ID, HOST_PROFILES_PATH, compareVersions, SUBAGENTS_COMPATIBILITY, THINKING_LEVELS, auditLaunchFields, auditLaunchPolicyFor,
  inSubagentsRange, parseHostProfiles,
} from "../src/host-profile.ts";
import { loadInventory, parseInventory } from "../src/subagent-inventory.ts";
import { reviewedVersions } from "./compat-versions.ts";
import { contractProfileId } from "./fakes.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../../../../..");
const PLUGIN = path.join(REPO, "plugins", "empirica");
const SSOT = path.join(REPO, "contracts", "empirica", "v2", "host-profiles.json");

const document = (): any => JSON.parse(readFileSync(SSOT, "utf8"));
const parse = (mutate: (doc: any) => void): unknown => { const doc = document(); mutate(doc); return parseHostProfiles(doc); };
const pi = (doc: any): any => doc.profiles.find((p: any) => p.profile_id === HOST_PROFILE_ID);

test("the adapter's policy is the contract's, byte-for-byte from the vendored copy of the SSOT", () => {
  assert.equal(readFileSync(HOST_PROFILES_PATH, "utf8"), readFileSync(SSOT, "utf8"));
  assert.deepEqual(AUDIT_LAUNCH_POLICY, pi(document()).audit_launch_policy);
  assert.deepEqual(AUDIT_LAUNCH_POLICY,
    { timeout_ms: 900_000, audit_child_tools: ["read", "grep", "find", "ls", "contact_supervisor"],
      tool_budget: { soft: 20, hard: 30, block: ["read", "grep", "find", "ls"] } });
  assert.deepEqual([...THINKING_LEVELS], document().thinking_levels);
  assert.deepEqual([...THINKING_LEVELS], ["off", "minimal", "low", "medium", "high", "xhigh", "max"]);
});

test("the launch fields are exactly timeoutMs and toolBudget, never a turn bound, and are copies", () => {
  const fields = auditLaunchFields(AUDIT_LAUNCH_POLICY);
  assert.deepEqual(Object.keys(fields), ["timeoutMs", "toolBudget"]);
  assert.deepEqual(fields, { timeoutMs: 900_000, toolBudget: { soft: 20, hard: 30, block: ["read", "grep", "find", "ls"] } });
  fields.toolBudget.block.push("bash");
  assert.deepEqual(AUDIT_LAUNCH_POLICY.tool_budget.block, ["read", "grep", "find", "ls"], "the policy is not aliased");
});

test("auditLaunchInput carries the policy and the host-owned fields, and no turnBudget", () => {
  const input = auditLaunchInput({ task: "T", model: "a/b", agentScope: "user", policy: AUDIT_LAUNCH_POLICY });
  assert.deepEqual(Object.keys(input).sort(), ["acceptance", "agentScope", "async", "context", "model", "task", "timeoutMs", "toolBudget"]);
  assert.equal("turnBudget" in input, false);
  assert.equal(input.async, false);
  assert.equal(input.context, "fresh", "EHB-AUDLAUNCH-02: an operator defaultSubagentContext:fork must not fork the author transcript into the auditor");
  const other = auditLaunchInput({ task: "T", model: "a/b", agentScope: "user",
    policy: { timeout_ms: 1000, audit_child_tools: ["read", "edit"], tool_budget: { soft: 1, hard: 2, block: ["edit"] } } });
  assert.equal(other.timeoutMs, 1000);
  assert.deepEqual(other.toolBudget, { soft: 1, hard: 2, block: ["edit"] });
});

// pi-subagents stops a call after the hard budget only for the tools listed in `block`; a list that
// misses a tool the auditor can call leaves the hard budget bounding nothing (B2 review, defect 2).
test("the hard tool budget blocks every tool the packaged auditor can call", () => {
  const frontmatter = readFileSync(path.join(PLUGIN, "agents", "pi", "empirica-auditor.md"), "utf8").split(/^---$/m)[1]!;
  const line = frontmatter.split("\n").find((row) => row.startsWith("tools:"));
  assert.ok(line, "the packaged auditor declares its tools");
  const tools = line.slice("tools:".length).split(",").map((tool) => tool.trim()).filter(Boolean);
  assert.ok(tools.length > 0);
  for (const tool of tools)
    assert.ok(AUDIT_LAUNCH_POLICY.tool_budget.block.includes(tool), `${tool} is not blocked once the hard budget is spent`);
});

test("the launch facts the preflight seam checks are read off the request that is sent", () => {
  const request = auditLaunchInput({ task: "T", model: "a/b", agentScope: "user", policy: AUDIT_LAUNCH_POLICY });
  assert.deepEqual(preflightLaunch(request), { async: false, output_mode: undefined, force_top_level_async: undefined, context: "fresh" });
  // Any field a later edit adds to the request is what the seam judges, not a constant.
  assert.deepEqual(preflightLaunch({ ...request, async: true, outputMode: "file-only", forceTopLevelAsync: true }),
    { async: true, output_mode: "file-only", force_top_level_async: true, context: "fresh" });
  assert.equal(preflightLaunch({ ...request, context: "fork" }).context, "fork");
});

test("the external-runtime policy is the contract's: a version range, one policy id, profile id derived from it", () => {
  const profile = pi(document());
  assert.deepEqual(SUBAGENTS_COMPATIBILITY, profile.subagents_compatibility);
  assert.equal(HOST_PROFILE_ID, `${profile.host_id}+${SUBAGENTS_COMPATIBILITY.policy_id}`);
  assert.deepEqual({ ...SUBAGENTS_COMPATIBILITY }, { package: "pi-subagents", policy_id: "pi-subagents-foreground-audit-v1",
    minimum: "0.50.0", maximum_exclusive: "1.0.0" });
  assert.equal(HOST_PROFILE_ID, contractProfileId("pi"), "read from the contract, never a copy");
  assert.ok(!/\d+\.\d+\.\d+/.test(HOST_PROFILE_ID), "a profile id never carries a version");
  for (const version of reviewedVersions())
    assert.ok(inSubagentsRange(SUBAGENTS_COMPATIBILITY, version), `${version} has a compat record, so it lies in the range`);
});

test("the range is half-open, numeric, and exact-version only", () => {
  const range = SUBAGENTS_COMPATIBILITY;
  for (const inside of ["0.50.0", "0.76.1", "0.100.0", "0.999.9"]) assert.equal(inSubagentsRange(range, inside), true, inside);
  for (const outside of ["0.49.9", "1.0.0", "1.2.3", "0.76.1-beta.1", "0.76", "v0.76.1", "", undefined, 76])
    assert.equal(inSubagentsRange(range, outside), false, String(outside));
});

test("each reviewed inventory records the Pi generation its package declares (peer_pi_ai), as data doc/hosts.md is checked against", () => {
  const peers = Object.fromEntries(reviewedVersions().map((v) => [v, loadInventory(v)!.peer_pi_ai]));
  for (const [version, peer] of Object.entries(peers)) assert.match(peer, /^>=\d+\.\d+\.\d+$/, version);  // the doc/hosts.md table is checked against these by the validator
  const shipped = JSON.parse(readFileSync(path.join(PLUGIN, "adapters", "pi", "compat", "pi-subagents-0.75.0.json"), "utf8"));
  const { peer_pi_ai: _recorded, ...withoutPeer } = shipped;
  assert.throws(() => parseInventory(withoutPeer), /missing "peer_pi_ai"/);
  for (const bad of ["", null, 86, [">=1.0.0"]])
    assert.throws(() => parseInventory({ ...shipped, peer_pi_ai: bad }), /peer_pi_ai must be the package's nonempty declared pi-ai peer range/);
});

test("parseHostProfiles accepts the shipped document and returns the policy per profile", () => {
  const parsed = parseHostProfiles(document());
  assert.deepEqual(auditLaunchPolicyFor(parsed, HOST_PROFILE_ID), AUDIT_LAUNCH_POLICY);
  assert.throws(() => auditLaunchPolicyFor(parsed, "codex@0.146.0"), /declares no audit_launch_policy/);
  assert.throws(() => auditLaunchPolicyFor(parsed, "unknown@1"), /declares no audit_launch_policy/);
});

const REJECTED: Array<[string, (doc: any) => void, RegExp]> = [
  ["a document that is not an object", () => { throw new Error("placeholder"); }, /placeholder/],
  ["no thinking_levels", (d) => { delete d.thinking_levels; }, /thinking_levels must be a nonempty list/],
  ["empty thinking_levels", (d) => { d.thinking_levels = []; }, /thinking_levels must be a nonempty list/],
  ["duplicate thinking levels", (d) => { d.thinking_levels = ["high", "high"]; }, /thinking_levels must be a nonempty list of unique/],
  ["an uppercase level", (d) => { d.thinking_levels = ["High"]; }, /thinking_levels must be a nonempty list/],
  ["a level with a colon", (d) => { d.thinking_levels = ["0:custom"]; }, /thinking_levels must be a nonempty list/],
  ["profiles that are not a list", (d) => { d.profiles = {}; }, /profiles must be a list/],
  ["an unknown policy field", (d) => { pi(d).audit_launch_policy.turn_budget = { max: 8 }; }, /unknown field "turn_budget"/],
  ["a missing timeout", (d) => { delete pi(d).audit_launch_policy.timeout_ms; }, /missing "timeout_ms"/],
  ["a zero timeout", (d) => { pi(d).audit_launch_policy.timeout_ms = 0; }, /timeout_ms must be an integer/],
  ["a fractional timeout", (d) => { pi(d).audit_launch_policy.timeout_ms = 1.5; }, /timeout_ms must be an integer/],
  ["a string timeout", (d) => { pi(d).audit_launch_policy.timeout_ms = "900000"; }, /timeout_ms must be an integer/],
  ["a timeout beyond the timer range", (d) => { pi(d).audit_launch_policy.timeout_ms = 2 ** 31; }, /timeout_ms must be an integer in 1\.\.2147483647/],
  ["an unknown budget field", (d) => { pi(d).audit_launch_policy.tool_budget.extra = 1; }, /unknown field "extra"/],
  ["soft above hard", (d) => { pi(d).audit_launch_policy.tool_budget.soft = 31; }, /soft must not exceed hard/],
  ["no child tool list", (d) => { delete pi(d).audit_launch_policy.audit_child_tools; }, /missing "audit_child_tools"/],
  ["an empty child tool list", (d) => { pi(d).audit_launch_policy.audit_child_tools = []; }, /audit_child_tools must be a nonempty list/],
  ["a duplicate child tool", (d) => { pi(d).audit_launch_policy.audit_child_tools = ["read", "read"]; }, /audit_child_tools must be a nonempty list of unique/],
  ["an empty block list", (d) => { pi(d).audit_launch_policy.tool_budget.block = []; }, /block must be a nonempty list/],
  ["a duplicate blocked tool", (d) => { pi(d).audit_launch_policy.tool_budget.block = ["write", "write"]; }, /block must be a nonempty list of unique/],
  ["a blank blocked tool", (d) => { pi(d).audit_launch_policy.tool_budget.block = [""]; }, /block must be a nonempty list/],
  ["a non-object policy", (d) => { pi(d).audit_launch_policy = []; }, /must be an object/],
  ["a profile without an id", (d) => { d.profiles.push({ host: "x" }); }, /profile_id must be a nonempty string/],
  ["an unknown subagents field", (d) => { pi(d).subagents_compatibility.latest = "0.75.0"; }, /unknown field "latest"/],
  ["a missing minimum", (d) => { delete pi(d).subagents_compatibility.minimum; }, /missing "minimum"/],
  ["a missing maximum", (d) => { delete pi(d).subagents_compatibility.maximum_exclusive; }, /missing "maximum_exclusive"/],
  ["a range as the minimum", (d) => { pi(d).subagents_compatibility.minimum = ">=0.50.0"; }, /exact versions with minimum below maximum_exclusive/],
  ["a prerelease as the maximum", (d) => { pi(d).subagents_compatibility.maximum_exclusive = "1.0.0-beta.1"; }, /exact versions/],
  ["an empty range", (d) => { pi(d).subagents_compatibility.maximum_exclusive = pi(d).subagents_compatibility.minimum; }, /minimum below maximum_exclusive/],
  ["an inverted range", (d) => { pi(d).subagents_compatibility.minimum = "2.0.0"; }, /minimum below maximum_exclusive/],
  ["a retired reviewed list", (d) => { pi(d).subagents_compatibility.reviewed_versions = ["0.50.0"]; }, /unknown field "reviewed_versions"/],
  ["a malformed policy id", (d) => { pi(d).subagents_compatibility.policy_id = "Pi Audit"; }, /policy_id must look like name-v1/],
];

for (const [name, mutate, message] of REJECTED) {
  test(`parseHostProfiles rejects ${name}`, () => {
    if (name.startsWith("a document that is not")) {
      for (const value of [null, [], "x", 1]) assert.throws(() => parseHostProfiles(value), /document must be an object/);
      return;
    }
    assert.throws(() => parse(mutate), message);
  });
}

// --- Python/TS mirror ---------------------------------------------------------------------------

function python(code: string): any {
  const result = spawnSync("python3", ["-c", code], { cwd: PLUGIN, encoding: "utf8", env: { ...process.env, PYTHONPATH: PLUGIN } });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test("Python reads the same thinking levels and the same audit launch policy as the adapter", () => {
  const seen = python(`
import json, pathlib
import adapters.identity as identity
doc = json.loads(pathlib.Path("vendor/contracts/empirica/v2/host-profiles.json").read_text(encoding="utf-8"))
profile = next(p for p in doc["profiles"] if p["profile_id"] == ${JSON.stringify(HOST_PROFILE_ID)})
print(json.dumps({"identity_levels": list(identity.THINKING_LEVELS), "pattern": identity._THINKING.pattern,
                  "policy": profile["audit_launch_policy"]}))
`);
  assert.deepEqual(seen.identity_levels, [...THINKING_LEVELS]);
  assert.equal(seen.pattern, `:(?:${[...THINKING_LEVELS].join("|")})$`);
  assert.deepEqual(seen.policy, AUDIT_LAUNCH_POLICY);
});

test("Python and the adapter strip the same levels: the shared cases agree with both", () => {
  const cases = JSON.parse(readFileSync(path.join(PLUGIN, "tests", "fixtures", "thinking-level-cases.json"), "utf8")).cases;
  const stripped = python(`
import json, pathlib
import adapters.identity as identity
cases = json.loads(pathlib.Path("tests/fixtures/thinking-level-cases.json").read_text(encoding="utf-8"))["cases"]
print(json.dumps({c["id"]: bool(identity._THINKING.search(c["model_id"].lower())) for c in cases}))
`);
  for (const row of cases) {
    const tsStrips = [...THINKING_LEVELS].some((level) => row.model_id.endsWith(`:${level}`));
    // The only sanctioned divergence is case: Python lowercases first.
    if (row.id === "level-is-case-sensitive-in-the-adapter") { assert.equal(tsStrips, false); assert.equal(stripped[row.id], true); }
    else assert.equal(stripped[row.id], tsStrips, row.id);
  }
});
