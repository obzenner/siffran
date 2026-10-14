// The UI's unit tests (make check-ui, make ui-test, make mutate through Stryker's Vitest runner). Every tests/*.test.mjs,
// in Node unless a file asks for happy-dom; never the headless-Chrome journeys (tests/journeys.json), never watch or
// coverage. fast-check runs from one fixed seed (tests/setup.mjs). The timeouts are liveness bounds, not budgets: the
// slowest test takes about 0.3 s, so a test over 5 s (a hook over 10 s) hangs, and a hang is a bug to fix. Vitest has no
// whole-run timeout; make check's runner bounds the suite (scripts/run_suites.py).
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/*.test.mjs"],
    setupFiles: ["tests/setup.mjs"],
    environment: "node",
    watch: false,
    coverage: { enabled: false },
    testTimeout: 5_000,
    hookTimeout: 10_000,
    teardownTimeout: 10_000,
    reporters: ["dot"],
  },
});
