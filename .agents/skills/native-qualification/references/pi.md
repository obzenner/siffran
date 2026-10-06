# Pi

- Launch (after explicit go): `make pi-dev PI=/absolute/supported/pi`.
- Use the checkout's complete package and run the auditor-resolution inspection described in `doc/testing.md`.
  Reviewer configuration: [Pi adapter guide](../../../../plugins/empirica/adapters/pi/README.md#governed-initialization).
- Activation is `/empirica <goal>`. Pi records `ctx.mode=tui|rpc` with `interactive=true`; `ctx.mode=print|json` has
  `interactive=false` and is the non-interactive surface.
- Pi's gate presents the evidence batch in-process (`ctx.hasUI`) and settles only on what that same call observed.
- Settings to snapshot and restore: `/model` updates `defaultModel` and `defaultProvider` in `~/.pi/agent/settings.json`.
- For the audit row, invoke the packaged auditor once through the structured `subagent` tool with exactly
  `{"agent":"empirica.empirica-auditor","task":"Audit the host-provided dossier."}`; send no other fields. A rejected
  launch is an admission failure, so stop rather than retrying. The
  [Pi adapter guide](../../../../plugins/empirica/adapters/pi/README.md#canonical-audit-call) owns the invocation and
  the reviewer-derivation rules.
- `reviewer_preflight_refusal` is required on Pi: the author runs on the model the host would choose as reviewer (the
  effective `subagents.defaultModel`).
- Methodologist sanity: `/think`.
