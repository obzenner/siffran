// Which UI tests hold which source, computed from the imports, never declared (T3). The one exception is declared in
// tests/journeys.json: the headless-Chrome journeys load the page in a browser, so no import shows what they hold; each
// names its page, and what it holds is computed from that page's module script.
//   node scripts/imports.mjs --related [file,…]  make ui-test: `vitest related --run` on the files (relative to this
//       folder or to the repository), or on the UI files changed in the worktree; then names the journeys that also hold
//       one of them (make ui-browser)
// tests/imports.test.mjs is the guard: every source under app/ and elements/ is imported, directly or through another
// module, by at least one Vitest test; every journey file is declared; no helper in tests/ is orphaned.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

export const UI = join(dirname(fileURLToPath(import.meta.url)), "..");
const disk = (f) => readFileSync(join(UI, f), "utf8");
const walk = (dir) => readdirSync(join(UI, dir)).flatMap((f) => (statSync(join(UI, dir, f)).isDirectory() ? walk(`${dir}/${f}`) : [`${dir}/${f}`]));

/** The sources every Vitest test may reach: each .js under app/ and elements/. */
export const sources = () => [...walk("app"), ...walk("elements")].filter((f) => f.endsWith(".js")).sort();
/** The Vitest unit tests (vitest.config.mjs `include`) and the journeys (tests/journeys.json). */
export const unitTests = () => readdirSync(join(UI, "tests")).filter((f) => f.endsWith(".test.mjs")).map((f) => `tests/${f}`).sort();
export const journeys = (read = disk) => JSON.parse(read("tests/journeys.json")).journeys;

// a statement `import … from "x"` or `export … from "x"` (across lines) or `import "x"`, at the start of a line, and a
// literal `import("x")` anywhere
const SPEC = /^\s*(?:import|export)\s[^'"`;]*?\bfrom\s*["']([^"']+)["']|\bimport\s*\(\s*["']([^"']+)["']\s*\)|^\s*import\s*["']([^"']+)["']/gm;
/** The local modules file `f` imports (paths relative to this folder; packages and node: builtins left out). */
export function imports(f, read = disk) {
  const text = read(f).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  return [...text.matchAll(SPEC)].map((m) => m[1] ?? m[2] ?? m[3]).filter((s) => s.startsWith(".")).map((s) => normalize(join(dirname(f), s)));
}
/** Every local module `entries` reach through their imports, the entries included. A name that is no file (a path in a
 * test's string, say) reaches nothing. */
export function reach(entries, read = disk) {
  const seen = new Set(), todo = [...entries];
  while (todo.length) {
    const f = todo.pop();
    if (seen.has(f)) continue;
    let found;
    try { found = /\.m?js$/.test(f) ? imports(f, read) : (read(f), []); } catch { continue; }
    seen.add(f);
    todo.push(...found);
  }
  return seen;
}
/** The module scripts a page loads (`<script type="module" src="…">`), relative to this folder. */
export const pageScripts = (page, read = disk) => [...read(page).matchAll(/<script\b[^>]*type="module"[^>]*src="([^"]+)"/g)].map((m) => normalize(join(dirname(page), m[1])));
/** What journey `j` holds: everything its page's scripts reach. */
export const heldBy = (j, read = disk) => reach(pageScripts(j.page, read), read);

/** The sources no unit test reaches. */
export const unreached = (srcs = sources(), tests = unitTests(), read = disk) => { const r = reach(tests, read); return srcs.filter((s) => !r.has(s)); };

/** The UI's files among repository paths (or paths relative to this folder), relative to this folder. */
export function uiFiles(paths, prefix = "plugins/empirica/ui/") {
  return [...new Set(paths.map((p) => (p.startsWith(prefix) ? p.slice(prefix.length) : p)).filter((p) => p && !/^(node_modules|reports|dist|vendor|\.stryker-tmp)\//.test(p)))];
}
/** The UI files changed in the worktree: against HEAD, and the untracked ones (git run here lists them relative to here). */
export function changed(git = (...a) => spawnSync("git", a, { cwd: UI, encoding: "utf8" }).stdout ?? "") {
  return uiFiles([...git("diff", "--name-only", "--relative", "HEAD").split("\n"), ...git("ls-files", "--others", "--exclude-standard").split("\n")]);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const [flag, ...args] = process.argv.slice(2);
  if (flag !== "--related") { console.error("imports: --related [file…]"); process.exit(2); }
  const given = args.flatMap((a) => a.split(",")).map((a) => a.trim()).filter(Boolean);  // FILES=a.js,b.js
  const files = (given.length ? uiFiles(given) : changed()).filter((f) => existsSync(join(UI, f)));
  if (!files.length) { console.log("ui-test: no UI file changed (FILES= names some)"); process.exit(0); }
  console.log(`ui-test: the tests related to ${files.join(" ")}`);
  const done = spawnSync(join(UI, "node_modules/.bin/vitest"), ["related", "--run", "--passWithNoTests", ...files], { cwd: UI, stdio: "inherit" });
  const held = journeys().filter((j) => files.some((f) => heldBy(j).has(f) || f === j.file));
  for (const j of held) console.log(`ui-test: ${j.file} also holds ${files.filter((f) => heldBy(j).has(f) || f === j.file).join(" ")}: make ${j.make.join(" / make ")}`);
  process.exit(done.status ?? 1);
}
