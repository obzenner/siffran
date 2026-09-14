// Emitted-builder schema conformance + lexical projection (D6-C C3).
//
// Two complementary checks share the suite:
//   (a) Lexical projection: a lexical parse of contract.ts reads the Command
//       union and each command interface's required/optional fields from the
//       ordinary TypeScript text, then proves the TS outbound types are a
//       CANONICAL-REQUIRED-EXACT, TS-OPTIONAL-SUBSET, NO-TS-EXTRAS partition of
//       the canonical request schema ($defs): same discriminators; canonical
//       required fields are exact in both directions (schema-required <=>
//       TS-required), TS optional fields are a subset of schema-optional (the
//       reverse is NOT asserted), and every TS field is a schema property (no
//       TS extras). The projection cannot omit a canonical required field. No
//       TypeScript compiler, no manual schema literal duplication; single source of truth.
//   (b) Emitted-builder conformance: one builder table where every emitted
//       envelope validates against the canonical request.schema.json via
//       Python jsonschema.
//
// Adapter integration (status, direct tool Block/Inert/openFault, compaction)
// lives in gate.test.ts; pure gate/notice unit tests live in translate.test.ts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { type Request } from "../src/contract.ts";
import {
  startRunRequest,
  resolveRunRequest,
  evaluateRunRequest,
  restoreRunRequest,
} from "../src/translate.ts";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../../../../..");
const schemaPath = resolve(repo, "contracts/empirica/v2/request.schema.json");
const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
const contractSrc = readFileSync(resolve(here, "../src/contract.ts"), "utf8");

// --- (a) lexical projection: contract.ts -> canonical schema -----------------

function parseCommandUnion(src: string): string[] {
  const m = src.match(/export\s+type\s+Command\s*=\s*([\s\S]*?);/);
  assert.ok(m, "Command union not found in contract.ts");
  return m[1].split("|").map((s) => s.trim()).filter(Boolean);
}

function parseStringUnion(src: string, name: string): string[] {
  const m = src.match(new RegExp(`export\\s+type\\s+${name}\\s*=\\s*([\\s\\S]*?);`));
  assert.ok(m, `${name} type alias not found in contract.ts`);
  return m[1].split("|").map((s) => s.trim().replace(/^"|"$/g, "")).filter(Boolean);
}

function parseStringConst(src: string, name: string): string {
  const m = src.match(new RegExp(`export\\s+const\\s+${name}\\s*=\\s*"([^"]+)"`));
  assert.ok(m, `const ${name} not found in contract.ts`);
  return m[1];
}

/** Read interface fields as { field, optional } from ordinary TS lexical forms. */
function parseInterfaceFields(
  src: string,
  name: string,
): Array<{ field: string; optional: boolean }> {
  const re = new RegExp(`export\\s+interface\\s+${name}\\s*\\{([^}]*)\\}`, "m");
  const m = src.match(re);
  assert.ok(m, `interface ${name} not found in contract.ts`);
  const fields: Array<{ field: string; optional: boolean }> = [];
  for (const line of m[1].split("\n")) {
    const fm = line.trim().match(/^(\w+)(\?)?:\s/);
    if (fm) fields.push({ field: fm[1], optional: fm[2] === "?" });
  }
  return fields;
}

const COMMAND_MAP: Record<string, string> = {
  StartRunCommand: "startRun",
  ResolveRunCommand: "resolveRun",
  EvaluateRunCommand: "evaluateRun",
  RestoreRunCommand: "restoreRun",
};

/** Assert a TS interface's partition is canonical-required-exact, with the TS
 * optional fields a subset of schema-optional and no TS extras: every
 * schema-required field is present and required in TS (canonical required exact,
 * both directions), every TS-optional field is schema-optional (subset), and
 * every TS field is a schema property (no extras). */
function assertCanonicalRequiredExactOptionalSubsetNoExtras(iface: string, defName: string): void {
  const fields = parseInterfaceFields(contractSrc, iface);
  const def = schema.$defs[defName];
  assert.ok(def, `schema must define $defs.${defName}`);
  const schemaRequired = new Set((def.required ?? []) as string[]);
  const schemaProps = new Set(Object.keys(def.properties));
  const tsFields = new Map(fields.map((f) => [f.field, f.optional]));

  for (const { field, optional } of fields) {
    assert.ok(schemaProps.has(field), `${iface}.${field} is not in schema ${defName}.properties`);
    if (!optional)
      assert.ok(schemaRequired.has(field), `${iface}.${field} required in TS but not schema ${defName}.required`);
    if (optional)
      assert.ok(!schemaRequired.has(field), `${iface}.${field} optional in TS but required in schema ${defName}`);
  }
  // schema-required => TS-required (and present): projection cannot omit a canonical required field.
  for (const req of schemaRequired) {
    assert.ok(tsFields.has(req), `schema ${defName} requires ${req} but ${iface} omits it`);
    assert.ok(tsFields.get(req) === false, `schema ${defName} requires ${req} but ${iface} makes it optional`);
  }
}

test("lexical projection: Command union is the four retained command interfaces", () => {
  assert.deepEqual(parseCommandUnion(contractSrc), [
    "StartRunCommand",
    "ResolveRunCommand",
    "EvaluateRunCommand",
    "RestoreRunCommand",
  ]);
});

test("lexical projection: PROTOCOL const matches the canonical schema protocol const", () => {
  assert.equal(parseStringConst(contractSrc, "PROTOCOL"), schema.properties.protocol.const);
});

test("lexical projection: EvaluateIntent enum matches the canonical schema", () => {
  assert.deepEqual(
    parseStringUnion(contractSrc, "EvaluateIntent").sort(),
    schema.$defs.evaluateRun.properties.intent.enum.sort(),
  );
});

test("lexical projection: each TS command interface is canonical-required-exact, TS-optional subset, no TS extras", () => {
  for (const [iface, defName] of Object.entries(COMMAND_MAP)) assertCanonicalRequiredExactOptionalSubsetNoExtras(iface, defName);
});

test("lexical projection: RunSelector is canonical-required-exact, TS-optional subset, no TS extras", () => {
  assertCanonicalRequiredExactOptionalSubsetNoExtras("RunSelector", "runSelector");
});

test("lexical projection: Budgets and Modes are canonical-optional schema properties", () => {
  for (const [iface, defName] of [["Budgets", "budgets"], ["Modes", "modes"]] as const) {
    const def = schema.$defs[defName];
    assert.ok(!Array.isArray(def.required), `${iface}: schema ${defName} must not require fields`);
    for (const { field } of parseInterfaceFields(contractSrc, iface))
      assert.ok(def.properties[field], `${iface}.${field} not in schema ${defName}`);
  }
});

// --- (b) emitted-builder conformance (one builder table) ---------------------

const BUILDER_TABLE: Array<{ type: string; build: () => Request }> = [
  { type: "StartRun", build: () => startRunRequest({ project: "p", session: "s" }, "goal", "r1") },
  { type: "StartRun", build: () => startRunRequest({ project: "p", session: "s" }, "g", "r2", { maxPasses: 3, maxSpawns: 1, modes: { cli_exec: true, multi_provider: false } }) },
  { type: "ResolveRun", build: () => resolveRunRequest({ project: "p", session: "s" }, "r3") },
  { type: "EvaluateRun", build: () => evaluateRunRequest("h", "continue", "r4") },
  { type: "EvaluateRun", build: () => evaluateRunRequest("h", "report_convergence", "r5") },
  { type: "EvaluateRun", build: () => evaluateRunRequest("h", "stop", "r6") },
  { type: "RestoreRun", build: () => restoreRunRequest("h", "r7") },
];

const pythonAvailable = (() => {
  try {
    return spawnSync("python3", ["--version"], { stdio: "ignore" }).status === 0;
  } catch {
    return false;
  }
})();

test(
  "every emitted builder envelope validates against the canonical request schema (Python jsonschema)",
  { skip: !pythonAvailable ? "python3 missing" : false },
  () => {
    const builds = BUILDER_TABLE.map((b) => b.build());
    const script =
      "import sys, json, jsonschema\n" +
      "schema = json.load(open(sys.argv[1]))\n" +
      "for env in json.load(sys.stdin):\n    jsonschema.validate(instance=env, schema=schema)\nprint('ok')";
    const result = spawnSync("python3", ["-c", script, schemaPath], {
      input: JSON.stringify(builds),
      timeout: 15_000,
      encoding: "utf-8",
    });
    assert.equal(result.status, 0, `jsonschema validation failed: ${result.stderr ?? ""}`);
    assert.equal(result.stdout.trim(), "ok");
  },
);
