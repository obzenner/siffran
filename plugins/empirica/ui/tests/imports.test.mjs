// The guard for scripts/imports.mjs (T3): nothing in the UI is untested by construction. Every source under app/ and
// elements/ is reached by the imports of at least one Vitest test (the same graph `vitest related` walks); every journey
// file is declared in tests/journeys.json, the one list kept by hand, with a page that exists; no helper in tests/ is
// orphaned; and the scan follows every import form the UI uses.
import assert from "node:assert/strict";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "vitest";
import config from "../vitest.config.mjs";
import { UI, changed, heldBy, imports, journeys, pageScripts, reach, sources, uiFiles, unitTests, unreached } from "../scripts/imports.mjs";

test("every source under app/ and elements/ is imported by at least one Vitest test", () => {
  assert.ok(sources().length > 20);
  assert.deepEqual(unreached(), []);
});

test("the unit tests are what Vitest runs: tests/*.test.mjs, and never a journey", () => {
  assert.deepEqual(config.test.include, ["tests/*.test.mjs"]);
  for (const j of journeys()) assert.ok(!unitTests().includes(j.file), j.file);
});

test("every journey file is declared, with its page and targets, and the page holds the app", () => {
  const files = readdirSync(join(UI, "tests")).filter((f) => f.endsWith(".journeys.mjs")).map((f) => `tests/${f}`);
  assert.deepEqual(journeys().map((j) => j.file).sort(), files.sort());
  for (const j of journeys()) {
    assert.ok(existsSync(join(UI, j.file)) && existsSync(join(UI, j.page)), j.file);
    assert.ok(j.make.length > 0, j.file);
    assert.deepEqual(pageScripts(j.page), ["app/app.js"]);
    assert.ok(heldBy(j).has("elements/organisms/ev-universe.js"), "the page reaches every element through elements/index.js");
  }
});

test("no helper in tests/ is orphaned: each is imported by a unit test or a journey, or is Vitest's setup", () => {
  const helpers = readdirSync(join(UI, "tests")).filter((f) => f.endsWith(".mjs") && !f.endsWith(".test.mjs") && !f.endsWith(".journeys.mjs")).map((f) => `tests/${f}`);
  const used = reach([...unitTests(), ...journeys().map((j) => j.file), ...config.test.setupFiles]);
  assert.deepEqual(helpers.filter((h) => !used.has(h)), []);
});

test("the scan follows every import form, across lines, and leaves packages out (negative controls)", () => {
  const files = {
    "tests/a.test.mjs": `import { x,\n  y } from "../app/one.js";\nimport "../app/side.js";\nconst z = await import("../app/late.js");\nimport fc from "fast-check";\n// import "../app/commented.js";`,
    "app/one.js": `export { q } from "./two.js";\nexport * from "./three.js";`,
    "app/two.js": "", "app/three.js": "", "app/side.js": "", "app/late.js": "", "app/lonely.js": "", "app/commented.js": "",
  };
  const read = (f) => files[f];
  assert.deepEqual(imports("tests/a.test.mjs", read), ["app/one.js", "app/side.js", "app/late.js"]);
  assert.deepEqual(unreached(Object.keys(files).filter((f) => f.startsWith("app/")), ["tests/a.test.mjs"], read), ["app/lonely.js", "app/commented.js"]);
  assert.deepEqual([...reach(["tests/a.test.mjs"], read)].sort(), ["app/late.js", "app/one.js", "app/side.js", "app/three.js", "app/two.js", "tests/a.test.mjs"]);
});

test("make ui-test's files: repository or UI paths, the worktree's changes by default, never a built or installed file", () => {
  assert.deepEqual(uiFiles(["plugins/empirica/ui/app/geometry.js", "app/list.js", "node_modules/x/y.js", "dist/ui.css", "reports/stryker-incremental.json", "app/list.js"]), ["app/geometry.js", "app/list.js"]);
  const git = (...a) => (a[0] === "diff" ? "app/geometry.js\nUI.md\n" : "tests/new.test.mjs\n");
  assert.deepEqual(changed(git), ["app/geometry.js", "UI.md", "tests/new.test.mjs"]);
});
