// The production load path. Inside Pi (bundled / embedded-modules mode) every extension module is
// evaluated by jiti with the host's own packages served as virtual modules and native resolution
// disabled; the owner's preflight is `.ts` under `node_modules/`, which native Node refuses to load.
// The other owner tests run under native Node, so this one loads runtime-owner.ts through the repo's
// jiti exactly as Pi's extension loader does (Pi 0.87.1 `dist/core/extensions/loader.js:405-414`:
// `virtualModules`, `tryNative: false`, `moduleCache: false`) and binds a fixture owner whose
// preflight imports the host peer `@earendil-works/pi-coding-agent`.

import { test } from "node:test";
import assert from "node:assert/strict";
import { realpathSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { createJiti } from "jiti/static";

import type * as RuntimeOwner from "../src/runtime-owner.ts";
import { makeSubagentsPackage, tempParent } from "./owner-fixture.ts";

const RUNTIME_OWNER = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "runtime-owner.ts");
const PEER = "@earendil-works/pi-coding-agent";

test("the owner-anchored preflight binds through Pi's jiti loader, where native Node cannot load it", async () => {
  // <tmp>/node_modules/pi-subagents: a `.ts` preflight under node_modules that imports a host peer.
  const pkg = makeSubagentsPackage(path.join(tempParent(), "node_modules"), {
    preflight: `import { PEER_MARK } from ${JSON.stringify(PEER)};\n`
      + "export async function resolveSubagentLaunchContract() {\n"
      + "  return { ok: false, message: `peer:${PEER_MARK}` };\n}\n",
  });
  const preflight = path.join(pkg.root, "src", "api", "preflight.ts");

  // Control: native import() of the same file fails, so a pass below proves the jiti path was taken.
  await assert.rejects(import(pathToFileURL(preflight).href), (error: NodeJS.ErrnoException) => {
    assert.ok(["ERR_UNSUPPORTED_NODE_MODULES_TYPE_STRIPPING", "ERR_MODULE_NOT_FOUND"].includes(String(error.code)),
      `unexpected native failure ${String(error.code)}: ${error.message}`);
    return true;
  });

  const jiti = createJiti(import.meta.url, {
    moduleCache: false, fsCache: false, tryNative: false,
    virtualModules: { [PEER]: { PEER_MARK: "virtual-peer" } },
  });
  const loaded = await jiti.import<typeof RuntimeOwner>(RUNTIME_OWNER);
  const { nodeOwnerIo, nodePreflightImporter, resolveOwnerPreflight, resolveSubagentOwner } = loaded;

  const owner = resolveSubagentOwner([pkg.tool()], [], nodeOwnerIo, {});
  assert.ok(owner.ok, JSON.stringify(owner));
  const bound = await resolveOwnerPreflight(owner, nodePreflightImporter, nodeOwnerIo);
  assert.ok(bound.ok, JSON.stringify(bound));
  assert.equal(typeof bound.api.resolveSubagentLaunchContract, "function");
  assert.equal(bound.preflight_path, realpathSync(preflight));
  // The peer was served by the virtual module, not resolved from disk.
  assert.deepEqual(await bound.api.resolveSubagentLaunchContract({}), { ok: false, message: "peer:virtual-peer" });
});
