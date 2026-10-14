import { EvElement, define, t } from "../core/element.js";
import { noticeHtml, sheetHtml } from "../views/sheet.js";

/**
 * The run sheet beside the list. Before the sheet arrives it shows the run's line from the list (when it has one) and says it
 * is loading; if it cannot arrive it says why and offers to ask again.
 * @element ev-sheet
 * @prop {object|null} data - a sheet context (views/sheet.js), or `{pending: true, notice}` before the sheet is here
 */
class EvSheet extends EvElement {
  render() {
    const d = this.data;
    if (!d) return "";
    if (d.pending) {
      return `<div class="flex flex-col gap-s"><div class="flex justify-end"><button type="button" class="btn" data-variant="ghost" data-size="sm" data-close>${t("sheet.close")}</button></div>
        <h1 class="text-l font-semibold" tabindex="-1" data-title>${t(d.notice ? "sheet.unavailable" : "sheet.loading")}</h1>${noticeHtml(d.notice)}</div>`;
    }
    return sheetHtml(d);
  }
}
define("ev-sheet", EvSheet);
