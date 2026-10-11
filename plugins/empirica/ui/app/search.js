// Finding and grouping runs. Pure: it reads the facts the observer publishes (a row's goal, claims, project label,
// session and `attention`) and decides nothing about them. Search is a substring match of every word typed.
import enums from "../enums.json" with { type: "json" };

const lower = (x) => String(x ?? "").toLowerCase();
/** Everything about a run that a search covers, lower-cased: its goal, claim text, project and session. */
export function haystack(row, project) {
  const s = row.session;
  return lower([row.goal, project.label, project.repo, s?.host, s?.label, s?.locator, ...(row.claims ?? []).map((c) => c.text)].join("\n"));
}
/** The runs matching `query` (all of them for a blank one), each with its project, in the view's order; and how
 * many runs that did not match could not be searched by claim text because their claims were not read. */
export function search(projects, query) {
  const words = lower(query).split(/\s+/).filter(Boolean);
  const entries = projects.flatMap((project) => project.runs.map((row) => ({ project, row })));
  const hits = entries.filter(({ project, row }) => { const h = haystack(row, project); return words.every((w) => h.includes(w)); });
  return { entries: hits, unsearched: words.length ? entries.filter((e) => !hits.includes(e) && e.row.claims === null).length : 0 };
}
/** `entries` under the core's `attention` values, in the contract's order; runs that were not read in full (attention
 * null) come last. Within a group the most recently changed run is first. */
export function groups(entries) {
  const order = [...enums.attention, null];
  const recent = (a, b) => (b.row.updated ?? -Infinity) - (a.row.updated ?? -Infinity) || (a.row.key < b.row.key ? -1 : 1);
  return order.map((attention) => ({ attention, entries: entries.filter((e) => e.row.attention === attention).sort(recent) })).filter((g) => g.entries.length);
}
/** The attempts of a run's family, in the observer's order (by generation), as rows of the view. */
export function attempts(project, row, byKey) {
  const family = project.families.find((f) => f.run === row.run);
  return (family ? family.attempts : [row.key]).map((k) => byKey.get(k)).filter(Boolean);
}
