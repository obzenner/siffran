// A run's attempts (the other generations of its session): a list with the run's place in it, previous and next, and
// a side-by-side of what each came to. Only published values are set side by side; whether an attempt is a retry of the
// same work is not claimed, a changed goal is said to be changed.
import { clip, esc, joined, t } from "../core/element.js";
import { auditHtml, chip, link, outcomeHtml, progress } from "./parts.js";

const cell = (html) => `<td class="align-top">${html}</td>`;
/** What a cell says where the attempt's document is not here: still being asked for, or it could not be had. */
const waiting = (a) => t(a.request === "pending" || a.request === "idle" ? "compare.pending" : "compare.failed");

function compareHtml(ctx) {
  const { attempts, row, href } = ctx;
  const head = attempts.map((a) => `<th scope="col" class="text-left">${a.row.key === row.key ? `<span aria-current="true">${t("row.attempt.short", { n: a.row.generation })}</span>` : link(href({ run: a.row.key, claim: null }), t("row.attempt.short", { n: a.row.generation }))}</th>`).join("");
  const rows = [
    // A goal is said to have changed only when both goals are known; a value the observer did not read is "Not read", never "None".
    [t("compare.goal"), attempts.map((a, i) => a.row.goal === null ? t("compare.unread") : `${esc(clip(a.row.goal, 100))}${i && attempts[i - 1].row.goal !== null && attempts[i - 1].row.goal !== a.row.goal ? ` <span class="text-xs font-medium">${t("compare.goal_changed")}</span>` : ""}`)],
    [t("compare.control"), attempts.map((a) => !a.detail ? waiting(a) : a.detail.governance ? esc(joined(t(`mode.${a.detail.governance.mode}`), t(`approval.${a.detail.governance.approval}`))) : t("compare.unread"))],
    [t("compare.outcome"), attempts.map((a) => outcomeHtml(a.row))],
    [t("compare.audit"), attempts.map((a) => a.row.audit === null ? t("compare.unread") : auditHtml(a.row.audit))],
    [t("compare.unresolved"), attempts.map((a) => !a.detail ? waiting(a) : a.detail.residuals === null ? t("compare.unread") : a.detail.residuals.length ? a.detail.residuals.map((x) => `<p>${esc(x.message)}</p>`).join("") : t("compare.none"))],
  ];
  return `<div class="overflow-x-auto"><table class="table ev-compare w-full"><caption class="sr-only">${t("compare.caption")}</caption><thead><tr><td></td>${head}</tr></thead><tbody>${rows.map(([label, cells]) => `<tr><th scope="row" class="text-left font-medium">${label}</th>${cells.map(cell).join("")}</tr>`).join("")}</tbody></table></div>`;
}

export function attemptsHtml(ctx) {
  const { attempts, row, href } = ctx;
  const at = attempts.findIndex((a) => a.row.key === row.key);
  if (attempts.length < 2) return `<p class="text-s text-muted-foreground">${t("attempts.only")}</p>`;
  const go = (a, text) => (a ? link(href({ run: a.row.key, claim: null }), text, "data-attempt") : "");
  const failed = attempts.some((a) => a.row.key !== row.key && a.request && a.request !== "pending" && a.request !== "idle");
  const retry = failed ? `<p class="flex flex-wrap items-center gap-s text-s"><span>${t("attempts.failed")}</span><button type="button" class="btn" data-variant="outline" data-size="sm" data-retry="family">${t("detail.retry")}</button></p>` : "";
  return `<p class="flex flex-wrap items-center gap-s text-s">${go(attempts[at - 1], t("attempts.previous"))}<span>${t("row.attempt", { n: row.generation, of: attempts.length })}</span>${go(attempts[at + 1], t("attempts.next"))}</p>${retry}${compareHtml(ctx)}`;
}
