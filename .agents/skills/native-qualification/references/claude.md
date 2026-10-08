# Claude Code

- Launch (after explicit go): `make claude-dev CLAUDE=/absolute/supported/claude ARGS="--debug-file /absolute/evidence/claude.debug.log"`.
- Claude needs native MCP form elicitation; that is how the human is asked inside `report_convergence`.
- Activation is the user command `/empirica:empirica <goal>`. A model-invoked skill or read, or ordinary pasted chat,
  does not activate.
- The Stop hook never pauses for evidence on its first block. When the only blocker is a human confirmation it blocks (exit 2) with one
  directive (`settlement_notices.evidence_report`): call `report_convergence`; the human is asked inside that call, if this host can show a dialog. Any other reason blocks with the real reason.
- A Stop continuation (`stop_hook_active: true`) is ended by control mode (ADR-0068, `HX-STOP-03`). Claude's own cap of
  eight continuations is a host limit, never the plan. Ledger rows: `stop_flag_reset`, `stop_pause_rendering`,
  `stop_unattended_directive`, `stop_host_cap`.
- Reviewer configuration: see the [governance guide](../../../../plugins/empirica/skills/empirica/references/governance.md).
- Settings to snapshot and restore: `/model` rewrites `model` in `~/.claude/settings.json`. Settings `env` must reach
  hooks and MCP; the recorded entrypoint is `cli`.
- Retained admission log: `make empirica-claude-mcp-log-check LOG=/absolute/evidence/claude.debug.log`.
- Methodologist sanity: `/methodologist:think`.
- `reviewer_preflight_refusal` cannot be induced on Claude without changing user settings while another Claude family is
  pinned; record it `NOT_REACHED` with that reason.
