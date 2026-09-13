import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { renderText, preserved, sameContract, type ContractView } from "../src/obligations.ts";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../..");
const dir = path.join(root, "contracts/obligations/v1/fixtures");
for (const file of readdirSync(dir).filter(f => f.endsWith(".json"))) {
  const fixture = JSON.parse(readFileSync(path.join(dir, file), "utf8"));
  if (fixture.expect?.view && fixture.expect?.text !== undefined) test(`${file}: renderText`, () => assert.equal(renderText(fixture.expect.view as ContractView), fixture.expect.text));
  if (fixture.before && fixture.after && fixture.expect?.ok !== undefined) test(`${file}: ${fixture.comparison ?? "preserved"}`, () => assert.deepEqual(
    fixture.comparison === "same_contract" ? sameContract(fixture.before, fixture.after) : preserved(fixture.before, fixture.after), fixture.expect));
}
