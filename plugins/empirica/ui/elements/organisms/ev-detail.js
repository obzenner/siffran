import { EvElement, define, esc, icon, t } from "../core/element.js";

/**
 * Detail panel content (level 2 + a folded level 3), shown in the side panel of an ev-split. Close emits ev-close.
 * With no data it says what to click to see a detail (the panel can stay open with nothing selected).
 * Long prose (a claim, a goal) is body text, never a heading: `title` is only for short names (a file, an outcome).
 * @element ev-detail
 * `domain`/`value` give the state chip (omitted for what has no state, e.g. a project). Each of `parts` is a titled
 * section: markup (`html`), or elements that take data (`children`, e.g. a run's history, its files), handed their
 * data after render.
 * @prop {{domain?:string,value?:string,kicker:string,title?:string,mono?:boolean,body?:string,goal?:string,
 *         facts:{label:string,value?:string,html?:string}[],parts?:{label:string,html?:string,children?:{tag:string,data:object}[]}[],
 *         technical?:object}} data
 */
const TITLE = { sans: "text-m font-medium text-foreground", mono: "break-all font-mono text-s font-medium text-foreground" };
class EvDetail extends EvElement {
  constructor() {
    super();
    this.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) this.emit("ev-close", {}); });
  }
  populate() {
    (this.data?.parts ?? []).forEach((p, i) => {
      const kids = this.querySelector(`[data-part="${i}"]`).children;
      (p.children ?? []).forEach((c, j) => { kids[j].data = c.data; });
    });
  }
  render() {
    const d = this.data;
    if (!d) return `<p class="p-l text-s text-muted-foreground" data-empty>${t("panel.empty")}</p>`;
    return `<div class="flex w-full flex-col gap-l p-l">
      <div class="flex items-center justify-between gap-s">
        <span class="text-xs font-medium tracking-wide text-muted-foreground uppercase">${esc(d.kicker)}</span>
        <button type="button" class="btn" data-variant="ghost" data-size="icon-sm" data-close aria-label="${t("panel.close")}">${icon("close")}</button>
      </div>
      <div class="flex flex-col gap-s">
        ${d.domain ? `<div><ev-state domain="${esc(d.domain)}" value="${esc(d.value)}" variant="chip"></ev-state></div>` : ""}
        ${d.title ? `<p class="${d.mono ? TITLE.mono : TITLE.sans}">${esc(d.title)}</p>` : ""}
        ${d.body ? `<p class="text-s leading-relaxed text-foreground">${esc(d.body)}</p>` : ""}
        ${d.goal ? `<ev-goal text="${esc(d.goal)}"></ev-goal>` : ""}
      </div>
      <dl class="flex flex-col gap-s border-t border-border pt-l text-s">
        ${(d.facts ?? []).map((f) => `<div class="flex gap-l"><dt class="w-1/3 shrink-0 text-muted-foreground">${esc(f.label)}</dt><dd class="min-w-0 flex-1 text-foreground">${f.html ?? esc(f.value)}</dd></div>`).join("")}
      </dl>
      ${(d.parts ?? []).map((p, i) => `<section class="flex flex-col gap-s border-t border-border pt-l" aria-label="${esc(p.label)}">
        <h3 class="text-s font-medium text-foreground">${esc(p.label)}</h3>
        <div class="flex flex-col text-s text-foreground" data-part="${i}">${p.html ?? p.children.map((c) => `<${c.tag}></${c.tag}>`).join("")}</div>
      </section>`).join("")}
      ${d.technical ? `<details class="text-xs text-muted-foreground"><summary class="cursor-pointer">${t("panel.technical")}</summary><pre class="mt-s overflow-x-auto rounded-m bg-muted p-s font-mono">${esc(JSON.stringify(d.technical, null, 2))}</pre></details>` : ""}
    </div>`;
  }
}
define("ev-detail", EvDetail);
