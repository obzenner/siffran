// Copies tailwindcss's own LICENSE beside dist/ui.css (Tailwind output carries only a banner) and records its version
// and sha256 in dist/VERSIONS. Run by `make ui-css` after the Tailwind CLI writes ui.css.
import { createHash } from "node:crypto";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

const version = JSON.parse(readFileSync("node_modules/tailwindcss/package.json", "utf8")).version;
copyFileSync("node_modules/tailwindcss/LICENSE", "dist/LICENSE-tailwindcss");
const sha = createHash("sha256").update(readFileSync("dist/LICENSE-tailwindcss")).digest("hex");
writeFileSync("dist/VERSIONS", `tailwindcss ${version}\n\n${sha}  LICENSE-tailwindcss\n`);
console.log(`tailwindcss ${version} licence copied to dist/`);
