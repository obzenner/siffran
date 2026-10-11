// The run workspace: one run, in the order an operator asks about it. Now (or Outcome), claims with their proof in
// place, the support graph, attempts, history and changed files. It words what the observer published for this run
// (the row from the view, the document from /api/run) and decides nothing; where the document has not arrived it says
// so instead of showing less as if there were less.
import catalogue from "../../catalogue.json" with { type: "json" };
import enums from "../../enums.json" with { type: "json" };
import { clip, esc, joined, t, when } from "../core/element.js";
import { attemptsHtml } from "./attempts.js";
import { claimsHtml, supportHtml } from "./claims.js";
import { auditHtml, chip, claimLink, fact, list, outcomeHtml, section, sessionContextHtml, sessionHtml, whoWhere } from "./parts.js";
import { historyHtml, changedHtml } from "./trail.js";

const loading = (ctx) => (ctx.notice ? "" : `<p class="text-s text-muted-foreground">${t("detail.loading")}</p>`);

/** The notice for a document that did not arrive, with the way to ask again. */
function noticeHtml(key) {
  return key ? `<div class="alert" role="alert" data-variant="destructive"><section class="flex flex-wrap items-center gap-s"><span>${t(key)}</span><button type="button" class="btn" data-variant="outline" data-size="sm" data-retry>${t("detail.retry")}</button></section></div>` : "";
}

function headerHtml({ row, project, attempts }, href) {
  const goal = row.goal ?? "";
  return `<header class="flex flex-col gap-s">
    <a class="w-fit text-s text-foreground underline underline-offset-2 lg:hidden" href="${esc(href({ run: null, claim: null }))}" data-back>${t("run.back")}</a>
    <h1 class="text-l font-semibold" tabindex="-1" data-title>${row.goal === null ? t("goal.unread") : esc(clip(goal, 160))}</h1>
    ${row.goal !== null && goal.length > 160 ? `<ev-more text="${esc(goal)}"></ev-more>` : ""}
    <dl class="ev-facts flex flex-col gap-xs">
      ${fact(t("fact.project"), `${esc(project.label)}${project.repo ? ` <code class="break-all font-mono text-xs">${esc(project.repo)}</code>` : ""}`)}
      ${fact(t("fact.attempt"), t("row.attempt", { n: row.generation, of: attempts.length }))}
      ${fact(t("fact.outcome"), outcomeHtml(row))}
      ${row.audit ? fact(t("fact.audit"), auditHtml(row.audit)) : ""}
      ${row.session ? fact(t("fact.session"), `${sessionHtml(row.session)}${sessionContextHtml(row.session)}`) : ""}
      ${row.updated === null ? "" : fact(t("fact.when"), esc(when(row.updated)))}
    </dl>
  </header>`;
}

/** What is owed (an active run) or what was left (an ended one): the observer's own words, grouped, with who acts and where. */
function nowHtml(ctx) {
  const { row, detail, href } = ctx;
  const ended = row.attention === "ended";
  const left = (x) => `<span class="flex flex-col items-start gap-3xs"><span>${esc(x.message)}</span>${x.claims.map((c) => claimLink(row, c, href)).join("")}</span>`;
  const owes = (x) => `<span class="flex flex-col items-start gap-3xs"><span class="text-s font-medium">${esc(whoWhere(x))}</span><span>${esc(x.message)}</span>${(x.claims ?? []).map((c) => claimLink(row, c, href)).join("")}</span>`;
  let body;
  if (row.attention === null) body = `<p class="text-s">${t("now.unread")}</p>`;
  else if (!detail) body = loading(ctx);
  else if (ended) {
    const s = detail.stop, then = s.basis === "unavailable"
      ? `<p class="text-s">${t("now.unavailable")}</p>`
      : detail.residuals.length ? `<h3 class="text-s font-medium">${t("now.unresolved")}</h3>${list(detail.residuals.map(left))}` : `<p class="text-s text-muted-foreground">${t("now.none_left")}</p>`;
    const scope = `<p class="text-xs text-muted-foreground">${esc(joined(s.frozen ? t("stop.frozen") : "", t("stop.scope", { required: s.required.length, deferred: s.deferred.length })))}</p>`;
    const today = detail.now?.length ? `<h3 class="text-s font-medium">${t("now.unresolved_now")}</h3><p class="text-xs text-muted-foreground">${t("now.today_hint")}</p>${list(detail.now.map(left))}` : "";
    body = `<p class="text-s">${t("now.ended")}</p>${scope}${then}${today}`;
  } else {
    body = `<p class="flex items-center gap-xs text-s">${chip("attention", row.attention)}<span class="text-muted-foreground">${t(`now.${row.attention}`)}</span></p>${detail.outstanding.length ? list(detail.outstanding.map(owes)) : `<p class="text-s text-muted-foreground">${t("row.nothing_owed")}</p>`}${detail.residuals.length ? `<h3 class="text-s font-medium">${t("now.unresolved_so_far")}</h3>${list(detail.residuals.map(left))}` : ""}`;
  }
  return section("now", t(ended ? "now.outcome" : "now.title"), body);
}

/** Governance and the audit, as labelled facts: the configuration in force, what is proposed only while a proposal waits. */
function governanceHtml({ row, detail }) {
  if (!detail?.governance) return "";
  const g = detail.governance, a = detail.audit;  // null for a run the observer could not read
  const meters = enums.budgets.map((k) => fact(t(`budget.${k}`), `<ev-meter label="budget.${k}" show="amount" used="${g.ceilings[k].used}" max="${g.ceilings[k].max}"></ev-meter>`));
  const facts = [
    fact(t("fact.control"), esc(joined(t(`mode.${g.mode}`), t(`approval.${g.approval}`)))),
    ...meters,
    ...(g.proposed ? [fact(t("fact.proposed"), esc(enums.budgets.map((k) => `${t(`budget.${k}`)} ${g.proposed[k]}`).join(", ")))] : []),
    ...(g.interactions ? [fact(t("fact.interactions"), t("interactions.left", g.interactions))] : []),
    ...(row.audit?.independence ? [fact(t("fact.independence"), t(`independence.${row.audit.independence}`))] : []),
    ...(detail.author ? [fact(t("fact.author"), esc(detail.author))] : []),
    ...(detail.auditor ? [fact(t("fact.auditor"), esc(detail.auditor))] : []),
    ...(a?.findings.length ? [fact(t("fact.findings"), list(a.findings.map((x) => `<span>${esc(x)}</span>`)))] : []),
  ];
  return section("governance", t("governance.title"), `<dl class="ev-facts flex flex-col gap-xs">${facts.join("")}</dl>`);
}

/** The workspace for the selected run. `ctx`: row, project, detail (the run document or null), notice (copy key), attempts
 * ({row, detail}[], the family in order), claim (the selected claim id or null), href(selection) -> url. */
export function runHtml(ctx) {
  const { row, detail, href } = ctx;
  const claims = row.claims === null ? `<p class="text-s">${t("claims.unread")}</p>` : claimsHtml(ctx);
  return `<article class="flex flex-col gap-m" aria-label="${t("run.label")}">
    ${headerHtml(ctx, href)}
    ${noticeHtml(ctx.notice)}
    ${nowHtml(ctx)}
    ${changedHtml(ctx)}
    ${section("claims", t("claims.title"), claims)}
    ${row.claims?.length ? section("support", t("support.title"), supportHtml(ctx)) : ""}
    ${governanceHtml(ctx)}
    ${section("attempts", t("attempts.title"), attemptsHtml(ctx))}
    ${detail?.steps ? section("history", t("history.title"), historyHtml(ctx)) : ""}
  </article>`;
}
