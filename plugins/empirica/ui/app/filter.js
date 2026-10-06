// The view's filters (VIEW.md R5), as one pure function over the observer's view (empirica-ui-view/v1). Filters
// are declared in catalogue.json (`filters`); this selects values the data provides and decides no state (X1/E4).
// A filter's choice is a list of values (a field filter: any of them matches) or `true` (a flag filter: the
// claim's `flag` field is set). Different filters must all hold. Tested in tests/filter.test.mjs.

import { approved, settled } from "../elements/core/count.js";

const chosen = (f, choice) => (f.flag ? choice[f.id] === true : (choice[f.id] ?? []).length > 0);
function holds(f, choice, x) {
  return f.flag ? x[f.flag] != null : choice[f.id].includes(x[f.field]);
}

/**
 * What stays in the view: the projects, runs and claims that pass, each project keeping the label the observer gave it;
 * support edges and previous attempts only between what stays.
 * A run keeps its own facts: `total` is how many claims it has and `approved` how many of them the plugin
 * approved, whatever the filters show of them.
 * @returns {{projects:{id:string,name:?string,label:string,since:?number,runs:object[]}[], counts:{projects:number,runs:number,claims:number}}}
 */
export function apply(data, choice, filters) {
  const runF = filters.filter((f) => f.of === "run" && chosen(f, choice));
  const claimF = filters.filter((f) => f.of === "claim" && chosen(f, choice));
  const projects = [];
  for (const p of data.projects) {
    const runs = [];
    for (const r of p.runs) {
      if (!runF.every((f) => holds(f, choice, r))) continue;
      const claims = r.claims.filter((c) => claimF.every((f) => holds(f, choice, c)));
      if (claimF.length && !claims.length) continue;
      const ids = new Set(claims.map((c) => c.id));
      runs.push({ ...r, total: r.claims.length, approved: approved(r), claims, edges: r.edges.filter((e) => ids.has(e.from) && ids.has(e.to)), root: ids.has(r.root) ? r.root : null });
    }
    if (runs.length) projects.push({ id: p.id, name: p.name, label: p.label, since: p.since, runs });
  }
  const kept = new Set(projects.flatMap((p) => p.runs.map((r) => r.key)));
  for (const p of projects) for (const r of p.runs) if (!kept.has(r.previous)) r.previous = null;
  const runs = projects.flatMap((p) => p.runs);
  return { projects, counts: { projects: projects.length, runs: runs.length, claims: runs.reduce((n, r) => n + r.claims.length, 0) } };
}

/** What `projects` show, counted per value in the order `order(domain)` declares (states.json); values with no
 * count are left out. The legend's numbers. Each claim counts once, under the mark it wears: an approved claim that
 * is not settled (core/count.js `settled`, with `quiet(domain)`) counts under `unsettled`, by its review. */
export function showing(projects, order, quiet) {
  const rs = projects.flatMap((p) => p.runs), cs = rs.flatMap((r) => r.claims);
  const unsettled = (c) => c.state === quiet("claim") && !settled(c, quiet);
  const tally = (domain, xs) => order(domain).map((value) => ({ value, n: xs.filter((x) => x === value).length })).filter((x) => x.n);
  return {
    total: rs.length,
    runs: tally("run", rs.map((r) => r.state)), audits: tally("audit", rs.map((r) => r.audit)),
    diagnostics: tally("diagnostic", rs.map((r) => r.diagnostic)),
    claims: tally("claim", cs.filter((c) => !unsettled(c)).map((c) => c.state)),
    unsettled: tally("review", cs.filter(unsettled).map((c) => c.review)),
    gating: cs.filter((c) => c.gating).length, supports: rs.reduce((n, r) => n + r.edges.length, 0), attempts: rs.filter((r) => r.previous).length,
  };
}
