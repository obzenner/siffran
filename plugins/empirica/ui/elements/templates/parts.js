// Small builders every template shares. They word facts the observer published (contract empirica-ui-view/v1) and
// decide nothing: a state is its word, a count is the core's count, a link is where the app says it goes.
import { clip, count, esc, joined, t, hostName } from "../core/element.js";

export const LINK = "text-foreground underline decoration-subtle underline-offset-2 hover:decoration-foreground";
export const chip = (domain, value) => `<ev-state domain="${esc(domain)}" value="${esc(value)}"></ev-state>`;
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

/** What a run came to: its state and the core's progress, or the diagnostic that kept it from being read in full. */
export function outcomeHtml(row) {
  if (row.state === null) return chip("diagnostic", row.diagnostic);
  const p = progress(row);
  return `<span class="inline-flex flex-wrap items-center gap-xs">${chip("run", row.state)}${p ? `<span class="text-xs text-muted-foreground">${esc(p)}</span>` : ""}${row.diagnostic ? `${chip("diagnostic", row.diagnostic)}` : ""}</span>`;
}

/** Who acts and where, as one phrase: "You · when the agent next reports". */
export const whoWhere = (x) => joined(t(`who.${x.who}`), t(`where.${x.where}`));

/** The session a run lives in: the host and its label, and the locator to copy; unknown where its host recorded none. */
export function sessionHtml(s, copy = true) {
  if (s === null) return "";
  const name = joined(hostName(s.host), s.label === null ? "" : esc(s.label));
  return `<span class="inline-flex min-w-0 flex-wrap items-center gap-x-xs gap-y-3xs"><span>${name}</span>${s.locator === null ? `<span class="text-muted-foreground">${t("session.unknown")}</span>` : copy ? `<ev-copy text="${esc(s.locator)}" label="fact.session"></ev-copy>` : `<code class="break-all font-mono text-xs">${esc(s.locator)}</code>`}</span>`;
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
  return c ? link(href?.({ run: row.key, claim: c.id }), clip(c.text)) : "";
}
export const list = (items, cls = "") => `<ul class="flex flex-col gap-xs ${cls}">${items.map((i) => `<li>${i}</li>`).join("")}</ul>`;
/** A labelled fact: a term (copy) and its definition (html). */
export const fact = (label, html) => `<div><dt class="text-xs text-muted-foreground">${label}</dt><dd class="text-s">${html}</dd></div>`;
export const section = (id, title, body, extra = "") => `<section class="flex flex-col gap-s border-t border-border pt-m" aria-labelledby="${id}" ${extra}><h2 id="${id}" class="text-m font-semibold">${title}</h2>${body}</section>`;
