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
- **Auditor selection (G14, ADR-0060 amendment).** An explicit `agentOverrides["empirica.empirica-auditor"].model` is
  used as written. Otherwise, if the default or main model is in the author's identity class, `/empirica` picks the first
  model of Pi `enabledModels` (project, else user) that is available and in another class, and its start notice says
  `auditor: <model> (picked from enabledModels; <replaced> is in your model's class)`. Record that line.
- `reviewer_preflight_refusal` is required on Pi. Since the pick above, the default no longer reproduces it: make the
  author's class the only one in `enabledModels`, or set an explicit same-class auditor override, then expect the
  refusal (`no model in enabledModels is available in another class` or `would run on your own model`).
- **Print and JSON mode** (`pi -p`, `--mode json`) have no UI: notices go to stderr and a refusal exits 1. Auto needs
  `EMPIRICA_AUTO_DELEGATION=1` there; deliberative cannot be approved without a human, so it is refused.
- Methodologist sanity: `/think`.
