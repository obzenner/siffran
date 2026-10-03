// The adapter's mirrored Pi API (src/pi-types.ts) against Pi's own declarations. `make
// empirica-pi-api-typecheck` fetches each reviewed Pi version and runs the same check; here the
// package roots can be supplied as PI_API_ROOTS='{"0.84.1":"<pi package dir>",...}' (unsupplied
// versions are reported, not skipped silently). The looser mirror of 4.0 is kept as a negative
// control: it must fail against every supplied Pi version.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { MIRROR, PI_API_VERSIONS, PI_PACKAGE, projectFiles, typecheckAgainst } from "../pi_api_typecheck.mjs";

const roots = process.env.PI_API_ROOTS ? JSON.parse(process.env.PI_API_ROOTS) : {};

/** The 4.0 mirror: optional label/parameters/display and a handler that may return nothing. */
function loosened(text) {
  const edits = [
    ["  label: string;", "  label?: string;"],
    ["  parameters: Record<string, unknown>;", "  parameters?: unknown;"],
    ["handler: (args: string, ctx: ExtensionContext) => Promise<void>;", "handler: (args: string, ctx: ExtensionContext) => Promise<void> | void;"],
    ["content: string; display: boolean }", "content: string; display?: boolean }"],
  ];
  return edits.reduce((out, [from, to]) => {
    assert.ok(out.includes(from), `the mirror no longer contains ${JSON.stringify(from)}; update the negative control`);
    return out.replace(from, to);
  }, text);
}

test("the throwaway project asserts Pi's ExtensionAPI is assignable to the mirror's, through the package's own index.d.ts", () => {
  const files = projectFiles({ piPackageRoot: "/x/pi", mirror: "/m/pi-types.ts", typeRoot: "/t" });
  assert.deepEqual(Object.keys(files).sort(), ["check.ts", "tsconfig.json"]);
  assert.match(files["check.ts"], /export const _: Ours = piApi;/);
  assert.deepEqual(JSON.parse(files["tsconfig.json"]).compilerOptions.paths[PI_PACKAGE], ["/x/pi/dist/index.d.ts"]);
  assert.deepEqual(PI_API_VERSIONS, ["0.84.1", "0.87.1", "1.0.0"]);
});

test("the mirror is assignable from Pi's ExtensionAPI in every supplied Pi version, and the loosened mirror is not", (t) => {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "pi-api-mirror-"));
  t.after(() => rmSync(scratch, { recursive: true, force: true }));
  const loose = path.join(scratch, "pi-types.ts");
  writeFileSync(loose, loosened(readFileSync(MIRROR, "utf8")));
  const checked = [];
  for (const version of PI_API_VERSIONS) {
    if (roots[version] === undefined) continue;
    const current = typecheckAgainst({ piPackageRoot: roots[version] });
    assert.equal(current.ok, true, `${version}: ${current.output}`);
    const old = typecheckAgainst({ piPackageRoot: roots[version], mirror: loose });
    assert.equal(old.ok, false, `${version}: the loosened mirror was accepted`);
    checked.push(version);
  }
  t.diagnostic(`checked against supplied Pi declarations: ${checked.length ? checked.join(", ") : "(none supplied — make empirica-pi-api-typecheck fetches them)"}`);
});
