// The Pi adapter must run from an installed copy of plugins/empirica/ alone. The marketplace root
// (its contracts/ SSOT, its node_modules) is not part of that copy, so every document the adapter
// reads has to resolve inside the plugin directory — the vendored contract copy.

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, symlinkSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { DEFAULT_SKILLS_DIR } from "../src/index.ts";
import { DEFAULT_BRIDGE_SCRIPT } from "../src/stdio-transport.ts";
import { PRIVATE_BRIDGE_SCRIPT } from "../src/private-transport.ts";
import { VENDORED_CONTRACT_DIR } from "../src/public-tools.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = path.resolve(HERE, "..", "..", "..");
const MARKETPLACE_ROOT = path.resolve(PLUGIN_ROOT, "..", "..");
const CONTRACT_FILES = ["public-contract.json", "public-tools.json", "response.schema.json"];

test("every path the adapter resolves at load stays inside the plugin directory", () => {
  const resolved = [
    DEFAULT_SKILLS_DIR, DEFAULT_BRIDGE_SCRIPT, PRIVATE_BRIDGE_SCRIPT, VENDORED_CONTRACT_DIR,
    ...CONTRACT_FILES.map((file) => path.join(VENDORED_CONTRACT_DIR, file)),
  ];
  for (const target of resolved) {
    const relative = path.relative(PLUGIN_ROOT, target);
    assert.ok(!relative.startsWith("..") && !path.isAbsolute(relative),
      `${target} escapes the plugin directory ${PLUGIN_ROOT}`);
    assert.ok(existsSync(target), `${target} must exist`);
  }
});

test("the adapter loads and registers its tools from a copy of plugins/empirica alone", (t) => {
  const root = mkdtempSync(path.join(os.tmpdir(), "empirica-installed-layout-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const installed = path.join(root, "plugins", "empirica");
  cpSync(PLUGIN_ROOT, installed, {
    recursive: true,
    filter: (source) => !["node_modules", "__pycache__", ".pytest_cache"].includes(path.basename(source)),
  });
  // Third-party packages (pi-tui) are resolved from the host's node_modules; the marketplace
  // contracts/ directory is deliberately NOT provided. pi-subagents is deliberately NOT provided
  // either: it is an external runtime (the repo's copy is a devDependency fixture), so the adapter
  // must load, and bind the owner's preflight, without it.
  const hostModules = path.join(root, "node_modules");
  mkdirSync(hostModules);
  for (const entry of readdirSync(path.join(MARKETPLACE_ROOT, "node_modules")))
    if (entry !== "pi-subagents")
      symlinkSync(path.join(MARKETPLACE_ROOT, "node_modules", entry), path.join(hostModules, entry));
  assert.equal(existsSync(path.join(hostModules, "pi-subagents")), false);
  assert.equal(existsSync(path.join(root, "contracts")), false);
  assert.equal(existsSync(path.join(installed, "..", "..", "contracts")), false);

  const probe = `
    const { createEmpiricaExtension } = await import(${JSON.stringify(path.join(installed, "adapters/pi/src/index.ts"))});
    const { nodePreflightImporter } = await import(${JSON.stringify(path.join(installed, "adapters/pi/src/runtime-owner.ts"))});
    const { FakePi } = await import(${JSON.stringify(path.join(installed, "adapters/pi/test/fakes.ts"))});
    const { defaultSubagentsPackage } = await import(${JSON.stringify(path.join(installed, "adapters/pi/test/owner-fixture.ts"))});
    const { realpathSync } = await import("node:fs");
    const loaded = [];
    const pi = new FakePi();
    createEmpiricaExtension({ ownerEnv: {}, dispatch: () => { throw new Error("no dispatch expected"); },
      preflightImporter: { resolve: nodePreflightImporter.resolve,
        load: (file) => { loaded.push(file); return nodePreflightImporter.load(file); } } })(pi);
    await pi.sessionStart();
    console.log(JSON.stringify({ tools: [...pi.tools.keys()].sort(), loaded,
      owner: realpathSync(defaultSubagentsPackage().root) }));
  `;
  const result = spawnSync(process.execPath,
    ["--experimental-strip-types", "--input-type=module", "-e", probe],
    { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const lines = result.stdout.trim().split("\n");
  const seen = JSON.parse(lines[lines.length - 1]) as { tools: string[]; loaded: string[]; owner: string };
  assert.deepEqual(seen.tools, ["empirica_observe", "empirica_read", "report_convergence"]);
  assert.equal(seen.loaded.length, 1, "exactly the owner's preflight is loaded at session_start");
  assert.ok(seen.loaded[0].startsWith(path.join(seen.owner, path.sep)),
    `${seen.loaded[0]} must come from the registered owner ${seen.owner}, not the host's node_modules`);
});
