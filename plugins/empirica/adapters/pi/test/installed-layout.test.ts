// The Pi adapter must run from an installed copy of plugins/empirica/ alone. The marketplace root
// (its contracts/ SSOT, its node_modules) is not part of that copy, so every document the adapter
// reads has to resolve inside the plugin directory — the vendored contract copy.

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
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
  // contracts/ directory is deliberately NOT provided.
  symlinkSync(path.join(MARKETPLACE_ROOT, "node_modules"), path.join(root, "node_modules"), "dir");
  assert.equal(existsSync(path.join(root, "contracts")), false);
  assert.equal(existsSync(path.join(installed, "..", "..", "contracts")), false);

  const probe = `
    const { default: extension } = await import(${JSON.stringify(path.join(installed, "adapters/pi/src/index.ts"))});
    const { FakePi } = await import(${JSON.stringify(path.join(installed, "adapters/pi/test/fakes.ts"))});
    const pi = new FakePi();
    extension(pi);
    console.log(JSON.stringify([...pi.tools.keys()].sort()));
  `;
  const result = spawnSync(process.execPath,
    ["--experimental-strip-types", "--input-type=module", "-e", probe],
    { cwd: root, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const lines = result.stdout.trim().split("\n");
  assert.deepEqual(JSON.parse(lines[lines.length - 1]),
    ["empirica_observe", "empirica_read", "report_convergence"]);
});
