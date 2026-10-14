// The Universe's layout and its pure helpers that are not geometry (UI.md, The Universe), with no DOM and no memory. Tested
// in tests/universe.test.mjs; the geometry it composes is app/geometry.js.
//
// The layout is a pure function of one summary, the observer's facts alone, so a reload, a poll or a summary taken while
// the observer is still reading lays out every island and mark it holds the same way:
// - island k (the observer's project order: by its first run's start, so a new project appends) sits in slot k
//   (`islandSlots`: equal slots of the smallest tier that holds the largest island, `slotTier`, packed append-only), drawn as large as its places reach (its
//   project's runs, the observer's count, or its furthest place), never past its slot;
// - each mark sits at its observer `place` (a timed run's ordinal in its project's start order; an untimed run's in the
//   outer band) on the fixed lattice (`latticePlace`, which never rescales with a count), so a run started later takes
//   the next place at the edge and nothing drawn moves; a mark the observer has not placed yet (its project not read in
//   full) is not drawn.
// Nothing here orders, counts or remembers.
import { bounds, footprint, islandRadius, islandSlots, latticePlace, slotTier } from "./geometry.js";

/** Where each island and mark of `summary` is, in world units: `islands[k]` (`{id, x, y, r, hue}`: its centre, its
 * radius and its hue) for `summary.projects[k]`, `x[i]`, `y[i]` for mark i (NaN while it has no place), and the world box
 * holding every island with its name's band. */
export function layout(summary, g) {
  const m = summary.marks;
  const reach = summary.projects.map((p) => p.runs);  // an island reaches its runs, or its furthest place (the outer band)
  for (let i = 0; i < m.id.length; i++) if (m.place[i] !== null) reach[m.project[i]] = Math.max(reach[m.project[i]], m.place[i] + 1);
  const slots = islandSlots(summary.projects.length, g, slotTier(Math.max(1, ...reach), g));
  const islands = summary.projects.map((p, k) => ({ id: p.id, x: slots[k].x, y: slots[k].y, r: islandRadius(reach[k], g), hue: k % g.island_hues }));
  const x = new Float64Array(m.id.length).fill(NaN), y = new Float64Array(m.id.length).fill(NaN);
  for (let i = 0; i < m.id.length; i++) {
    if (m.place[i] === null) continue;
    const island = islands[m.project[i]], o = latticePlace(m.place[i], g);
    x[i] = island.x + o.x; y[i] = island.y + o.y;
  }
  return { islands, x, y, bounds: bounds(islands.map((c) => footprint(c.x, c.y, c.r, g))) };
}

/** The observer's count behind each highlight chip, one entry per flag the contract declares (`enums.flag`, tested): the
 * runs with a failed audit, a refutation or a decision in the period (`aggregates.runs`, by each event's own time, the
 * same runs the chip lights), and the runs changed since their audit. */
export const FLAG_COUNT = {
  failed_audit: (a) => a.runs.failed_audits,
  refuted: (a) => a.runs.refutations,
  decided: (a) => a.runs.decisions,
  changed_since_audit: (a) => a.by.standing.changed_since_audit ?? 0,
};

/** Whether mark `i` is lit: with a highlight `flag`, when it carries that flag within the summary's window (the
 * observer's `window_flags`: an event by its own time); without one, when it changed within the window (`since`). */
export function lit(m, i, flag, since) {
  if (flag) return (m.window_flags[i] & (1 << m.flag_bits.indexOf(flag))) !== 0;
  return since === null || (m.updated[i] !== null && m.updated[i] >= since);
}

/** The callouts: the runs that need a human whose marks are on screen, at most `cap`, chosen across the islands: each
 * island's in the summary's order (the most urgent first), the islands taking turns in the order their first one comes,
 * so one busy island never takes every callout. `islandOf` names a need's island (its key's project by default). With
 * how many more need one (the store's own count, `total`, less those shown). */
export function callouts(needs, visible, total, cap, islandOf = (n) => n.key.split("/")[0]) {
  const queues = new Map();
  for (const n of needs) if (visible(n)) { const k = islandOf(n); if (!queues.has(k)) queues.set(k, []); queues.get(k).push(n); }
  const shown = [], lists = [...queues.values()];
  for (let round = 0; shown.length < cap && lists.some((l) => l.length > round); round++)
    for (const l of lists) { if (shown.length >= cap) break; if (l.length > round) shown.push(l[round]); }
  return { shown, more: Math.max(0, total - shown.length) };
}

/** What the accessible mirror lists, the same items the canvas draws: at store zoom every island and every callout's run; on
 * an island its marks in the observer's order, at most `cap`, and how many more its project has (the observer's count). */
export function mirror({ level, summary, zoom = null, shown = [], cap }) {
  if (level === "store") return { islands: summary.projects.map((p) => ({ id: p.id, label: p.label, runs: p.runs })), runs: shown.map((n) => ({ id: n.id, need: n })), rest: 0 };
  const m = summary.marks, project = summary.projects.findIndex((p) => p.id === zoom), runs = [];
  for (let i = 0; i < m.id.length && runs.length < cap; i++) if (m.project[i] === project) runs.push({ id: m.id[i], standing: m.standings[m.standing[i]], attempt: m.attempt[i] });
  return { islands: [], runs, rest: project < 0 ? 0 : summary.projects[project].runs - runs.length };
}

/** The SVG path of a standing's shape (`states.json` standing.shape), centred on 0,0, `r` across half: the canvas draws it
 * with Path2D and the key draws it as SVG, so a shape means the same in both. */
export function shapePath(shape, r) {
  const n = (v) => Math.round(v * 100) / 100;
  const poly = (pts) => `M${pts.map(([x, y]) => `${n(x * r)} ${n(y * r)}`).join("L")}Z`;
  const circle = (k) => `M${n(-k * r)} 0A${n(k * r)} ${n(k * r)} 0 1 0 ${n(k * r)} 0A${n(k * r)} ${n(k * r)} 0 1 0 ${n(-k * r)} 0Z`;
  const k = 0.55;  // the inner radius of a band, as a share of the outer
  // a ring: the outer circle one way round, the inner the other, so the nonzero rule leaves the middle empty
  const ring = `${circle(1)}M${n(-k * r)} 0A${n(k * r)} ${n(k * r)} 0 1 1 ${n(k * r)} 0A${n(k * r)} ${n(k * r)} 0 1 1 ${n(-k * r)} 0Z`;
  const build = Object.hasOwn(SHAPES, shape) ? SHAPES[shape] : null;
  if (!build) throw new Error(`no such shape: ${shape}`);
  return build({ r, n, poly, circle, ring, k });
}

/** Every shape `states.json` may name, a closed map: an unknown one is an error, never a plausible glyph. */
const SHAPES = {
  diamond: ({ poly }) => poly([[0, -1.15], [1.15, 0], [0, 1.15], [-1.15, 0]]),
  ring: ({ ring }) => ring,
  // the top half filled, the bottom half a band: a run half way
  half: ({ r, n, k }) => `M${n(-r)} 0A${n(r)} ${n(r)} 0 0 1 ${n(r)} 0ZM${n(r)} 0A${n(r)} ${n(r)} 0 0 1 ${n(-r)} 0L${n(-k * r)} 0A${n(k * r)} ${n(k * r)} 0 0 0 ${n(k * r)} 0Z`,
  triangle: ({ poly }) => poly([[0, -1.15], [1.05, 0.85], [-1.05, 0.85]]),
  circle: ({ circle }) => circle(1),
  square: ({ poly }) => poly([[-0.9, -0.9], [0.9, -0.9], [0.9, 0.9], [-0.9, 0.9]]),
  "hollow-square": ({ poly, k }) => `${poly([[-0.9, -0.9], [0.9, -0.9], [0.9, 0.9], [-0.9, 0.9]])}${poly([[-k, -k], [-k, k], [k, k], [k, -k]])}`,
  hexagon: ({ poly }) => poly([0, 1, 2, 3, 4, 5].map((j) => [Math.cos((Math.PI / 3) * j), Math.sin((Math.PI / 3) * j)])),
  cross: ({ poly }) => poly([[-1, -0.3], [-0.3, -1], [0, -0.7], [0.3, -1], [1, -0.3], [0.7, 0], [1, 0.3], [0.3, 1], [0, 0.7], [-0.3, 1], [-1, 0.3], [-0.7, 0]]),
};
