import { EvElement, define, t } from "../core/element.js";
import { listHtml } from "../templates/runs.js";

/**
 * The run list: the runs that match the search, under the core's attention groups. Rows are links (the page moves the
 * selection when one is followed); a run's locator has its own copy button beside the link.
 * @element ev-runs
 * @prop {{groups:object[], href:Function, selected:?string, attemptsOf:Function, empty:string}} data
 */
class EvRuns extends EvElement {
  render() {
    const d = this.data;
    if (!d) return "";
    return d.groups.length ? listHtml(d.groups, d) : `<p class="p-s text-s text-muted-foreground">${t(d.empty)}</p>`;
  }
}
define("ev-runs", EvRuns);
