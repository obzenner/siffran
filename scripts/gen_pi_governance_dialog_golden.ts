#!/usr/bin/env node
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { initialState, renderLines } from "../plugins/empirica/adapters/pi/src/dialog-view.ts";
import type { Dialog, DialogState } from "../plugins/empirica/adapters/pi/src/dialog-view.ts";

const root = resolve(import.meta.dirname, "..");
const fixture = JSON.parse(readFileSync(resolve(root, "plugins/empirica/tests/fixtures/governance-dialog-golden.json"), "utf8")) as {
  dialogs: { review: Dialog; confirmation: Dialog; hostile: Dialog; hostile_rationale: Dialog };
  review: { message: string };
};
const out = resolve(root, "plugins/empirica/adapters/pi/test/dialog-view-golden");
const theme = { accent: (text: string) => text, muted: (text: string) => text,
  warning: (text: string) => text };
const check = process.argv.includes("--check");

/** The canonical bound document (ADR-0072 Q4): the short digest line and the exempt absolute
 * path, plus the core-owned priors and the core-derived `question` (M1, review 2). Derived
 * from the shared golden's review message (M3, review 2) so the path the Pi goldens wrap is
 * exactly the *real* sidecar layout the round-08 Claude fixture pins —
 * `<home>/projects/s256-<64 hex>/runs/s256-<64 hex>/gen-<n>/hitl/<64 hex>.json`, ~274
 * characters, never again a made-up short one: if the Python generator re-renders its home,
 * the Pi goldens follow at the next regeneration and the check mode catches the drift. */
const reviewMessage = fixture.review.message;
const digestAt = reviewMessage.split("\n").findIndex(line => line.startsWith("Document: "));
if (digestAt < 0) throw new Error("the fixture's review message names no document");
const REAL_PATH = reviewMessage.split("\n")[digestAt + 1] as string;
if (REAL_PATH.length <= 250)
  throw new Error(`the fixture path must be the real layout, got ${REAL_PATH.length} characters`);
const HEX_OF_PATH = (REAL_PATH.split("/").pop() ?? "").replace(/\.json$/, "");
const DOCUMENT = { digest: `sha256:${HEX_OF_PATH}`, path: REAL_PATH,
  priors: { max_passes: 8, max_spawns: 1, max_audit_spawns: 2 },
  question: "approve" } as const;

function screen(dialog: Dialog, state: DialogState, width: number,
                confirmation = false): string {
  return renderLines(dialog, state, width, theme,
    { confirmation, timeoutMs: 900_000, document: DOCUMENT }).join("\n") + "\n";
}

const invalid = initialState(fixture.dialogs.review);
invalid.values.max_passes = 0;
const cases: Record<string, (width: number) => string> = {
  review: width => screen(fixture.dialogs.review, initialState(fixture.dialogs.review), width),
  confirmation: width => screen(fixture.dialogs.confirmation,
    initialState(fixture.dialogs.confirmation, true), width, true),
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
