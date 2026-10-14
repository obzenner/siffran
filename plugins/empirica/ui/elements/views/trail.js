// What happened in a run, in order, and the files that changed since its spikes ran. One line each, in words.
import { clip, count, esc, t } from "../core/element.js";
import { chip, claimLink, list } from "./parts.js";

/** One step: its number, its kind and outcome, the claim it is about (said when it was reworded or is gone) and its command. */
function stepHtml(s, row, href) {
  const about = s.claim && (row.claims ?? []).find((c) => c.id === s.claim);
  const claim = !s.claim ? "" : about && s.standing !== "gone" ? `${claimLink(row, s.claim, href)}${s.standing === "replaced" ? ` <span class="text-xs text-muted-foreground">${t("history.reworded")}</span>` : ""}` : `<span class="text-xs text-muted-foreground">${t("history.gone")}</span>`;
  return `<span class="flex flex-wrap items-baseline gap-x-xs gap-y-3xs"><span class="w-m tabular-nums text-xs text-muted-foreground">${s.n}</span>${chip("step", s.kind)}${s.outcome ? chip("outcome", s.outcome) : ""}${s.terminal ? `<span class="text-xs">${t("history.terminal")}</span>` : ""}${claim}${s.command ? `<code class="break-all font-mono text-xs">${esc(clip(s.command, 120))}</code>` : ""}</span>`;
}

/** The steps in order; those that only record how the run was kept (the core says which) are folded away, the numbers kept. */
export function historyHtml({ row, detail, href }) {
  const shown = detail.steps.filter((s) => !s.bookkeeping), kept = detail.steps.filter((s) => s.bookkeeping);
  const folded = kept.length ? `<details class="pt-xs"><summary class="cursor-pointer text-xs text-muted-foreground">${count("history.bookkeeping", kept.length)}</summary><div class="pt-xs">${list(kept.map((s) => stepHtml(s, row, href)))}</div></details>` : "";
  return `${list(shown.map((s) => stepHtml(s, row, href)))}${folded}`;
}

/** The files whose observation differs from what a spike ran against: a separate fact from the run's recorded end. */
export function changedHtml({ detail }) {
  if (!detail?.stale?.length) return "";
  return `<h3 class="text-s font-medium">${t("changed.title")}</h3><p class="text-xs text-muted-foreground">${t("changed.hint")}</p>${list(detail.stale.map((f) => `<span class="flex flex-wrap items-baseline gap-xs"><code class="break-all font-mono text-xs">${esc(f.path)}</code>${chip("file", f.state)}</span>`))}`;
}
