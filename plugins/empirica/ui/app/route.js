// The one navigation model (VIEW.md "Navigation"). What is selected is one value of a declared union:
//   none | project | run | claim (of a run) | step (of a run) | file (of a run)
// and it lives in the URL with the filters. `claim`, `step` and `file` are children of a run; a disclosure such as
// "Technical details" is not a level. Every way of moving (a click on a mark, a link, a breadcrumb, Esc, Back, a
// reload) ends in the same selection value, so they cannot disagree. Pure; tested in tests/route.test.mjs.
//
// The levels and who is whose parent are declared once, in catalogue.json `route`; this module reads them. What stays
// here is only how a level is written in a URL and how a parent is built from its child.
import catalogue from "../catalogue.json" with { type: "json" };

const { levels: LEVELS, parent: PARENT } = catalogue.route;
/** The URL parameter of each level, outermost first: its own name. */
export const PARAMS = Object.fromEntries(LEVELS.map((l) => [l, l]));
/** The levels of a run: the ones whose parent is a run. */
const CHILDREN = LEVELS.filter((l) => PARENT[l] === "run");
/** The levels that are drawn as a mark in the view; closing a level without one returns to its nearest ancestor with one. */
export const MARKED = catalogue.route.marked;
// A level added to the catalogue without code here is a mistake that must fail at load, not at the first click.
for (const l of LEVELS) if (!["project", "run", "claim", "step", "file"].includes(l)) throw new Error(`route.js has no URL form for the level ${l}`);

/** The selection a query names. Several children named at once resolve to the first of claim, step, file; a step
 * that is not a positive whole number names its run. */
export function parse(q) {
  const run = q.get(PARAMS.run);
  if (run) {
    for (const kind of CHILDREN) {
      const v = q.get(PARAMS[kind]);
      if (!v) continue;
      if (kind !== "step") return { kind, run, [kind]: v };
      if (/^[1-9]\d*$/.test(v)) return { kind, run, step: Number(v) };
    }
    return { kind: "run", run };
  }
  const project = q.get(PARAMS.project);
  return project ? { kind: "project", project } : { kind: "none" };
}

/** Write `sel` into the query `q`, removing every other level. */
export function write(q, sel) {
  for (const name of Object.values(PARAMS)) q.delete(name);
  if (sel.kind === "none") return;
  if (sel.kind === "project") { q.set(PARAMS.project, sel.project); return; }
  q.set(PARAMS.run, sel.run);
  if (sel.kind !== "run") q.set(PARAMS[sel.kind], String(sel[sel.kind]));
}

export const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** The level above: step, file and claim → their run; run → its project; project → none. `runs` maps a run key to
 * its row; a run that is not in it leads to none. */
export function parentOf(sel, runs) {
  if (sel.kind === "none") return null;
  const up = PARENT[sel.kind];
  if (up === "none") return { kind: "none" };
  if (up === "run") return { kind: "run", run: sel.run };
  const project = runs.get(sel.run)?.project;  // up === "project"
  return project ? { kind: "project", project } : { kind: "none" };
}

/** The nearest level at or above `sel` that is drawn as a mark: a step or a file has none, its run does. */
export function markedOf(sel, runs) {
  for (let s = sel; s && s.kind !== "none"; s = parentOf(s, runs)) if (MARKED.includes(s.kind)) return s;
  return null;
}

/** The selections from the outermost level down to `sel` itself (none is not a level). */
export function path(sel, runs) {
  const out = [];
  for (let s = sel; s && s.kind !== "none"; s = parentOf(s, runs)) out.unshift(s);
  return out;
}
