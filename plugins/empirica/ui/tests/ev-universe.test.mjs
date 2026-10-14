// elements/organisms/ev-universe.js: what its markup promises, read from its source (it is a custom element; its drawing is
// held by the browser journeys). A callout is wide enough for every ask in full, and its host is the part that gives way.
import assert from "node:assert/strict";
import { test } from "vitest";
import { readFileSync } from "node:fs";
import catalogue from "../catalogue.json" with { type: "json" };
import copy from "../copy/en.json" with { type: "json" };

const G = catalogue.universe;

test("a callout is wide enough for every ask in full, its host the part that gives way", () => {
  const asks = Object.entries(copy).filter(([k]) => /^ask\.[a-z_]+$/.test(k)).map(([, v]) => v);
  const host = Math.min(...["host.claude", "host.pi"].map((k) => copy[k].length));
  const need = Math.max(...asks.map((a) => a.length)) * G.label_char_px + 2 * G.glyph_max_px + G.label_pad_px + host * G.label_char_px + G.callout_gap;
  assert.ok(G.callout_w >= need, `callout_w ${G.callout_w} < ${need}`);
  const src = readFileSync(new URL("../elements/organisms/ev-universe.js", import.meta.url), "utf8");
  const callout = src.slice(src.indexOf("data-callout="), src.indexOf("</a>`);", src.indexOf("data-callout=")));
  assert.match(callout, /<span class="shrink-0 font-semibold">\$\{t\(`ask\./, "the ask never truncates");
  assert.match(callout, /<span class="min-w-0 truncate text-muted-foreground">\$\{hostName/, "the host does");
});

