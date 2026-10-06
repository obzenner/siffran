import { EvElement, define, esc, t } from "../core/element.js";

/**
 * One research record of a claim (ADR-0067): its source and how it stands at a glance, and on click its quote.
 * The word says how the record is verified: checked against the file by the service, confirmed or rejected by the
 * human, or (pending) where its confirmation goes: the auditor, the human, or no one on this host. Opened, it
 * gives the quote, what kind of source it is, why no spike could establish an observation, and the human's note.
 * It words what the observer says; it derives nothing (X1).
 * @element ev-record
 * @prop {{source:string,source_kind:string,quote:string,outcome:string,class:string,status:string,route:?string,
 *         retrieve:boolean,spike_unavailable:?string,note:?string}} data - a `records[]` row of the run document
 */
class EvRecord extends EvElement {
  render() {
    const r = this.data;
    if (!r) return "";
    const [domain, value] = r.status === "pending" ? ["route", r.route] : ["verification", r.status];
    const note = r.note ?? "";
    return `<details class="border-b border-border py-xs last:border-b-0">
      <summary class="flex cursor-pointer flex-col items-start gap-2xs text-s text-foreground">
        <ev-state domain="${esc(domain)}" value="${esc(value)}"></ev-state>
        <span class="min-w-0 break-all font-mono text-xs text-muted-foreground">${esc(r.source)}</span>
      </summary>
      <div class="flex flex-col gap-xs pt-xs text-s">
        <blockquote class="border-l-2 border-border pl-s text-foreground"><ev-more text="${esc(r.quote)}"></ev-more></blockquote>
        <p class="text-xs text-muted-foreground">${t(`record.class.${r.class}`)}</p>
        ${r.spike_unavailable ? `<div class="flex flex-col gap-3xs text-xs"><span class="text-muted-foreground">${t("record.spike")}</span><ev-more text="${esc(r.spike_unavailable)}"></ev-more></div>` : ""}
        ${note ? `<div class="flex flex-col gap-3xs text-xs"><span class="text-muted-foreground">${t(r.status === "rejected_by_human" ? "record.note.rejected" : "record.note")}</span><ev-more text="${esc(r.note)}"></ev-more></div>` : ""}
      </div>
    </details>`;
  }
}
define("ev-record", EvRecord);
