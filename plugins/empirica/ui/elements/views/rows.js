// One run as one line (UI.md, Rows): glyph, goal, what is asked (a run that needs a human also says which host), project and
// time. The core's sentences, session paths and reason text are never here; they are in the sheet. Pure strings: the list
// renders hundreds of these, so a row is not an element.
import { ago, clip, esc, hostName, joined, t } from "../core/element.js";
import { glyph } from "./parts.js";

/** @param {object} r a page line (/api/runs, /api/search) @param {{href:(id:string)=>string, now:number, open:?string, here:?string}} ctx
 * `open` is the run whose sheet is open; `here` the one the keyboard is on. */
export function rowHtml(r, ctx) {
  // only a run filed under Needs a human shows its ask: a host that has not looked for a dialog (not_ended) owes no question yet
  const asked = r.standing === "needs_human" && r.ask !== null;
  const goal = r.goal === null ? `<span class="text-muted-foreground">${t("goal.unread")}</span>` : esc(clip(r.goal, 140));
  const where = asked ? `<span class="shrink-0 rounded-m bg-tone-human-tint px-xs text-xs font-medium text-tone-human">${t(`ask.${r.ask}`)}</span><span class="ev-optional hidden shrink-0 text-xs text-muted-foreground md:inline">${joined(r.host === null ? "" : hostName(r.host), esc(r.session ?? ""))}</span>` : "";
  // an attempt after the first says so on every row, so attempts at one run never read as unrelated runs
  const attempt = r.generation > 1 ? `<span class="shrink-0 rounded-s border border-border px-2xs text-xs text-muted-foreground" data-attempt-badge>${t("row.attempt.short", { n: r.generation })}</span>` : "";
  // the time column is a fixed width: a time not recorded is a short word there, the whole sentence in its title
  const at = asked ? r.asked_at : r.updated, short = typeof at === "number" ? ago(at, ctx.now) : t("time.unknown.short");
  const time = asked ? t("row.asked", { when: short }) : short, full = asked ? t("row.asked", { when: ago(at, ctx.now) }) : ago(at, ctx.now);
  return `<li data-row="${esc(r.key)}" data-id="${r.id}" class="border-b border-border">
    <a class="ev-row flex h-xl items-center gap-s px-s text-s hover:bg-muted" href="${esc(ctx.href(r.id))}" data-run="${r.id}" ${ctx.open === r.id ? 'aria-current="true"' : ""} ${ctx.here === r.id ? "" : 'tabindex="-1"'}>
      ${glyph("standing", r.standing)}
      <span class="min-w-0 flex-1 truncate" data-goal>${goal}</span>
      ${where}${attempt}
      <span class="ev-optional hidden w-project shrink-0 truncate text-xs text-muted-foreground sm:block" data-project>${esc(r.project_label)}</span>
      <span class="w-time shrink-0 overflow-hidden text-ellipsis whitespace-nowrap text-right text-xs tabular-nums text-muted-foreground" title="${esc(full)}" data-time>${esc(time)}</span>
    </a>
  </li>`;
}

/** The rows of a list as the window shows them, between two spacers that stand for the rows not drawn (their heights are
 * set by the caller: it measures a row). */
export const windowHtml = (rows, ctx) => `<ul class="flex flex-col" data-rows><li aria-hidden="true" data-before></li>${rows.map((r) => rowHtml(r, ctx)).join("")}<li aria-hidden="true" data-after></li></ul>`;
