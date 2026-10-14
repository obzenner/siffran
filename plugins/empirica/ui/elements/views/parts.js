// Small builders every view shares. They word facts the observer published (contract empirica-ui-view/v1) and decide
// nothing: a state is its glyph and word, a count is the observer's count, a link is where the app says it goes.
import { clip, count, esc, hostName, joined, stateHtml, t } from "../core/element.js";

export const LINK = "text-foreground underline decoration-subtle underline-offset-2 hover:decoration-foreground";
export const chip = (domain, value) => stateHtml(domain, value, "full");
export const glyph = (domain, value) => stateHtml(domain, value, "glyph");
export const link = (url, text, attrs = "") => (url ? `<a class="${LINK}" href="${esc(url)}" ${attrs}>${esc(text)}</a>` : `<span>${esc(text)}</span>`);

/** "3 of 8 claims approved" (the core's count), or that the run has no claims yet; nothing for a run not read. */
export function progress(row) {
  if (row.progress === null) return "";
  return row.progress.required ? count("progress", row.progress.required, { n: row.progress.approved, total: row.progress.required }) : t("progress.none");
}

/** The audit as words: the verdict, and (beside it) whether it still covers the current argument and whether a re-audit
 * waits: three facts the core publishes, each its own words. */
export function auditHtml(a) {
  if (a === null) return "";
  const more = [a.current === false ? t("audit.not_current") : "", a.pending ? t(a.verdict === "none" ? "audit.first_pending" : "audit.reaudit_pending") : ""].filter(Boolean);
  return `<span class="inline-flex flex-wrap items-center gap-xs">${chip("audit", a.verdict)}${more.map((m) => `<span class="text-xs text-muted-foreground">${m}</span>`).join("")}</span>`;
}

/** Who acts and where, as one phrase: "A human · when the agent next reports". */
export const whoWhere = (x) => joined(t(`who.${x.who}`), t(`where.${x.where}`));

/** The session a run lives in: the host and its label, and the locator to copy; unknown where its host recorded none. */
export function sessionHtml(s) {
  if (s === null) return "";
  const name = joined(hostName(s.host), s.label === null ? "" : esc(s.label));
  return `<span class="inline-flex min-w-0 flex-wrap items-center gap-x-xs gap-y-3xs"><span>${name}</span>${s.locator === null ? `<span class="text-muted-foreground">${t("session.unknown")}</span>` : `<ev-copy text="${esc(s.locator)}" label="fact.session"></ev-copy>`}</span>`;
}

/** What the session's host recorded about it, in a closed disclosure: whether it is interactive (unknown when the host did not
 * say) and whether the run is delegated, and that none of it says the session is alive. */
export function sessionContextHtml(s) {
  if (s === null) return "";
  const interactive = s.interactive === null ? t("session.interactive.unknown") : s.interactive ? t("session.interactive.yes") : t("session.interactive.no");
  const delegated = s.delegated ? t("session.delegated.yes") : t("session.delegated.no");
  return `<details class="text-xs"><summary class="cursor-pointer text-muted-foreground" data-session-context>${t("session.context")}</summary><div class="flex flex-col gap-3xs pt-xs"><span>${interactive}</span><span>${delegated}</span><span class="text-muted-foreground">${t("session.liveness")}</span></div></details>`;
}

/** A claim of the run as a link labelled with its words (never its id); plain words where links are not offered. */
export function claimLink(row, id, href) {
  const c = (row.claims ?? []).find((x) => x.id === id);
  return c ? link(href?.({ claim: c.id }), clip(c.text)) : "";
}
export const list = (items, cls = "") => `<ul class="flex flex-col gap-xs ${cls}">${items.map((i) => `<li>${i}</li>`).join("")}</ul>`;
/** A labelled fact: a term (copy) and its definition (html). */
export const fact = (label, html) => `<div><dt class="text-xs text-muted-foreground">${label}</dt><dd class="text-s">${html}</dd></div>`;
/** A collapsible section of the sheet: its title (with the count when it has one), open or closed. */
export const section = (id, title, body, { open = true, count: n = null } = {}) => `<details class="border-t border-border py-s" ${open ? "open" : ""} data-section="${id}">
  <summary class="flex cursor-pointer items-baseline gap-xs text-m font-semibold" id="${id}">${title}${n === null ? "" : `<span class="text-xs font-normal text-muted-foreground">${n}</span>`}</summary>
  <div class="flex flex-col gap-s pt-s">${body}</div></details>`;
