// Load a pi-subagents package through its PUBLIC `exports` map, from an explicit package root.
//
// Empirica's tools and tests never guess which pi-subagents to use: the caller names a package root
// (an installed `pi-subagents` directory, with its dependencies resolvable) and this module resolves
// `pi-subagents/<subpath>` from that package's own `package.json` `exports`, then loads it. It never
// consults the home directory, `npm root`, or Pi's settings.
//
// Loading goes through jiti because pi-subagents ships TypeScript source before 0.7x and compiled JS
// after. Peers are host-provided: `typebox` and `@earendil-works/pi-tui` resolve to this repository's
// devDependency copies, and the Pi packages that are not installed here are replaced by stubs that throw on use. The launch preflight touches the peer only for
// `context: "fork"` (SessionManager); a stubbed peer therefore cannot change a `context: "fresh"` answer.
import { createJiti } from "jiti";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const PI_PEER = "@earendil-works/pi-coding-agent";
/** Peers pi-subagents expects its host to provide; the repository's own devDependency copy stands in. */
const REAL_PEERS = ["@earendil-works/pi-tui", "typebox", "typebox/compile", "typebox/value"];
/** Peers with no installed copy here: loading succeeds, any use throws (see `peerStub`). */
const STUBBED_PEERS = [PI_PEER, "@earendil-works/pi-ai", "@earendil-works/pi-agent-core"];

function peerStub(peer) {
  return new Proxy({}, { get(_target, name) {
    if (name === "__esModule" || typeof name === "symbol" || name === "then") return undefined;
    return new Proxy(function stubbedPiPeer() {}, {
      apply() { throw new Error(`${peer} is a test stub: ${String(name)} needs a real Pi`); },
      get(_t, member) {
        if (typeof member === "symbol" || member === "then") return undefined;
        throw new Error(`${peer} is a test stub: ${String(name)}.${String(member)} needs a real Pi`);
      },
    });
  } });
}

/** `{ root, version, manifest }` for an installed pi-subagents directory; throws when it is not one. */
export function readPackage(packageRoot) {
  const root = path.resolve(packageRoot);
  const manifest = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
  if (manifest.name !== "pi-subagents") throw new Error(`${root} is not a pi-subagents package`);
  if (typeof manifest.version !== "string") throw new Error(`${root}/package.json has no version`);
  return { root, version: manifest.version, manifest };
}

/** The file a public export subpath (for example `./preflight`) maps to in this package. */
export function exportFile(pkg, subpath) {
  const entry = pkg.manifest.exports?.[subpath];
  const target = typeof entry === "string" ? entry : entry?.default;
  if (typeof target !== "string") throw new Error(`${pkg.root} does not export ${subpath}`);
  return path.join(pkg.root, target);
}

/** Does this package ship its extension as a bundle (`exports["."]` under `./bundle/`, pi-subagents 0.81.0 and later)? */
export function isBundled(pkg) {
  const entry = pkg.manifest.exports?.["."];
  const target = typeof entry === "string" ? entry : entry?.default;
  return typeof target === "string" && target.startsWith("./bundle/");
}

/**
 * Import a private module of the package the way the package runs it. A source layout (before 0.81.0) has the file under
 * `src/`: `srcSubpath` (without extension). A bundled one (0.81.0 and later) ships `bundle/chunk-<hash>.js` files whose
 * export blocks list the same names under the same spelling, with hashed file names that change from release to release,
 * so the chunk is found by what it exports: the first (by name) whose export block lists every one of `names`.
 * `undefined` when a source layout has no such file; a bundle with no such chunk throws.
 */
export async function importInternal(loader, pkg, srcSubpath, names) {
  if (isBundled(pkg)) {
    const dir = path.join(pkg.root, "bundle");
    const chunk = readdirSync(dir).filter((file) => file.endsWith(".js")).sort().find((file) => {
      const text = readFileSync(path.join(dir, file), "utf8");
      const exported = text.slice(text.lastIndexOf("\nexport {") + 1);
      return exported.startsWith("export {") && names.every((name) => new RegExp(`^  ${name},?$`, "m").test(exported));
    });
    if (chunk === undefined) throw new Error(`no chunk of ${dir} exports ${names.join(", ")}`);
    return loader.import(path.join(dir, chunk));
  }
  const file = ["js", "ts"].map((ext) => path.join(pkg.root, `${srcSubpath}.${ext}`)).find(existsSync);
  return file === undefined ? undefined : loader.import(file);
}

/** A jiti loader with no caches (a fixture must observe the package on disk, not a stale compile). */
export function makeLoader(parentFile) {
  return createJiti(parentFile, {
    moduleCache: false, fsCache: false, tryNative: false,
    alias: Object.fromEntries(REAL_PEERS.map((peer) => [peer, fileURLToPath(import.meta.resolve(peer))])),
    virtualModules: Object.fromEntries(STUBBED_PEERS.map((peer) => [peer, peerStub(peer)])),
  });
}

/** Import `pi-subagents/<subpath>` from the named package root. */
export async function importExport(packageRoot, subpath, loader = makeLoader(path.join(path.resolve(packageRoot), "package.json"))) {
  const pkg = readPackage(packageRoot);
  return loader.import(exportFile(pkg, subpath));
}

/**
 * Call the package's public launch preflight with an explicit Pi agent directory and project.
 * `env` is restored afterwards; the package reads `PI_CODING_AGENT_DIR` (and `PI_OFFLINE`) at call
 * time, so nothing outside `agentDir` and `cwd` is consulted unless the caller leaves PI_OFFLINE unset
 * (then pi-subagents may run `npm root -g` to find globally installed packages).
 */
export async function runPreflight({ packageRoot, loader, agentDir, env = {}, input }) {
  const preflight = await importExport(packageRoot, "./preflight", loader);
  const keys = ["PI_CODING_AGENT_DIR", ...Object.keys(env)];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    process.env.PI_CODING_AGENT_DIR = agentDir;
    Object.assign(process.env, env);
    return await preflight.resolveSubagentLaunchContract(input);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}
