// The run list: the runs under the core's attention groups, one row each. A row answers "which run, what became of
// it, what does it owe next and where do I find its session" without opening it.
import { clip, count, esc, t } from "../core/element.js";
import { auditHtml, chip, outcomeHtml, sessionHtml, whoWhere } from "./parts.js";

/** What the run owes next (the core's `next`), as who acts and where, then its words. Nothing for an ended run. */
function nextHtml(row) {
  if (row.attention === null) return `<span class="text-xs text-muted-foreground">${t("row.unread")}</span>`;
  if (row.attention === "ended") return "";
  if (row.next === null) return `<span class="text-xs text-muted-foreground">${t("row.nothing_owed")}</span>`;
  return `<span class="flex flex-col gap-3xs text-xs"><span class="font-medium">${esc(whoWhere(row.next))}</span><span class="text-muted-foreground">${esc(row.next.message)}</span></span>`;
}

/** One run's row: a link to the run, and beside it (not inside it) its session and the button that copies the locator. */
export function rowHtml({ row, project }, { href, selected, attempts }) {
  const goal = row.goal === null ? t("goal.unread") : esc(clip(row.goal, 140));
  return `<li class="flex flex-col gap-2xs border-b border-border py-xs" data-row="${esc(row.key)}">
    <a class="flex flex-col gap-2xs rounded-m p-xs hover:bg-muted" href="${esc(href({ run: row.key, claim: null }))}" data-run="${esc(row.key)}" ${selected ? 'aria-current="true"' : ""}>
      <span class="text-s font-medium">${goal}</span>
      <span class="text-xs text-muted-foreground">${esc(project.label)}${row.attention === null ? "" : ` · ${t("row.attempt", { n: row.generation, of: attempts })}`}</span>
      <span class="flex flex-wrap items-center gap-x-s gap-y-3xs">${outcomeHtml(row)}${auditHtml(row.audit)}</span>
      ${nextHtml(row)}
    </a>
    <div class="px-xs text-xs">${sessionHtml(row.session)}</div>
  </li>`;
}

/** The whole list: each non-empty attention group with its heading and count; runs not read in full under their own. */
export function listHtml(groups, { href, selected, attemptsOf }) {
  return groups.map((g) => `<section class="flex flex-col gap-2xs" aria-labelledby="group-${g.attention ?? "unread"}">
    <h2 id="group-${g.attention ?? "unread"}" class="flex items-center gap-xs text-s font-semibold">${g.attention === null ? t("group.unread") : chip("attention", g.attention)}<span class="text-xs font-normal text-muted-foreground">${count("count.runs", g.entries.length)}</span></h2>
    <ul>${g.entries.map((e) => rowHtml(e, { href, selected: e.row.key === selected, attempts: attemptsOf(e) })).join("")}</ul>
  </section>`).join("");
}
