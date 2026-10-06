import { EvElement, count, define, esc, icon, t } from "../core/element.js";

/**
 * The view's filters (VIEW.md R5), declared in catalogue.json (`filters`): a field filter offers the values present
 * in the data as chips that wrap (the project filter is one choice from a list); a flag filter is one chip, the
 * flags on one line after their heading. A chip
 * is a checkbox: chosen, it is outlined, bold and checked, so it never relies on colour. Above them, what is showing
 * and a clear button. Compact so the column fits beside the view without scrolling. The page applies the choice (app/filter.js) and gives back the counts.
 * The form is built once per data set and patched in place, so focus stays where it is.
 * Emits ev-filter {choice}: field filter id → chosen values, flag filter id → true when set.
 * @element ev-filters
 * @prop {{filters:object[], options:Object<string,{value:string,label:string}[]>}} data - options per field filter
 * @prop {object} choice - the current choice (patched into the form)
 * @prop {{projects:number,runs:number,claims:number}} counts - what is showing
 */
const LEGEND = "text-xs font-medium text-muted-foreground";
const CHIP = "relative inline-flex cursor-pointer items-center gap-3xs rounded-full border border-border px-xs py-3xs text-xs text-foreground hover:bg-muted has-checked:border-ring has-checked:bg-muted has-checked:font-medium has-focus-visible:outline-2 has-focus-visible:outline-ring";
const chip = (attrs, label) => `<label class="${CHIP}"><input type="checkbox" class="peer sr-only" ${attrs}><span class="hidden peer-checked:inline-flex">${icon("chosen", "xs")}</span>${esc(label)}</label>`;

class EvFilters extends EvElement {
  #choice = {};
  #counts = null;
  set choice(c) { this.#choice = c ?? {}; this.#patch(); }
  get choice() { return this.#choice; }
  set counts(c) { this.#counts = c; this.#patch(); }
  constructor() {
    super();
    this.addEventListener("change", () => this.emit("ev-filter", { choice: this.#read() }));
    this.addEventListener("click", (e) => { if (e.target.closest("[data-clear]")) this.emit("ev-filter", { choice: {} }); });
  }
  #read() {
    const choice = {};
    for (const f of this.data.filters) {
      if (f.control === "flag") { if (this.querySelector(`[data-filter="${f.id}"]`)?.checked) choice[f.id] = true; continue; }
      const sel = this.querySelector(`select[data-filter="${f.id}"]`);
      const vs = sel ? [sel.value].filter(Boolean) : [...this.querySelectorAll(`input[data-filter="${f.id}"]:checked`)].map((x) => x.value);
      if (vs.length) choice[f.id] = vs;
    }
    return choice;
  }
  render() {
    const d = this.data;
    if (!d) return "";
    const field = (f) => {
      const opts = d.options[f.id] ?? [];
      if (!opts.length) return "";
      if (f.control === "select") return `<label class="flex flex-col gap-2xs"><span class="${LEGEND}">${t(`filter.${f.id}`)}</span>
        <select class="select w-full" data-size="sm" data-filter="${f.id}"><option value="">${t("filter.project.all")}</option>${opts.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}</select></label>`;
      return `<fieldset><legend class="${LEGEND} pb-3xs">${t(`filter.${f.id}`)}</legend>
        <div class="flex flex-wrap gap-2xs">${opts.map((o) => chip(`data-filter="${f.id}" value="${esc(o.value)}"`, o.label)).join("")}</div></fieldset>`;
    };
    const flags = d.filters.filter((f) => f.control === "flag");
    return `<form class="flex flex-col gap-xs" aria-label="${t("filter.label")}">
      <div class="flex items-center justify-between gap-xs border-b border-border pb-xs">
        <p class="text-xs text-muted-foreground" data-showing aria-live="polite"></p>
        <button type="button" class="btn shrink-0" data-size="sm" data-variant="ghost" data-clear>${t("filter.clear")}</button>
      </div>
      ${d.filters.filter((f) => f.control !== "flag").map(field).join("")}
      <div role="group" aria-label="${t("filter.flags")}" class="flex flex-wrap items-center gap-2xs"><span class="${LEGEND} pr-2xs" aria-hidden="true">${t("filter.flags")}</span>${flags.map((f) => chip(`data-filter="${f.id}"`, t(`filter.${f.id}`))).join("")}</div>
    </form>`;
  }
  populate() { this.#patch(); }
  #patch() {
    if (!this.firstElementChild || !this.data) return;
    const c = this.#choice;
    for (const f of this.data.filters) {
      if (f.control === "flag") { const x = this.querySelector(`[data-filter="${f.id}"]`); if (x) x.checked = c[f.id] === true; continue; }
      const sel = this.querySelector(`select[data-filter="${f.id}"]`);
      if (sel) sel.value = c[f.id]?.[0] ?? "";
      for (const x of this.querySelectorAll(`input[data-filter="${f.id}"]`)) x.checked = (c[f.id] ?? []).includes(x.value);
    }
    const k = this.#counts;
    this.querySelector("[data-showing]").textContent = k ? t("filter.showing", { projects: count("count.projects", k.projects), runs: count("count.runs", k.runs), claims: count("count.claims", k.claims) }) : "";
    this.querySelector("[data-clear]").disabled = !Object.keys(c).length;
  }
}
define("ev-filters", EvFilters);
