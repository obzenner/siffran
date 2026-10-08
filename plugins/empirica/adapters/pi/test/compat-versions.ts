// The reviewed pi-subagents versions are the checked-in compat records: tests read them from the directory,
// so reviewing a release (adding its record) never needs a version list edited here.

import { readdirSync } from "node:fs";

import { compareVersions } from "../src/host-profile.ts";
import { COMPAT_DIR } from "../src/subagent-inventory.ts";

/** The exact versions that have a compat record, oldest first. */
export function reviewedVersions(): string[] {
  return readdirSync(COMPAT_DIR).filter((file) => /^pi-subagents-\d+\.\d+\.\d+\.json$/.test(file))
    .map((file) => file.slice("pi-subagents-".length, -".json".length)).sort(compareVersions);
}
