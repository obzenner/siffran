#!/usr/bin/env node
// Typecheck the adapter's mirrored Pi API (plugins/empirica/adapters/pi/src/pi-types.ts) against the
// real Pi declarations of each reviewed Pi version: `const _: Ours = piApi`. The mirror is a hand-kept
// subset of Pi's `ExtensionAPI`; node's type stripping never checks it, so this is the only thing that
// notices when the mirror promises something Pi does not provide (a looser label/handler/display).
//
// Pi is never installed into the repository: each version's package is fetched with `npm pack` into a
// scratch directory (or supplied with --roots), and `tsc --noEmit` runs against a throwaway tsconfig.
//
// Usage: node scripts/pi_api_typecheck.mjs [--roots '{"0.84.1":"<pi package dir>",...}']
//   versions without a supplied root are fetched from the registry. Exit 1 on any mismatch.
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { packPackage } from "./lib/npm_fetch.mjs";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const PI_PACKAGE = "@earendil-works/pi-coding-agent";
/** The Pi versions whose declarations the mirror is proven against: both ends of the contract interval and the version in between. */
export const PI_API_VERSIONS = ["0.84.1", "0.87.1", "1.0.0"];
export const MIRROR = path.join(repo, "plugins", "empirica", "adapters", "pi", "src", "pi-types.ts");

/** The files of a throwaway project asserting `ExtensionAPI` (Pi's) is assignable to the mirror's. */
export function projectFiles({ piPackageRoot, mirror = MIRROR, typeRoot = path.join(repo, "node_modules", "@types") }) {
  const tsconfig = {
    compilerOptions: {
      target: "ES2022", module: "NodeNext", moduleResolution: "NodeNext", strict: true, noEmit: true,
      skipLibCheck: true, allowImportingTsExtensions: true, types: [], typeRoots: [typeRoot], baseUrl: ".",
      paths: { [PI_PACKAGE]: [path.join(piPackageRoot, "dist", "index.d.ts")] },
    },
    files: ["check.ts"],
  };
  const check = [
    `import type { ExtensionAPI as PiApi } from ${JSON.stringify(PI_PACKAGE)};`,
    `import type { ExtensionAPI as Ours } from ${JSON.stringify(mirror)};`,
    "declare const piApi: PiApi;",
    "export const _: Ours = piApi;", ""].join("\n");
  return { "tsconfig.json": JSON.stringify(tsconfig, null, 2), "check.ts": check };
}

/** `{ ok, output }` of `tsc --noEmit` over the throwaway project; `mirror` names the file under test. */
export function typecheckAgainst({ piPackageRoot, mirror = MIRROR, tsc = path.join(repo, "node_modules", ".bin", "tsc"), run = spawnSync }) {
  const scratch = mkdtempSync(path.join(os.tmpdir(), "pi-api-typecheck-"));
  try {
    for (const [name, text] of Object.entries(projectFiles({ piPackageRoot, mirror }))) writeFileSync(path.join(scratch, name), text);
    const result = run(tsc, ["-p", path.join(scratch, "tsconfig.json")], { encoding: "utf8", timeout: 120_000 });
    if (result.error) return { ok: false, output: String(result.error.message) };
    return { ok: result.status === 0, output: `${result.stdout ?? ""}${result.stderr ?? ""}`.trim() };
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

async function main(argv) {
  let roots = {};
  if (argv[0] === "--roots" && argv[1] !== undefined) roots = JSON.parse(argv[1]);
  else if (argv.length > 0) throw new Error(`unknown argument: ${argv[0]}`);
  const cache = path.join(process.env.TMPDIR ?? "/tmp", "empirica-pi-api", "npm-cache");
  mkdirSync(cache, { recursive: true });
  let failed = false;
  for (const version of PI_API_VERSIONS) {
    let result;
    try {
      const root = roots[version] ?? packPackage({ name: PI_PACKAGE, version,
        dest: path.join(process.env.TMPDIR ?? "/tmp", "empirica-pi-api", version), cache });
      result = typecheckAgainst({ piPackageRoot: root });
    } catch (error) {
      result = { ok: false, output: String(error.message ?? error) };
    }
    if (!result.ok) failed = true;
    console.log(JSON.stringify({ pi: version, pass: result.ok, ...(result.ok ? {} : { output: result.output.split("\n").slice(0, 30).join("\n") }) }));
  }
  return failed ? 1 : 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (error) => {
    console.error(String(error.message ?? error)); process.exitCode = 2;
  });
}
