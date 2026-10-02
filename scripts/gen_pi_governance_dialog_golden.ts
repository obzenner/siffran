#!/usr/bin/env node
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { initialState, renderLines } from "../plugins/empirica/adapters/pi/src/dialog-view.ts";
import type { Dialog, DialogState } from "../plugins/empirica/adapters/pi/src/dialog-view.ts";

const root = resolve(import.meta.dirname, "..");
const fixture = JSON.parse(readFileSync(resolve(root, "plugins/empirica/tests/fixtures/governance-dialog-golden.json"), "utf8")) as {
  dialogs: { review: Dialog; confirmation: Dialog; hostile: Dialog; hostile_rationale: Dialog };
};
const out = resolve(root, "plugins/empirica/adapters/pi/test/dialog-view-golden");
const theme = { accent: (text: string) => text, muted: (text: string) => text,
  warning: (text: string) => text };
const check = process.argv.includes("--check");

function screen(dialog: Dialog, state: DialogState, width: number,
                confirmation = false, before?: Dialog): string {
  return renderLines(dialog, state, width, theme,
    { confirmation, before, timeoutMs: 900_000 }).join("\n") + "\n";
}

const invalid = initialState(fixture.dialogs.review);
invalid.values.max_passes = 0;
const cases: Record<string, (width: number) => string> = {
  review: width => screen(fixture.dialogs.review, initialState(fixture.dialogs.review), width),
  confirmation: width => screen(fixture.dialogs.confirmation,
    initialState(fixture.dialogs.confirmation, true), width, true, fixture.dialogs.review),
  hostile: width => screen(fixture.dialogs.hostile, initialState(fixture.dialogs.hostile), width),
  "hostile-rationale": width => screen(fixture.dialogs.hostile_rationale,
    initialState(fixture.dialogs.hostile_rationale), width),
  "range-error": width => screen(fixture.dialogs.review, invalid, width),
};

mkdirSync(out, { recursive: true });
let stale = false;
for (const [name, render] of Object.entries(cases)) {
  for (const width of [80, 50]) {
    const path = resolve(out, `${name}-${width}.txt`);
    const expected = render(width);
    if (check) {
      try { stale ||= readFileSync(path, "utf8") !== expected; }
      catch { stale = true; }
    } else writeFileSync(path, expected);
  }
}
if (stale) {
  console.error(`stale Pi governance dialog goldens: ${out}`);
  process.exit(1);
}
