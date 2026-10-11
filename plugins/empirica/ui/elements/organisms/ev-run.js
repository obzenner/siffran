import { EvElement, define, t } from "../core/element.js";
import { runHtml } from "../templates/run.js";

/**
 * The run workspace (templates/run.js). Its research records are elements that take structured data, handed to them
 * after each render.
 * @element ev-run
 * @prop {object} data - the workspace context of templates/run.js (null shows what to do)
 */
class EvRun extends EvElement {
  render() {
    return this.data ? runHtml(this.data) : `<p class="p-m text-s text-muted-foreground">${t("run.none")}</p>`;
  }
  populate() {
    const own = this.data?.detail?.claims?.find((c) => c.id === this.data.claim);
    for (const el of this.querySelectorAll("ev-record[data-record]")) el.data = own?.records[Number(el.dataset.record)];
  }
}
define("ev-run", EvRun);
