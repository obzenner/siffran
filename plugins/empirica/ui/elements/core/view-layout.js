// Where everything in the view goes (VIEW.md R1–R3). Pure, no DOM; tested in tests/view-layout.test.mjs.
// Units are abstract (a claim is `U.claim` across); the view's SVG scales the whole picture to fit its box, so
// with many things everything gets smaller and nothing scrolls (R1).
// - A run is its head (the run mark, its count and its audit mark) with its claim tree under it, laid out by the
//   layered layout (core/layout.js): the root on top, supporting claims below it.
// - A project is a labelled region; its runs flow left to right in the data's order (newest first), wrapping.
// - Projects flow the same way. Of the widths it tries for the rows, the layout keeps the one that lets the
//   picture be largest in the given box.
import { layout } from "./layout.js";

export const U = { claim: 12, mark: 12, claimGap: 6, layerGap: 14, head: 16, headW: 58, headGap: 6, runGap: 20, pad: 10, label: 16, projectGap: 20, char: 6, text: 9, labelText: 10 };

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

/** `labelChars`: the length of the project's label, so the region is never narrower than its label. */
function projectBox(p, labelChars) {
  const blocks = p.runs.map(runBlock);
  const area = blocks.reduce((s, b) => s + (b.w + U.runGap) * (b.h + U.runGap), 0);
  const inner = rows(blocks, Math.max(...blocks.map((b) => b.w), Math.sqrt(area) * 1.6), U.runGap);
  return { id: p.id, n: p.n, blocks, inner, w: Math.max(inner.w, labelChars * U.char) + 2 * U.pad, h: U.label + inner.h + 2 * U.pad };
}

/**
 * @param {{id:string,n:number,runs:{key:string,previous:string|null,root:string|null,claims:{id:string}[],edges:{from:string,to:string}[]}[]}[]} projects
 * @param {{width:number,height:number}} box - the space the picture must fit (any unit; only its proportions count)
 * @param {(p:object) => number} labelChars - the length of a project's label, in characters
 */
export function viewLayout(projects, box, labelChars = () => 0) {
  const boxes = projects.map((p) => projectBox(p, labelChars(p)));
  if (!boxes.length) return { width: 0, height: 0, projects: [], runs: [], claims: [], supports: [], attempts: [] };
  const widest = Math.max(...boxes.map((b) => b.w)), total = boxes.reduce((s, b) => s + b.w + U.projectGap, 0);
  let best = null;
  for (let k = 0; k <= 48; k++) {
    const maxW = widest + ((total - widest) * k) / 48, r = rows(boxes, maxW, U.projectGap);
    const scale = Math.min(box.width / r.w, box.height / r.h);
    if (!best || scale > best.scale + 1e-9) best = { ...r, scale };
  }
  const out = { width: best.w, height: best.h, projects: [], runs: [], claims: [], supports: [], attempts: [] };
  const head = new Map();
  boxes.forEach((b, i) => {
    const P = best.pos[i];
    out.projects.push({ id: b.id, n: b.n, x: P.x, y: P.y, w: b.w, h: b.h });
    b.blocks.forEach((blk, j) => {
      const ox = P.x + U.pad + b.inner.pos[j].x, oy = P.y + U.pad + U.label + b.inner.pos[j].y;
      const run = { key: blk.key, project: b.id, x: ox, y: oy, w: blk.w, h: blk.h };
      out.runs.push(run);
      head.set(blk.key, run);
      for (const c of blk.claims) out.claims.push({ run: blk.key, id: c.id, cx: ox + c.cx, cy: oy + c.cy });
      for (const s of blk.supports) out.supports.push({ run: blk.key, supported: s.supported, supporter: s.supporter, points: s.points.map((p) => ({ x: ox + p.x, y: oy + p.y })) });
    });
  });
  // Next attempt: from the previous run's mark to the later run's mark, bowed over the heads.
  for (const p of projects) for (const r of p.runs) {
    const a = head.get(r.previous), b = head.get(r.key);
    if (a && b) out.attempts.push({ from: a.key, to: b.key, a: { x: a.x + U.mark / 2, y: a.y }, b: { x: b.x + U.mark / 2, y: b.y } });
  }
  return out;
}
