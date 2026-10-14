// Where the plugin's vendored fixtures and the repository's contracts are, from the UI's tests. Relative to this folder,
// unless EMPIRICA_PLUGIN_ROOT names the plugin (make mutate runs the tests from Stryker's sandbox, a copy of the UI alone).
// Built from paths, not `new URL("…", import.meta.url)`: Vite rewrites that form into a served asset's URL in a browser-like
// environment (the happy-dom tests), and happy-dom's own URL resolves a file: base against its page.
import { dirname, join, resolve } from "node:path";
import { URL, fileURLToPath, pathToFileURL } from "node:url";

const root = process.env.EMPIRICA_PLUGIN_ROOT ? resolve(process.env.EMPIRICA_PLUGIN_ROOT) : join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const dir = (...parts) => new URL(`${pathToFileURL(join(root, ...parts)).href}/`);
export const PLUGIN = dir();
export const FIXTURES = dir("vendor/contracts/empirica-ui-view/v1/fixtures");
export const CONTRACTS = dir("..", "..", "contracts/empirica-ui-view/v1");
