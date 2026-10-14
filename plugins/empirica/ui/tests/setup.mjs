// Vitest's setup for every unit test file (vitest.config.mjs): fast-check from one fixed seed, so a run is the same run
// every time and a failure prints the seed and path that reproduce it. A test that passes its own { seed } keeps it.
import fc from "fast-check";

fc.configureGlobal({ seed: 20261013 });
