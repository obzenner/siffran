// Mechanical convention checks (CONVENTIONS 0.6, C1, S1, T1, E7, G1). `audit(inputs)` is pure, so
// tests/audit.test.mjs can prove each check with negative controls; the CLI reads the tree and exits 1 on findings.
//
// What it checks, and its limits (keep this list honest):
// - C1 copy keys: every key is used (a complete string literal in code, a state copy key, or an expansion of a
//   declared dynamic family); every literal t("…"), label="…" and family expansion exists. A template t(`…${}…`)
//   that matches no declared family is itself a finding.
// - C1 literal text: words in markup text nodes and in aria-label/title/placeholder/alt attribute literals.
//   <noscript> and the static <title> are exempt (no JS to look up copy).
// - 0.6 icons: every icon meaning is used and every used meaning exists.
// - T1: no Tailwind arbitrary value (`text-[9px]`, `h-[36rem]`) in any string: raw values live in tokens.json.
// - E7: no inline style attributes; no class value glued to an interpolation (`text-${x}`, `${x}-foo`).
// - G1: every defined element appears in the gallery as a tag or a created-by-name string (comments ignored), or
//   inside a template (elements/templates/) that the gallery shows.
// - Levels (L3): an import graph by atomic level. core imports core; atoms core; molecules core and atoms;
//   organisms core, atoms and molecules; layout core; templates core and templates; elements/index.js any element;
//   pages (app/, gallery/) the elements' index, core, templates and their own folder. Nothing imports upward.
// - T1 raw values: no literal `12px`, `150ms`, hex colour, bare timer delay or unitless SVG number in code; lengths,
//   durations and colours are tokens, geometry is named (view-layout.js `U`, marks.js `R`/`STROKE`) or catalogue data.
// - T8 unused tokens: every token in tokens.json is used by a class, a var() or a declared reference in code or app.css.
// - E10 observed attributes: every attribute an element observes is set somewhere other than its own file.
// - V5 icons: within one vocabulary no two values share a glyph, unless one declares `alias` of the other.
// - G4 gallery states: every vocabulary in states.json is shown by the gallery (catalogue `gallery_state_domains`, or
//   `gallery_other_domains` for those it shows another way).
// - C4 placeholders: the variables a t()/plain()/count() call gives are exactly the {placeholders} of its copy.
// It does not check levels of detail beyond these patterns, or runtime-built strings.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Block, HTML and line comments, including trailing ones (`x; // "ev-tag"`). A `//` only starts a comment after
// whitespace or punctuation, so URLs (`https://`) survive.
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/<!--[\s\S]*?-->/g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1");
const entries = (o) => Object.entries(o).filter(([k]) => !k.startsWith("$"));

/** Dynamic copy families: template pattern (each ${…} normalised to *) -> the keys it can produce. */
function families(states, cat) {
  const dom = (d) => Object.keys(states[d] ?? {});
  const F = {
    "*.label": [], "*.hint": [],  // state copy keys, checked through stateKeys
    "gallery.*": cat.gallery_sections.map((v) => `gallery.${v}`),
    "gallery.*.hint": cat.gallery_sections.map((v) => `gallery.${v}.hint`),
    "gallery.states.*": cat.gallery_state_domains.map((v) => `gallery.states.${v}`),
    "kind.*": cat.kinds.map((v) => `kind.${v}`),
    "independence.*": cat.independence.map((v) => `independence.${v}`),
    "control.mode.*": cat.control_modes.map((v) => `control.mode.${v}`),
    "control.approval.*": cat.approvals.map((v) => `control.approval.${v}`),
    "budget.*": cat.budgets.map((v) => `budget.${v}`),
    "legend.of.*": cat.legend_domains.map((v) => `legend.of.${v}`),
    "filter.*": cat.filters.map((f) => `filter.${f.id}`),
    "*.*": cat.filters.filter((f) => f.copy).flatMap((f) => cat[f.values].map((v) => `${f.copy}.${v}`)),
    "record.class.*": cat.record_classes.map((v) => `record.class.${v}`),
    "outcome.*": dom("run").map((v) => `outcome.${v}`),
    "outcome.empty.*": dom("run").map((v) => `outcome.empty.${v}`),
    "view.empty.*": cat.data_sources.map((v) => `view.empty.${v}`),
    "app.data.*": cat.data_sources.map((v) => `app.data.${v}`),
    "tone.*": cat.tones.map((v) => `tone.${v}`),
  };
  return F;
}

/** Template literals passed to t(`…`), plain(`…`) or count(`…`) (`name` picks the function), with each ${…}
 * (balanced) replaced by *. */
function templates(src, name = "t") {
  const out = [], head = `${name}(\``;
  for (let i = src.indexOf(head); i !== -1; i = src.indexOf(head, i + 1)) {
    if (/[\w$]/.test(src[i - 1] ?? "")) continue;
    let j = i + head.length, depth = 0, norm = "";
    while (j < src.length && !(src[j] === "`" && depth === 0)) {
      if (src.startsWith("${", j) && depth === 0) { depth = 1; norm += "*"; j += 2; continue; }
      if (depth) { if (src[j] === "{") depth++; else if (src[j] === "}") depth--; j++; continue; }
      norm += src[j++];
    }
    out.push(norm);
  }
  return out;
}

/** Values of class="…" / class='…' attributes, with interpolations kept, scanning ${…} nesting so inner quotes
 * don't end them. */
function classValues(src) {
  const out = [];
  for (const m of src.matchAll(/\bclass=(["'])/g)) {
    const q = m[1];
    let j = m.index + m[0].length, depth = 0, v = "";
    while (j < src.length && !(src[j] === q && depth === 0)) {
      if (src.startsWith("${", j)) depth++;
      else if (src[j] === "}" && depth) depth--;
      v += src[j++];
    }
    out.push(v);
  }
  return out;
}

const LEVELS = {
  core: ["core"], atoms: ["core"], molecules: ["core", "atoms"], organisms: ["core", "atoms", "molecules"], layout: ["core"],
  templates: ["core", "templates"], index: ["core", "atoms", "molecules", "organisms", "layout", "templates", "molecule", "index"],
};
/** The level of a file: elements/<level>/…, elements/index.js, or a page folder. */
function levelOf(f) {
  const parts = f.split(path.sep);
  if (parts[0] === "elements") return parts.length === 2 ? "index" : parts[1];
  return parts[0];
}
/** The calls `name("key", { a, b: 1 })` with a literal object: [key, [variable names]]. */
function callVars(src, name) {
  const out = [];
  for (const m of src.matchAll(new RegExp(`\\b${name}\\(\\s*(["'])([^"']+)\\1\\s*(?:,\\s*(?:[^,{()]+,\\s*)?\\{)`, "g"))) {
    let j = m.index + m[0].length, depth = 1, body = "";
    while (j < src.length && depth) { const ch = src[j++]; if ("{([".includes(ch)) depth++; if ("})]".includes(ch)) depth--; if (depth) body += ch; }
    const names = []; let d = 0, token = "";
    for (const ch of body + ",") {
      if ("{([".includes(ch)) d++; else if ("})]".includes(ch)) d--;
      if (ch === "," && d === 0) { const mm = /^\s*([A-Za-z_$][\w$]*)\s*(?::|$)/.exec(token); if (mm) names.push(mm[1]); token = ""; } else token += ch;
    }
    out.push([m[2], names]);
  }
  return out;
}

export function audit({ code, copy, states, icons, catalogue, tokens = null, css = {} }) {
  const findings = [];
  const report = (rule, msg) => findings.push(`${rule}: ${msg}`);
  const clean = Object.fromEntries(Object.entries(code).map(([f, s]) => [f, stripComments(s)]));
  const all = Object.values(clean).join("\n");

  // C1 copy keys
  const literals = new Set([...all.matchAll(/["']([a-z][\w-]*(?:\.[\w-]+)+)["']/g)].map((m) => m[1]));
  const calls = new Set([...all.matchAll(/\b(?:t|plain)\(\s*(["'])([^"']+)\1/g)].map((m) => m[2]));
  // count(key, n) reads `key` and `key.one` (copy says "1 run" and "2 runs").
  const counted = new Set([...all.matchAll(/\bcount\(\s*(["'])([^"']+)\1/g)].map((m) => m[2]));
  for (const k of counted) { calls.add(k); calls.add(`${k}.one`); }
  const labelAttr = new Set([...all.matchAll(/\blabel="([a-z_.-]+)"/g)].map((m) => m[1]));
  const stateList = entries(states).flatMap(([, d]) => (d.copy ? [d] : entries(d).map(([, s]) => s)));
  const stateKeys = new Set(stateList.flatMap((s) => [`${s.copy}.label`, `${s.copy}.hint`]));
  const F = families(states, catalogue);
  const expanded = new Set();
  // copy-key-shaped template literals passed elsewhere (e.g. group(`gallery.states.${d}`)) expand their family too
  for (const m of all.matchAll(/`([a-z][\w-]*(?:\.(?:[\w-]+|\$\{[^}`]*\}))+)`/g)) {
    const norm = m[1].replace(/\$\{[^}`]*\}/g, "*");
    if (norm in F) for (const k of F[norm]) expanded.add(k);
  }
  const grouped = [["t", false], ["plain", false], ["count", true]];
  for (const [name, counts] of grouped) {
    for (const tpl of new Set(Object.values(clean).flatMap((src) => templates(src, name)))) {
      if (!tpl.includes("*")) { calls.add(tpl); if (counts) calls.add(`${tpl}.one`); continue; }
      if (!(tpl in F)) { report("C1 undeclared dynamic copy family", `${name}(\`${tpl}\`)`); continue; }
      for (const k of F[tpl]) { expanded.add(k); if (counts) expanded.add(`${k}.one`); }
    }
  }
  const used = (k) => literals.has(k) || stateKeys.has(k) || expanded.has(k) || calls.has(k);
  for (const [k] of entries(copy)) if (!used(k)) report("C1 unused copy key", k);
  for (const k of new Set([...calls, ...labelAttr, ...stateKeys, ...expanded])) if (!(k in copy)) report("C1 missing copy key", k);

  // C1 literal text in markup
  for (const [f, s] of Object.entries(clean)) {
    const body = s.replace(/<noscript>[\s\S]*?<\/noscript>/g, "").replace(/<title>[^<]*<\/title>/g, "");
    for (const m of body.matchAll(/>([^<>`]*?)</g)) {
      const text = m[1].replace(/\$\{[^}]*\}/g, "").replace(/&[a-z#0-9]+;/g, "");
      if (/[A-Za-z]{2,}/.test(text) && !/[{}()=;&|]/.test(text)) report("C1 literal text", `${f}: "${text.trim().slice(0, 40)}"`);
    }
    for (const m of body.matchAll(/\b(aria-label|title|placeholder|alt)=(["'])([^"'$]*[A-Za-z][^"'$]*)\2/g)) report("C1 literal attribute", `${f}: ${m[1]}="${m[3]}"`);
  }

  // icons
  const iconUse = new Set([
    ...[...all.matchAll(/\bicon\(\s*"([^"]+)"/g)].map((m) => m[1]),
    ...stateList.map((s) => s.icon),
    ...[...all.matchAll(/"needs-[a-z]+":\s*"([a-z-]+)"/g)].map((m) => m[1]),
  ]);
  for (const [k] of entries(icons)) if (!iconUse.has(k)) report("0.6 unused icon meaning", k);
  for (const k of iconUse) if (!(k in icons)) report("S1 missing icon meaning", k);

  // T1 raw values in classes (Tailwind arbitrary values), E7 inline styles and glued class interpolation
  for (const [f, s] of Object.entries(clean)) {
    for (const m of s.matchAll(/(?<![\w-])[a-z][a-z0-9:-]*-\[[^\]\s"'`]+\]/g)) report("T1 raw value in a class", `${f}: ${m[0]}`);
    if (/\sstyle\s*=\s*["']/.test(s)) report("E7 inline style attribute", f);
    for (const v of classValues(s)) if (/[\w:!-]\$\{|\}[\w-]/.test(v)) report("E7 class built by concatenation", `${f}: class="${v.slice(0, 60)}"`);
  }

  // G1 gallery coverage
  const defined = [...all.matchAll(/define\("(ev-[a-z-]+)"/g)].map((m) => m[1]);
  const gallery = Object.entries(clean).filter(([f]) => f.startsWith(`gallery${path.sep}`)).map(([, s]) => s).join("\n");
  // An element is covered when the gallery shows it, or when a TEMPLATE (elements/templates/) the gallery shows
  // renders it: the gallery then shows it inside the template, with real data.
  const shown = (src, tag) => src.includes(`<${tag}`) || src.includes(`"${tag}"`);
  const templateFiles = Object.entries(clean).filter(([f]) => f.startsWith(path.join("elements", "templates") + path.sep));
  const covered = new Set(defined.filter((tag) => shown(gallery, tag)));
  for (let grew = true; grew;) {
    grew = false;
    for (const [, src] of templateFiles) {
      const own = [...src.matchAll(/define\("(ev-[a-z-]+)"/g)].map((m) => m[1]);
      if (!own.some((tag) => covered.has(tag))) continue;
      for (const tag of defined) if (!covered.has(tag) && shown(src, tag)) { covered.add(tag); grew = true; }
    }
  }
  for (const tag of defined) if (!covered.has(tag)) report("G1 element missing from gallery", tag);

  // L3 levels
  for (const [f, src] of Object.entries(clean)) {
    const mine = levelOf(f);
    // static `import x from "./y.js"` and dynamic `import("./y.js")` alike
    for (const m of src.matchAll(/\bimport\s*\(\s*["'](\.[^"']+)["']\s*\)|\bimport\s+(?:[^"';]*?\s+from\s+)?["'](\.[^"']+)["']/g)) {
      m[1] = m[1] ?? m[2];
      if (m[1].endsWith(".json")) continue;
      const target = path.relative(".", path.join(path.dirname(f), m[1]));
      const theirs = levelOf(target);
      if (mine === theirs && mine !== "index") continue;
      if (mine === "app" || mine === "gallery") {
        if (!["app", "index", "core", "templates"].includes(theirs) && !(theirs === "gallery")) report("L3 page imports below the elements' index", `${f} -> ${target}`);
        continue;
      }
      if (theirs === "app" || theirs === "gallery" || !(LEVELS[mine] ?? []).includes(theirs)) report("L3 import against the levels", `${f} (${mine}) -> ${target} (${theirs})`);
    }
  }

  // T1 raw units in code, a bare delay, and unitless SVG geometry written as a literal
  for (const [f, src] of Object.entries(clean)) {
    for (const m of src.matchAll(/(?<![\w.#-])\d+(?:\.\d+)?(px|ms)\b/g)) report("T1 raw unit", `${f}: ${m[0]}`);
    // a colour is a token (a semantic utility or `var(--color-*)`), never a hex literal in code or app.css
    for (const m of src.matchAll(/(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g)) report("T1 raw colour", `${f}: ${m[0]}`);
    // a delay is a duration token (`--duration-*`) or catalogue data, not a number in a timer call (0 defers a tick)
    for (const m of src.matchAll(/\b(?:setTimeout|setInterval)\((?:[^()]|\([^()]*\))*?,\s*(\d+(?:\.\d+)?)\s*\)/g)) if (Number(m[1]) !== 0) report("T1 bare duration", `${f}: ${m[0].slice(0, 50)}`);
    // SVG geometry: no number written into an attribute (rx="6") or into an expression in one (${x + 4}); the numbers
    // are named (view-layout.js `U`, catalogue `picture`). 0, 1 and 2 (an origin, a unit, a half) are not geometry.
    const SVG_ATTR = /\b(?:rx|ry|r|x|y|cx|cy|x1|y1|x2|y2|dx|dy|width|height|stroke-width|stroke-dasharray|font-size)=(["'])((?:(?!\1)[\s\S])*)\1/g;
    for (const m of src.matchAll(SVG_ATTR)) {
      const value = m[2];
      if (/^\s*-?\d+(?:\.\d+)?(?:\s+-?\d+(?:\.\d+)?)*\s*$/.test(value) && !["0", "1", "2"].includes(value.trim()) && /<(?:rect|circle|path|text|svg|use|line|ellipse)\b[^>]*$/.test(src.slice(Math.max(0, m.index - 160), m.index))) report("T1 unitless SVG literal", `${f}: ${m[0]}`);
      else for (const e of value.matchAll(/\$\{([^}]*)\}/g)) for (const n of e[1].matchAll(/(?<![\w.$"'])(\d+(?:\.\d+)?)(?![\w"'])/g)) if (!["0", "1", "2"].includes(n[1]) && /<(?:rect|circle|path|text|svg|use|line|ellipse)\b[^>]*$/.test(src.slice(Math.max(0, m.index - 160), m.index))) report("T1 unitless SVG literal", `${f}: ${m[0].slice(0, 60)}`);
    }
  }

  for (const [f, src] of Object.entries(css)) if (!f.startsWith("vendor")) for (const m of stripComments(src).matchAll(/(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b/g)) report("T1 raw colour", `${f}: ${m[0]}`);

  // C4 placeholders supplied exactly
  const placeholders = (k) => [...new Set([...(copy[k] ?? "").matchAll(/\{(\w+)\}/g)].map((m) => m[1]))];
  for (const [f, src] of Object.entries(clean)) for (const name of ["t", "plain", "count"]) for (const [k, vars] of callVars(src, name)) {
    if (!(k in copy)) continue;
    const want = placeholders(k), got = new Set(name === "count" ? [...vars, "n"] : vars);
    for (const w of want) if (!got.has(w)) report("C4 placeholder not supplied", `${f}: ${name}("${k}") lacks {${w}}`);
    for (const g of vars) if (!want.includes(g)) report("C4 variable the copy does not use", `${f}: ${name}("${k}") gives ${g}`);
  }

  // E10 observed attributes set somewhere
  for (const [f, src] of Object.entries(clean)) {
    const m = /static observedAttributes = \[([^\]]*)\]/.exec(src);
    if (!m) continue;
    for (const attr of [...m[1].matchAll(/"([\w-]+)"/g)].map((x) => x[1])) {
      const set = new RegExp(`\\b${attr}=|(?:set|toggle|remove)Attribute\\(\\s*["']${attr}["']`);
      if (!Object.entries(clean).some(([g, s]) => g !== f && set.test(s))) report("E10 observed attribute never set outside its element", `${f}: ${attr}`);
    }
  }

  // V5 one glyph per value within a vocabulary
  for (const [domain, d] of entries(states)) {
    if (domain === "unknown" || d.copy) continue;
    const byIcon = new Map();
    for (const [value, st] of entries(d)) {
      if (!st || typeof st !== "object" || !st.icon) continue;
      const first = byIcon.get(st.icon);
      if (first && st.alias !== first && d[first]?.alias !== value) report("V5 two values share a glyph", `${domain}: ${first} and ${value} (${st.icon})`);
      else if (!first) byIcon.set(st.icon, value);
    }
  }

  // G4 gallery shows every vocabulary
  const shownDomains = new Set([...catalogue.gallery_state_domains, ...(catalogue.gallery_other_domains ?? [])]);
  for (const [domain, d] of entries(states)) if (domain !== "unknown" && typeof d === "object" && !shownDomains.has(domain)) report("G4 state vocabulary missing from the gallery", domain);

  // T8 unused tokens: every group of tokens.json has a rule, and every token in it is used
  if (tokens) {
    const text = [...Object.values(clean), ...Object.values(css)].join("\n");
    const walk = (o, trail) => Object.entries(o).filter(([k]) => !k.startsWith("$")).flatMap(([k, v]) => (v && typeof v === "object" && "$value" in v ? [[...trail, k]] : walk(v, [...trail, k])));
    // group -> the Tailwind namespaces (or CSS variable prefixes) a token of it can be used under
    const NS = { width: ["container"], breakpoint: ["breakpoint"], spacing: ["spacing"], text: ["text"], radius: ["radius"], weight: ["font-weight", "font"], tracking: ["tracking"], shadow: ["shadow"], leading: ["leading"], duration: ["duration"], font: ["font"] };
    const referenced = JSON.stringify(Object.fromEntries(Object.entries(tokens).filter(([g]) => g !== "primitive")));
    for (const [group, body] of entries(tokens)) {
      if (group === "primitive") {  // a primitive is used when a semantic token points at it
        for (const trail of walk(body, [])) if (!referenced.includes(`{primitive.${trail.join(".")}}`)) report("T8 unused token", `primitive.${trail.join(".")}`);
      } else if (group === "light" || group === "dark") {  // a semantic colour is used as a colour utility or var
        for (const trail of walk(body, [])) { const name = trail.join("-"); if (!new RegExp(`(?<![\\w])(?:[a-z-]*-)?${name}(?![\\w-])`).test(text)) report("T8 unused token", `${group}.${name}`); }
      } else if (group in NS) {
        for (const trail of walk(body, [])) {
          const name = trail.join("-");
          // durations and families are used only under their own prefix (`duration-fast`, `--duration-fast`, `font-sans`)
          const strict = group === "duration" || group === "font";
          const patterns = NS[group].flatMap((ns) => [new RegExp(`--${ns}-${name}(?![\\w-])`), strict ? new RegExp(`(?<![\\w-])(?:[a-z]+:)*${ns}-${name}(?![\\w-])`) : new RegExp(`(?<![\\w])(?:[a-z]+:)*(?:[a-z-]+-)?${name}(?![\\w-])`)]);
          if (!patterns.some((re) => re.test(text))) report("T8 unused token", `${group}.${name}`);
        }
      } else if (!group.startsWith("$")) report("T8 token group without a usage rule", group);
    }
  }

  return { findings, stats: { files: Object.keys(code).length, keys: entries(copy).length, elements: defined.length } };
}

export function readTree(root) {
  const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]));
  const files = ["elements", "gallery", "app"].flatMap((d) => walk(path.join(root, d))).filter((f) => /\.(js|html)$/.test(f));
  const json = (f) => JSON.parse(fs.readFileSync(path.join(root, f), "utf8"));
  return {
    code: Object.fromEntries(files.map((f) => [path.relative(root, f), fs.readFileSync(f, "utf8")])),
    copy: json("copy/en.json"), states: json("states.json"), icons: json("icons.json"), catalogue: json("catalogue.json"),
    tokens: json("tokens/tokens.json"), css: Object.fromEntries(["styles/app.css", "vendor/basecoat/basecoat-nova.css"].map((f) => [f, fs.readFileSync(path.join(root, f), "utf8")])),
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { findings, stats } = audit(readTree(process.cwd()));
  if (findings.length) { console.error(findings.join("\n")); process.exit(1); }
  console.log(`audit: ${stats.files} files, ${stats.keys} copy keys, ${stats.elements} elements: clean`);
}
