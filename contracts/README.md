# Siffran contracts

These contracts are the implementation-independent boundary between plugin cores and host or
persistence adapters. Claude Code hooks, Pi events, filesystem paths, Git commands, process exit
codes, and UI details must not appear in a core request or response.

Contract versions are additive within a major version. A consumer validates the envelope before
dispatch and treats an unknown command or decision as `unsupported`, never as success.

## Protocols

- `empirica/v1` — run lifecycle and convergence decisions.
- `methodologist/v1` — methodology selection and phase progression.

`fixtures/` contains substrate-neutral examples used by every adapter's conformance suite.
