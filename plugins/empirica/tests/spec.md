# Fixture spec — unknowns with inline confidence (spike M3)

Convention under test (ADR-15/16): each unknown is a checkbox item; its confidence
lives in a trailing HTML comment `<!-- confidence: N -->`. θ = 0.8.
converged() ⇔ every unknown's confidence ≥ θ.

## Unknowns

- [ ] U1: Can a Python Stop hook block completion via the documented stdin contract? <!-- confidence: 0.40 -->
- [ ] U2: Does M3 SpikeHarness yield a deterministic gate from a real subprocess? <!-- confidence: 0.55 -->
- [x] U3: Repo tooling is Python (validate.py). <!-- confidence: 0.95 -->
