// The adapter's mirrored Pi API (src/pi-types.ts) against Pi's own declarations. `make
// empirica-pi-api-typecheck` fetches each reviewed Pi version and runs the same check; here the
// package roots can be supplied as PI_API_ROOTS='{"1.0.0":"<pi package dir>",...}' (unsupplied
// versions are reported, not skipped silently). The looser mirror of 4.0 is kept as a negative
// control: it must fail against every supplied Pi version.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { MIRROR, MIRRORS, PI_API_VERSIONS, PI_PACKAGE, projectFiles, typecheckAgainst } from "../pi_api_typecheck.mjs";

const roots = process.env.PI_API_ROOTS ? JSON.parse(process.env.PI_API_ROOTS) : {};

/** The 4.0 Empirica mirror: optional label/parameters/display and a handler that may return nothing. */
const LOOSENED_EDITS = {
  empirica: [
    ["  label: string;", "  label?: string;"],
    ["  parameters: Record<string, unknown>;", "  parameters?: unknown;"],
    ["handler: (args: string, ctx: ExtensionContext) => Promise<void>;", "handler: (args: string, ctx: ExtensionContext) => Promise<void> | void;"],
    ["content: string; display: boolean }", "content: string; display?: boolean }"],
  ],
  // The Methodologist mirror shipped with the handler return type Pi does not accept: that is its control.
  methodologist: [
    ["handler: (args: string, ctx: ExtensionContext) => Promise<void>;", "handler: (args: string, ctx: ExtensionContext) => Promise<void> | void;"],
  ],
};
function loosened(text, edits) {
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
  assert.deepEqual(PI_API_VERSIONS, ["1.0.0", "1.1.0"]);
  assert.deepEqual(Object.keys(MIRRORS), ["empirica", "methodologist"], "both Pi adapters are proven against Pi's declarations");
  assert.equal(MIRRORS.empirica, MIRROR);
});

test("each mirror is assignable from Pi's ExtensionAPI in every supplied Pi version, and its loosened form is not", (t) => {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "pi-api-mirror-"));
  t.after(() => rmSync(scratch, { recursive: true, force: true }));
  const checked = [];
  for (const [name, mirror] of Object.entries(MIRRORS)) {
    const loose = path.join(scratch, `${name}-pi-types.ts`);
    writeFileSync(loose, loosened(readFileSync(mirror, "utf8"), LOOSENED_EDITS[name]));
    for (const version of PI_API_VERSIONS) {
      if (roots[version] === undefined) continue;
      const current = typecheckAgainst({ piPackageRoot: roots[version], mirror });
      assert.equal(current.ok, true, `${name} ${version}: ${current.output}`);
      const old = typecheckAgainst({ piPackageRoot: roots[version], mirror: loose });
      assert.equal(old.ok, false, `${name} ${version}: the loosened mirror was accepted`);
      checked.push(`${name}@${version}`);
    }
  }
  t.diagnostic(`checked against supplied Pi declarations: ${checked.length ? checked.join(", ") : "(none supplied — make empirica-pi-api-typecheck fetches them)"}`);
});
