// A run's claims, each expanding in place to its proof (sources and quotes, the human's answer, spike commands, the audit's
// coverage), and the support graph as an indented list. Words only: the states, the scope (Required or Deferred) and the
// assurance are the core's; the blocker's sentence is the registry's.
import { clip, esc, joined, t, word } from "../core/element.js";
import { supportTree } from "../core/tree.js";
import { chip, claimLink, fact, link, list } from "./parts.js";

/** Counts worth saying: only the ones above zero ("No evidence" already says none). */
function countsHtml(own) {
  return own.research > 0 ? fact(t("fact.research.supporting"), String(own.research)) : "";
}

/** What one spike was bound to, in full: the gate it ran under and each file's path and SHA-256, for the reader who checks the
 * bytes. A disclosure, so the compact line stays compact. */
function bindingsHtml(p) {
  const file = (f) => `<span class="flex flex-col"><code class="break-all font-mono">${esc(f.path)}</code><code class="break-all font-mono text-muted-foreground">${esc(f.sha256)}</code></span>`;
  return `<details class="text-xs"><summary class="cursor-pointer text-muted-foreground" data-bindings>${t("spike.bindings")}</summary>
    <div class="flex flex-col gap-xs pt-xs"><span>${t("spike.gate", { gate: t(`spike.outcome.${p.gate}`) })}</span>${p.files.map(file).join("")}</div></details>`;
}

/** The spikes that were run for a claim, each as the core states it: the command, how it ended, the files it was bound to
 * and whether it still holds; and that the command's output is not kept. */
function spikesHtml(proof) {
  if (!proof.length) return "";
  const one = (p) => `<div class="flex flex-col gap-3xs"><code class="break-all font-mono text-xs">${esc(p.command)}</code>
    <span class="text-xs text-muted-foreground">${joined(t(`spike.outcome.${p.outcome}`), t("spike.exit", { code: p.exit_code }), t(`spike.${p.state}`))}</span>
    <span class="text-xs text-muted-foreground">${t("spike.files")} ${p.files.map((f) => `<code class="break-all font-mono">${esc(f.path)}</code>`).join(", ")}</span>${bindingsHtml(p)}</div>`;
  return fact(t("fact.proof"), `${list(proof.map(one))}<p class="text-xs text-muted-foreground">${t("spike.output.not_retained")}</p>`);
}

/** A needs-decision claim's decision: where it stands, where it is asked, and the human's answer. */
function decisionHtml(d) {
  return fact(t("fact.decision"), chip("decision", d.status))
    + (d.where ? fact(t("fact.asked"), t(`where.${d.where}`)) : "")
    + (d.answer ? fact(t("fact.answer"), `<ev-more text="${esc(d.answer)}"></ev-more>`) : "");
}

/** The body of an open claim: everything the document holds about it, together. */
function proofHtml(c, own, ctx) {
  const { row, href } = ctx;
  if (!own) return `<p class="text-s text-muted-foreground">${t(ctx.notice ? "claim.unavailable" : "detail.loading")}</p>`;
  const waits = c.blocker_claim && c.blocker_claim !== c.id ? claimLink(row, c.blocker_claim, href) : "";
  return `<div class="flex flex-col gap-s">
    <ev-more text="${esc(c.text)}"></ev-more>
    <dl class="ev-facts flex flex-col gap-xs">
      ${fact(t("fact.kind"), t(`kind.${c.kind}`))}
      ${own.blocker_message ? fact(t("fact.blocker"), esc(own.blocker_message)) : ""}
      ${waits ? fact(t("fact.waits"), waits) : ""}
      ${fact(t("fact.evidence"), chip("evidence", c.evidence))}
      ${fact(t("fact.review"), chip("review", c.review))}
      ${fact(t("fact.assurance"), chip("assurance", c.assurance))}
      ${row.audit?.independence ? fact(t("fact.independence"), t(`independence.${row.audit.independence}`)) : ""}
      ${own.decision ? decisionHtml(own.decision) : ""}
      ${countsHtml(own)}
      ${spikesHtml(own.proof)}
    </dl>
    ${own.records.length ? `<div class="flex flex-col"><h3 class="text-s font-medium">${t("fact.sources")}</h3>${own.records.map((_, i) => `<ev-record data-record="${i}"></ev-record>`).join("")}</div>` : ""}
  </div>`;
}

/** The claims as a list of disclosures: one open at a time, the selected one (the URL's `claim`). */
export function claimsHtml(ctx) {
  const { row, detail, claim, href } = ctx;
  if (!row.claims.length) return `<p class="text-s text-muted-foreground">${t("claims.none")}</p>`;
  return `<ul class="flex flex-col">${row.claims.map((c) => {
    const own = detail?.claims.find((x) => x.id === c.id);
    const open = c.id === claim;
    return `<li class="border-b border-border py-xs" data-claim-row="${esc(c.id)}">
      <details ${open ? "open" : ""}>
        <summary class="flex cursor-pointer flex-col items-start gap-2xs rounded-m p-xs text-left hover:bg-muted" data-claim="${esc(c.id)}" data-href="${esc(href({ run: row.key, claim: open ? null : c.id }))}" ${open ? 'aria-current="true"' : ""}>
          <span class="text-s font-medium">${esc(clip(c.text, 160))}</span>
          <span class="flex flex-wrap items-center gap-x-s gap-y-3xs"><span class="text-xs font-medium">${t(c.gating ? "scope.required" : "scope.deferred")}</span>${chip("claim", c.state)}${chip("assurance", c.assurance)}</span>
          ${own?.blocker_message ? `<span class="text-xs text-muted-foreground">${esc(own.blocker_message)}</span>` : ""}
        </summary>
        <div class="px-xs pt-xs">${open ? proofHtml(c, own, ctx) : ""}</div>
      </details>
    </li>`;
  }).join("")}</ul>`;
}

/** The run's support graph, labelled: each claim's supporters beneath it, every claim a link to it. */
export function supportHtml({ row, href }) {
  const byId = new Map(row.claims.map((c) => [c.id, c]));
  const render = (nodes) => `<ul class="flex flex-col gap-2xs ${nodes === root ? "" : "ml-m border-l border-border pl-s"}">${nodes.map((n) => {
    const c = byId.get(n.id);
    return `<li>${link(href({ run: row.key, claim: n.id }), clip(c.text, 100))} <span class="text-xs text-muted-foreground">${esc(word("claim", c.state))}${n.again ? ` · ${t("support.again")}` : ""}</span>${n.children.length ? render(n.children) : ""}</li>`;
  }).join("")}</ul>`;
  const root = supportTree(row.root, row.claims, row.edges ?? []);
  return `<p class="text-xs text-muted-foreground">${t("support.hint")}</p>${render(root)}`;
}
