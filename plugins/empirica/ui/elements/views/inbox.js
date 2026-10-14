// The inbox home (UI.md, The inbox): the rail of buckets and projects, the value strip over a period, the calendar of
// runs per day, and the groups of rows, the ones that need a human first. Everything here is counted by the observer
// (the summary's facets, aggregates and calendar cells, by civil day in the viewer's zone); this only draws them.
import catalogue from "../../catalogue.json" with { type: "json" };
import enums from "../../enums.json" with { type: "json" };
import { TONE, count, esc, joined, num, stateOf, t } from "../core/element.js";
import { glyph } from "./parts.js";
import { rowHtml } from "./rows.js";

const link = (url, inner, cls = "", attrs = "") => `<a class="${cls}" href="${esc(url)}" ${attrs}>${inner}</a>`;

/** The rail: Everything, one bucket per standing the summary counts (an empty one is left out unless it is the one chosen),
 * then the projects. `go(filters)` is the link to the work view with those filters. */
export function railHtml(summary, place, go) {
  const standings = summary.marks.standings;
  const mine = place.filters.standing;
  const item = (url, label, n, current, extra = "") => link(url, `${extra}<span class="min-w-0 flex-1 truncate">${label}</span><span class="text-xs tabular-nums text-muted-foreground">${num(n)}</span>`,
    `flex h-xl items-center gap-xs rounded-m px-xs text-s hover:bg-muted ${current ? "bg-muted font-medium" : ""}`, current ? 'aria-current="true"' : "");
  const buckets = standings.filter((s) => (summary.facets.standing[s] ?? 0) > 0 || s === mine)
    .map((s) => item(go({ standing: s }), t(`${stateOf("standing", s).copy}.label`), summary.facets.standing[s] ?? 0, mine === s, glyph("standing", s)));
  const projects = [...summary.projects].sort((a, b) => a.label.localeCompare(b.label))
    .map((p) => item(go({ project: p.id }), esc(p.label), p.runs, place.filters.project === p.id));
  const covered = summary.coverage;
  const unread = covered.pending ? t("coverage.pending", { read: num(covered.read + covered.unreadable + covered.partial), runs: num(covered.runs) }) : "";
  return `<nav class="flex flex-col gap-m" aria-label="${t("rail.label")}">
    <div class="flex flex-col gap-3xs">${item(go({}), t("rail.everything"), covered.runs, place.view === "work" && !Object.keys(place.filters).length && !place.q)}${buckets.join("")}</div>
    <div class="flex flex-col gap-3xs"><h2 class="px-xs text-xs font-medium tracking-wide text-muted-foreground uppercase">${t("rail.projects")}</h2>${projects.join("")}</div>
    ${unread ? `<p class="px-xs text-xs text-muted-foreground" aria-live="polite">${unread}</p>` : ""}
  </nav>`;
}

/** The value strip: what the period held, each number a link to the runs that carry it. A fact with no recorded time is not in
 * any period, and the runs not read in full are in no count; the strip says both, in one quiet line with the audits and the
 * decisions by kind. `periodHref(days)` and `go(filters)` are links. */
export function stripHtml(summary, place, { go, periodHref }) {
  const a = summary.aggregates, facts = a.facts;
  const periods = catalogue.periods.map((key) => {
    const on = place.period === key && place.since === null;
    return link(periodHref(key), t(`period.${key}`), `rounded-m px-xs py-3xs text-xs ${on ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted"}`, on ? 'aria-current="true"' : "");
  });
  const asks = enums.ask.filter((k) => a.decisions_by[k]?.facts > 0)
    .map((k) => t("strip.decision_part", { n: num(a.decisions_by[k].facts), what: t(`ask.part.${k}`) }));
  const stat = (n, label, url) => link(url, `<span class="text-l font-semibold tabular-nums">${num(n)}</span> <span class="text-s text-muted-foreground">${label}</span>`, "flex items-baseline gap-xs hover:underline", "data-stat");
  const quiet = [t("strip.audits", { passed: num(a.by.audit.passed ?? 0), none: num(a.by.audit.none ?? 0) }), ...asks,
    a.not_read ? t("strip.not_read", { n: num(a.not_read) }) : "", a.untimed_runs ? t("strip.untimed", { n: num(a.untimed_runs) }) : ""];
  return `<section class="flex flex-col gap-xs" aria-label="${t("strip.label")}">
    <div class="flex flex-wrap items-baseline gap-x-l gap-y-xs">
      <span class="flex items-center gap-3xs">${periods.join("")}</span>
      ${stat(a.total, count("strip.runs", a.total), go({}))}
      ${stat(facts.decisions, t("strip.decisions"), go({ flag: "decided" }))}
      ${stat(facts.failed_audits, t("strip.caught"), go({ flag: "failed_audit" }))}
      ${stat(facts.refutations, t("strip.refuted"), go({ flag: "refuted" }))}
    </div>
    <p class="text-xs text-muted-foreground" data-strip-notes>${joined(...quiet)}</p>
  </section>`;
}

/** The calendar's days: the `days` civil days ending today in `tz`, the IANA zone the summary was asked in (it names it
 * back, `summary.calendar.tz`), never the process's own zone. Each is its ISO date (`day`, how the observer names its
 * cells) and that date's noon UTC (`ms`, only for its label, which is formatted in UTC so it names that date). Calendar
 * arithmetic on the date, never 24-hour steps, so a day 23 or 25 hours long is still one day. */
export function calendarDays(nowMs, days, tz) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(nowMs).map((x) => [x.type, x.value]));
  const pad = (n) => String(n).padStart(2, "0");
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) - (days - 1 - i), 12));
    return { day: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`, ms: d.getTime() };
  });
}

/** The observer's calendar cells (`summary.calendar`, columnar) by `project:day`: how many runs, the most urgent standing
 * and the day's first moment, as served. Nothing here counts or compares. */
export function calendarCells(calendar) {
  const cells = new Map();
  for (let i = 0; i < calendar.day.length; i++)
    cells.set(`${calendar.project[i]}:${calendar.day[i]}`, { n: calendar.runs[i], standing: calendar.standing[i], start: calendar.start[i] });
  return cells;
}

/** The projects × days calendar, the observer's cells (counted by civil day in the viewer's zone). A cell takes the tone of
 * the most urgent standing among its runs, and an urgent one is drawn taller as well (size and colour: no single channel
 * carries it). A cell with runs is a link to that project's runs since that day's first moment (the observer's). A run
 * with no time is on no day; how many read runs have none is the observer's count (`aggregates.untimed_runs`). */
export function calendarHtml(summary, place, { nowMs, go }) {
  const days = calendarDays(nowMs, catalogue.calendar_days, summary.calendar.tz), cells = calendarCells(summary.calendar), untimed = summary.aggregates.untimed_runs;
  const shown = [...summary.projects].sort((a, b) => b.runs - a.runs).slice(0, catalogue.calendar_projects);
  const projectIndex = new Map(summary.projects.map((p, i) => [p.id, i]));
  const date = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" }); // a day's ms is its noon UTC
  const row = (p) => {
    const i = projectIndex.get(p.id);
    const cols = days.map(({ day, ms }) => {
      const c = cells.get(`${i}:${day}`);
      if (!c) return `<span class="min-w-0 flex-1" aria-hidden="true"></span>`;
      const name = summary.marks.standings[c.standing], s = stateOf("standing", name), tone = TONE[s.tone];
      const label = joined(esc(p.label), date.format(ms), count("calendar.runs", c.n), t(`${s.copy}.label`));
      return link(go({ project: p.id }, Math.floor(c.start)), `<span class="sr-only">${label}</span>`,
        `ev-cell block min-w-0 flex-1 rounded-s ${tone.solid} ${s.urgent ? "h-m" : "h-s"}`, `title="${label}" data-cell data-standing="${name}"`);
    });
    return `<div class="flex items-end gap-3xs"><span class="w-project shrink-0 truncate text-xs text-muted-foreground">${esc(p.label)}</span><span class="flex min-w-0 flex-1 items-end gap-3xs">${cols.join("")}</span></div>`;
  };
  return `<section class="flex flex-col gap-xs" aria-label="${t("calendar.label")}">
    <h2 class="text-xs font-medium tracking-wide text-muted-foreground uppercase">${joined(t("calendar.title", { days: days.length }), untimed ? t("calendar.untimed", { n: num(untimed) }) : "")}</h2>
    <div class="flex flex-col gap-3xs" data-calendar>${shown.map(row).join("")}</div>
  </section>`;
}

/** One group of the home: a bucket's name and its count (the summary's), its first rows, and the link to all of them. */
export function groupHtml(standing, total, rows, ctx) {
  const s = stateOf("standing", standing);
  const cap = rows.length;
  return `<section class="flex flex-col" aria-labelledby="group-${standing}" data-group="${standing}">
    <div class="flex items-baseline gap-xs px-s py-2xs">${glyph("standing", standing)}<h2 id="group-${standing}" class="text-s font-semibold">${t(`${s.copy}.label`)}</h2>
      ${total > cap ? link(ctx.all(standing), t("group.all", { n: num(total) }), "text-xs tabular-nums text-foreground underline underline-offset-2", "data-all data-count") : `<span class="text-xs tabular-nums text-muted-foreground" data-count>${num(total)}</span>`}</div>
    <ul class="flex flex-col">${rows.map((r) => rowHtml(r, ctx)).join("")}</ul>
  </section>`;
}

/** The home body: the groups in the order the summary lists the standings; a bucket with no runs is not drawn. */
export function groupsHtml(summary, loaded, ctx) {
  const order = summary.marks.standings.filter((s) => (summary.facets.standing[s] ?? 0) > 0);
  if (!order.length) return `<p class="px-s text-s text-muted-foreground">${t(ctx.empty)}</p>`;
  return order.map((s) => groupHtml(s, summary.facets.standing[s], loaded.get(s) ?? [], ctx)).join("");
}
