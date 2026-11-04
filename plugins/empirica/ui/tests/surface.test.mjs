// The run sheet's own surface and the quiet Copy's hiding, declared once (PR 17 §4, review 17-1). These pin the
// declarations the two follow-ups named: the sheet pane sits on the semantic `sheet` token - one step below the page
// in light (neutral.100 #f5f5f5), the page's own floor with the cards elevated above it in dark (neutral.950) - so the
// section cards (bg-card) stand out from the sheet in both schemes, and `identifier` chips' pair holds on it because
// the contrast check names `sheet` among its surfaces; and a quiet Copy, until hover or focus, takes no space at all
// (out of flow, untouchable, invisible) so an audit finding's sentence reads as a sentence, no blank gap after the
// digest chip and no line-height inflation, while the tab key still reveals it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";
import { fileURLToPath } from "node:url";

const read = (u) => readFileSync(new URL(u, import.meta.url), "utf8");
const tokens = JSON.parse(read("../tokens/tokens.json"));

test("the sheet pane sits on the semantic sheet token, named for its place", () => {
  const page = read("../app/index.html");
  const pane = /id="sheet-pane" class="([^"]*)"/.exec(page);
  assert.ok(pane, "the sheet pane exists in the page");
  assert.match(pane[1], /(^|\s)bg-sheet(\s|$)/, "the pane's class says bg-sheet, the sheet's own semantic token");
  assert.doesNotMatch(pane[1], /bg-sidebar|bg-background/, "not the sidebar's or the page's surface");
});

test("sheet is one step below the page in light, the page's floor with cards above it in dark", () => {
  assert.equal(tokens.light.sheet.$value, "{primitive.neutral.100}", "light: neutral.100 (#f5f5f5), the coordinator's review 17-1 value");
  assert.equal(tokens.dark.sheet.$value, "{primitive.neutral.950}", "dark: neutral.950, so the bg-card sections (#171717) separate from it by fill");
});

test("the contrast check judges every pair on the sheet surface too", () => {
  const surfaces = /const surfaces = \[([^\]]*)\]/.exec(read("../scripts/contrast.mjs"));
  assert.ok(surfaces, "the contrast script names its surfaces");
  assert.match(surfaces[1], /"sheet"/, "sheet is among the judged surfaces");
});

test("a quiet Copy, hidden, takes no space: out of flow, untouchable, invisible", () => {
  const css = read("../styles/app.css");
  const hidden = /\[data-copy-quiet\]\s+\[data-copy\]\s*\{([^}]*)\}/.exec(css);
  assert.ok(hidden, "the hidden quiet Copy has a rule of its own");
  assert.match(hidden[1], /position:\s*absolute/, "out of the flow, so no gap and no line-height inflation");
  assert.match(hidden[1], /pointer-events:\s*none/, "an invisible button may not eat a click");
  assert.match(hidden[1], /opacity:\s*0/, "invisible");
  const reveal = /\[data-copy-quiet\]:hover\s+\[data-copy\],\s*\[data-copy-quiet\]:focus-within\s+\[data-copy\]\s*\{([^}]*)\}/.exec(css);
  assert.ok(reveal, "hover and focus-within share the reveal rule");
  assert.match(reveal[1], /position:\s*static/, "revealed, it takes its place in the sentence again");
  assert.match(reveal[1], /pointer-events:\s*auto/, "and can be clicked");
  assert.match(reveal[1], /opacity:\s*1/, "seen");
});
