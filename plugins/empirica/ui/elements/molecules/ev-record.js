import { EvElement, define, esc, joined, t } from "../core/element.js";

/**
 * One research record of a claim (ADR-0067), in place: its source and how it stands, and its quote. The word says how
 * the record is verified: checked against the file by the service, confirmed or rejected by the human, or (pending)
 * who it waits on and where. It words what the observer says; it decides nothing.
 * @element ev-record
 * @prop {{source:string,source_kind:string,quote:string,outcome:string,class:string,status:string,wait:?{who:string,where:string},
 *         retrieve:boolean,spike_unavailable:?string,note:?string}} data - a `records[]` row of the run document
 */
class EvRecord extends EvElement {
  render() {
    const r = this.data;
    if (!r) return "";
    const standing = r.status === "pending" && r.wait ? joined(t(`who.${r.wait.who}`), t(`where.${r.wait.where}`)) : "";
    return `<article class="flex flex-col gap-xs border-b border-border py-s last:border-b-0">
      <div class="flex flex-wrap items-center gap-xs">
        <ev-state domain="verification" value="${esc(r.status)}"></ev-state>
        ${standing ? `<span class="text-xs text-muted-foreground">${esc(standing)}</span>` : ""}
        <ev-state domain="outcome" value="${esc(r.outcome)}"></ev-state>
      </div>
      <span class="min-w-0 break-all font-mono text-xs text-muted-foreground">${esc(r.source)}</span>
      <blockquote class="border-l-2 border-border pl-s"><ev-more text="${esc(r.quote)}"></ev-more></blockquote>
      <p class="text-xs text-muted-foreground">${t(`record.class.${r.class}`)}</p>
      ${r.spike_unavailable ? `<div class="flex flex-col gap-3xs text-xs"><span class="text-muted-foreground">${t("record.spike")}</span><ev-more text="${esc(r.spike_unavailable)}"></ev-more></div>` : ""}
      ${r.note ? `<div class="flex flex-col gap-3xs text-xs"><span class="text-muted-foreground">${t(r.status === "rejected_by_human" ? "record.note.rejected" : "record.note")}</span><ev-more text="${esc(r.note)}"></ev-more></div>` : ""}
    </article>`;
  }
}
define("ev-record", EvRecord);
