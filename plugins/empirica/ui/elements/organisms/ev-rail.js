import { EvElement, define } from "../core/element.js";
import { railHtml } from "../views/inbox.js";

/**
 * The rail: Everything, a bucket per standing the store has runs in, and the projects, each with its count and each a link
 * to the runs it names.
 * @element ev-rail
 * @prop {{summary:object, place:object, go:(filters:object)=>string}} data
 */
class EvRail extends EvElement {
  render() {
    const d = this.data;
    return d ? railHtml(d.summary, d.place, d.go) : "";
  }
}
define("ev-rail", EvRail);
