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
// It does not check per-enum gallery coverage, levels, or runtime-built strings beyond these patterns.
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
    "budget.*": ["passes", "spawns", "audit_spawns"].map((v) => `budget.${v}`),
    "legend.of.*": ["run", "audit", "diagnostic", "claim", "line"].map((v) => `legend.of.${v}`),
    "filter.*": cat.filters.map((f) => `filter.${f.id}`),
    "outcome.*": dom("run").map((v) => `outcome.${v}`),
    "outcome.empty.*": dom("run").map((v) => `outcome.empty.${v}`),
    "view.empty.*": cat.data_sources.map((v) => `view.empty.${v}`),
    "app.data.*": cat.data_sources.map((v) => `app.data.${v}`),
    "tone.*": cat.tones.map((v) => `tone.${v}`),
  };
  return F;
}

/** Template literal passed to t(`…`), with each ${…} (balanced) replaced by *. */
function templates(src) {
  const out = [];
  for (let i = src.indexOf("t(`"); i !== -1; i = src.indexOf("t(`", i + 1)) {
    if (/[\w$]/.test(src[i - 1] ?? "")) continue;
    let j = i + 3, depth = 0, norm = "";
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

export function audit({ code, copy, states, icons, catalogue }) {
  const findings = [];
  const report = (rule, msg) => findings.push(`${rule}: ${msg}`);
  const clean = Object.fromEntries(Object.entries(code).map(([f, s]) => [f, stripComments(s)]));
  const all = Object.values(clean).join("\n");

  // C1 copy keys
  const literals = new Set([...all.matchAll(/["']([a-z][\w-]*(?:\.[\w-]+)+)["']/g)].map((m) => m[1]));
  const calls = new Set([...all.matchAll(/\bt\(\s*(["'])([^"']+)\1/g)].map((m) => m[2]));
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
  for (const tpl of new Set(Object.values(clean).flatMap(templates))) {
    if (!tpl.includes("*")) { calls.add(tpl); continue; }
    if (!(tpl in F)) { report("C1 undeclared dynamic copy family", `t(\`${tpl}\`)`); continue; }
    for (const k of F[tpl]) expanded.add(k);
  }
  const used = (k) => literals.has(k) || stateKeys.has(k) || expanded.has(k);
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

  return { findings, stats: { files: Object.keys(code).length, keys: entries(copy).length, elements: defined.length } };
}

export function readTree(root) {
  const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]));
  const files = ["elements", "gallery", "app"].flatMap((d) => walk(path.join(root, d))).filter((f) => /\.(js|html)$/.test(f));
  const json = (f) => JSON.parse(fs.readFileSync(path.join(root, f), "utf8"));
  return {
    code: Object.fromEntries(files.map((f) => [path.relative(root, f), fs.readFileSync(f, "utf8")])),
    copy: json("copy/en.json"), states: json("states.json"), icons: json("icons.json"), catalogue: json("catalogue.json"),
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { findings, stats } = audit(readTree(process.cwd()));
  if (findings.length) { console.error(findings.join("\n")); process.exit(1); }
  console.log(`audit: ${stats.files} files, ${stats.keys} copy keys, ${stats.elements} elements: clean`);
}
