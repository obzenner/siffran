import { EvElement, define, t } from "../core/element.js";
import { calendarHtml, groupsHtml, stripHtml } from "../views/inbox.js";

/**
 * The inbox home: what the period held (the value strip), the projects × days calendar, and the runs in groups, the ones
 * that need a human first, each group capped with a link to all of it.
 * @element ev-inbox
 * @prop {{summary:object, place:object, loaded:Map<string,object[]>, ctx:object, go:Function, statGo:Function, periodHref:Function, calendarGo:Function, nowMs:number, empty:string}} data
 */
class EvInbox extends EvElement {
  render() {
    const d = this.data;
    if (!d) return "";
    return `<div class="flex flex-col gap-l">
      <h1 class="sr-only">${t("inbox.title")}</h1>
      ${stripHtml(d.summary, d.place, { go: d.statGo, periodHref: d.periodHref })}
      ${calendarHtml(d.summary, d.place, { nowMs: d.nowMs, go: d.calendarGo })}
      <div class="flex flex-col gap-m">${groupsHtml(d.summary, d.loaded, { ...d.ctx, empty: d.empty })}</div>
    </div>`;
  }
}
define("ev-inbox", EvInbox);
