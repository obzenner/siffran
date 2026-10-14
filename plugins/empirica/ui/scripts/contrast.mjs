// T7: contrast is a test. Reads the resolved tokens via Style Dictionary, judges with culori's WCAG contrast.
import StyleDictionary from "style-dictionary";
import { wcagContrast } from "culori";
import catalogue from "../catalogue.json" with { type: "json" };

const sd = new StyleDictionary({ source: ["tokens/tokens.json"], usesDtcg: true, log: { verbosity: "silent" }, platforms: { css: {} } });
const dict = await sd.exportPlatform("css");
const get = (scheme, key) => {
  const node = dict[scheme]?.[key];
  if (!node) throw new Error(`missing ${scheme}.${key}`);
  return node.$value;
};

const TEXT = 4.5, UI = 3;
const surfaces = ["background", "card", "muted", "sidebar"];
const tones = catalogue.tones;
const rows = [];
for (const s of ["light", "dark"]) {
  for (const bg of surfaces) {
    rows.push([s, "foreground", bg, TEXT]);
    rows.push([s, "muted-foreground", bg, TEXT]);
    for (const t of tones) rows.push([s, `tone-${t}`, bg, TEXT]);
    rows.push([s, "ring", bg, UI]);
  }
  for (const t of tones) rows.push([s, `tone-${t}`, `tone-${t}-tint`, TEXT]);
  rows.push([s, "primary-foreground", "primary", TEXT]);
  // Graphical marks (T7, WCAG 1.4.11): the view's run diamonds and claim circles are outlined in `subtle`, so the
  // shape of every mark reads at >= 3:1 whatever its tint; lines and the gating outline are `muted-foreground`.
  for (const bg of ["background", "card"]) rows.push([s, "subtle", bg, UI]);
  // Each island's name in the Universe is text in its own hue on the page's background.
  for (let k = 1; k <= catalogue.universe.island_hues; k++) rows.push([s, `island-${k}`, "background", TEXT]);
}
let failed = 0;
for (const [s, fg, bg, min] of rows) {
  const r = wcagContrast(get(s, fg), get(s, bg));
  const ok = r >= min;
  if (!ok) failed++;
  if (!ok || process.argv.includes("-v")) console.log(`${ok ? "ok  " : "FAIL"} ${s.padEnd(5)} ${fg.padEnd(20)} on ${bg.padEnd(20)} ${r.toFixed(2)} (min ${min})`);
}
console.log(`contrast: ${rows.length} pairs, ${failed} failed`);
process.exit(failed ? 1 : 0);
