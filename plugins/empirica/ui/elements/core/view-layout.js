// Where everything in the view goes (VIEW.md R1–R3). Pure, no DOM; tested in tests/view-layout.test.mjs.
// Units are abstract (a claim is `U.claim` across); the view's SVG scales the whole picture to fit its box, so
// with many things everything gets smaller and nothing scrolls (R1).
// - A run is its head (the run mark, its count and its audit mark) with its claim tree under it, laid out by the
//   layered layout (core/layout.js): the root on top, supporting claims below it.
// - A project is a labelled region; its runs flow left to right in the data's order (newest first), wrapping.
// - Projects flow the same way. Of the widths it tries for the rows, the layout keeps the one that lets the
//   picture be largest in the given box.
import { layout } from "./layout.js";

export const U = { claim: 12, mark: 12, claimGap: 6, layerGap: 14, head: 16, headW: 58, headGap: 6, runGap: 20, pad: 10, label: 16, projectGap: 20, labelCap: 28, labelLines: 2, char: 6, text: 9, labelText: 10,
  // Named geometry of what the view draws around its marks (not layout: spacing between things is above).
  margin: 4, regionRx: 6, regionStroke: 1, ringPad: 3, ringRx: 3, ringStroke: 1.5, ringExtra: 4, claimRing: 2.5, countGap: 4, baseline: 0.35, sideX: 12, sideY: 5 };

/** A run's block, in its own coordinates (0,0 = top left). */
function runBlock(r) {
  const block = { key: r.key, w: U.headW, h: U.head, claims: [], supports: [] };
  if (!r.claims.length) return block;
  const g = layout(r.claims.map((c) => ({ id: c.id })), r.edges, { w: U.claim, h: U.claim, gapX: U.claimGap, gapY: U.layerGap, root: r.root });
  const ns = Object.values(g.nodes);
  const x0 = Math.min(...ns.map((n) => n.x)), y0 = Math.min(...ns.map((n) => n.y));
  const top = U.head + U.headGap, at = (p) => ({ x: p.x - x0, y: p.y - y0 + top });
  for (const [id, n] of Object.entries(g.nodes)) {
    const p = at(n);
    block.claims.push({ id, cx: p.x + U.claim / 2, cy: p.y + U.claim / 2 });
  }
  // An edge runs from the supported claim (from) to its supporter (to); a support line points the other way.
  for (const e of g.edges) block.supports.push({ supported: e.from, supporter: e.to, points: e.points.map(at).reverse() });
  block.w = Math.max(U.headW, Math.max(...ns.map((n) => n.x + n.w)) - x0);
  block.h = top + Math.max(...ns.map((n) => n.y + n.h)) - y0;
  return block;
}

/** Left-to-right rows no wider than `maxW` (an item wider than that gets a row of its own). */
function rows(items, maxW, gap) {
  const pos = [];
  let x = 0, y = 0, rowH = 0, w = 0;
  for (const it of items) {
    if (x > 0 && x + it.w > maxW) { y += rowH + gap; x = 0; rowH = 0; }
    pos.push({ x, y });
    x += it.w + gap; rowH = Math.max(rowH, it.h); w = Math.max(w, x - gap);
  }
  return { pos, w, h: items.length ? y + rowH : 0 };
}

/**
 * A project's label wrapped to at most `lines` lines of `cap` characters, broken at its " · " separators first (they
 * separate facts), then at spaces; what still does not fit ends in an ellipsis (the full label is in the hint, the
 * panel and the breadcrumb). A label never sets a box wider than `cap` characters.
 */
export function wrapLabel(text, cap = U.labelCap, lines = U.labelLines) {
  const out = [];
  let rest = String(text).split(" · ").flatMap((part, i, all) => (i < all.length - 1 ? [part + " ·"] : [part]));
  let line = "";
  for (const piece of rest) {
    const next = line ? `${line} ${piece}` : piece;
    if (next.length <= cap || !line) line = next;
    else { out.push(line.replace(/ ·$/, "")); line = piece; }
  }
  if (line) out.push(line);
  const shown = out.slice(0, lines);
  if (out.length > lines) shown[lines - 1] = `${shown[lines - 1]} ${out.slice(lines).join(" ")}`;
  return shown.map((s, i) => (s.length > cap ? `${s.slice(0, cap - 1)}…` : s));
}

/** `label`: how the project's label is drawn, `{ chars, lines }` (the widest line, in characters, and how many lines). */
function projectBox(p, label) {
  const { chars, lines } = typeof label === "number" ? { chars: label, lines: 1 } : label;
  const blocks = p.runs.map(runBlock);
  const area = blocks.reduce((s, b) => s + (b.w + U.runGap) * (b.h + U.runGap), 0);
  const inner = rows(blocks, Math.max(...blocks.map((b) => b.w), Math.sqrt(area) * 1.6), U.runGap);
  return { id: p.id, n: p.n, blocks, inner, lines, w: Math.max(inner.w, chars * U.char) + 2 * U.pad, h: lines * U.label + inner.h + 2 * U.pad };
}

/**
 * @param {{id:string,n:number,runs:{key:string,previous:string|null,root:string|null,claims:{id:string}[],edges:{from:string,to:string}[]}[]}[]} projects
 * @param {{width:number,height:number}} box - the space the picture must fit (any unit; only its proportions count)
 * @param {(p:object) => number|{chars:number,lines:number}} labelChars - how wide a project's label is drawn: its widest line in characters (and its number of lines)
 * @param {number} minScale - the smallest scale the picture is drawn at; where even that does not fit, the rows are
 *   kept no wider than the box allows at it, so the picture scrolls one way (down) and not both
 */
export function viewLayout(projects, box, labelChars = () => 0, minScale = 0) {
  const boxes = projects.map((p) => projectBox(p, labelChars(p)));
  if (!boxes.length) return { width: 0, height: 0, projects: [], runs: [], claims: [], supports: [], attempts: [] };
  const widest = Math.max(...boxes.map((b) => b.w)), total = boxes.reduce((s, b) => s + b.w + U.projectGap, 0);
  let best = null;
  for (let k = 0; k <= 48; k++) {
    const maxW = widest + ((total - widest) * k) / 48, r = rows(boxes, maxW, U.projectGap);
    const scale = Math.min(box.width / r.w, box.height / r.h), fits = r.w * minScale <= box.width + 1e-9;
    if (!best || (fits && !best.fits) || (fits === best.fits && scale > best.scale + 1e-9)) best = { ...r, scale, fits };
  }
  const out = { width: best.w, height: best.h, projects: [], runs: [], claims: [], supports: [], attempts: [] };
  const head = new Map();
  boxes.forEach((b, i) => {
    const P = best.pos[i];
    out.projects.push({ id: b.id, n: b.n, lines: b.lines, x: P.x, y: P.y, w: b.w, h: b.h });
    b.blocks.forEach((blk, j) => {
      const ox = P.x + U.pad + b.inner.pos[j].x, oy = P.y + U.pad + b.lines * U.label + b.inner.pos[j].y;
      const run = { key: blk.key, project: b.id, x: ox, y: oy, w: blk.w, h: blk.h };
      out.runs.push(run);
      head.set(blk.key, run);
      for (const c of blk.claims) out.claims.push({ run: blk.key, id: c.id, cx: ox + c.cx, cy: oy + c.cy });
      for (const s of blk.supports) out.supports.push({ run: blk.key, supported: s.supported, supporter: s.supporter, points: s.points.map((p) => ({ x: ox + p.x, y: oy + p.y })) });
    });
  });
  // Next attempt: from the previous run's mark to the later run's mark (the centres of the two diamonds).
  for (const p of projects) for (const r of p.runs) {
    const a = head.get(r.previous), b = head.get(r.key);
    if (a && b) out.attempts.push({ from: a.key, to: b.key, a: { x: a.x + U.mark / 2, y: a.y + U.head / 2 }, b: { x: b.x + U.mark / 2, y: b.y + U.head / 2 } });
  }
  return out;
}

/** The dashed line between two runs' diamonds (centres `a` → `b`), as an SVG cubic path, routed clear of what is
 * around it (Q3b-9): side by side (one row), it bows over the heads but never higher than the gap above them (below
 * the project's label); stacked (one column), it runs down the free margin at the left of the column, from the left
 * vertex of one diamond to the left vertex of the other, so it crosses neither diamond nor the claims under the upper
 * one. `r` is a diamond's half size. */
export function attemptPath(a, b, r) {
  if (Math.abs(a.x - b.x) < U.headW && a.y !== b.y) {
    const x = Math.min(a.x, b.x) - r, out = x - U.pad * 0.8;
    return `M${a.x - r},${a.y} C${out},${a.y} ${out},${b.y} ${b.x - r},${b.y}`;
  }
  const top = Math.min(a.y, b.y) - r, rise = U.pad;
  return `M${a.x},${a.y - r} C${a.x},${top - rise} ${b.x},${top - rise} ${b.x},${b.y - r}`;
}
