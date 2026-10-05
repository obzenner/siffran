import { EvElement, define, esc, t } from "../core/element.js";

/**
 * What the agent was asked to do: quiet context, never a title. Two lines; "Show all" expands in place and
 * "Show less" folds it again.
 * @element ev-goal
 * @attr {string} text - the goal as written
 */
class EvGoal extends EvElement {
  static observedAttributes = ["text"];
  constructor() {
    super();
    this.addEventListener("click", (e) => { if (e.target.closest("[data-fold]")) this.querySelector("details").open = false; });
  }
  render() {
    const text = esc(this.getAttribute("text") ?? "");
    return `<details class="group max-w-prose text-s text-muted-foreground">
      <summary class="flex cursor-pointer list-none flex-col items-start gap-3xs text-left">
        <span class="text-xs font-medium tracking-wide uppercase">${t("goal.label")}</span>
        <span class="line-clamp-2 group-open:hidden">${text}</span>
        <span class="text-xs text-foreground underline underline-offset-4 group-open:hidden">${t("goal.more")}</span>
      </summary>
      <p class="pt-3xs">${text}</p>
      <button type="button" class="pt-3xs text-xs text-foreground underline underline-offset-4" data-fold>${t("goal.less")}</button>
    </details>`;
  }
}
define("ev-goal", EvGoal);
