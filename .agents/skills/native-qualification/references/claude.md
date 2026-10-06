# Claude Code

- Launch (after explicit go): `make claude-dev CLAUDE=/absolute/supported/claude ARGS="--debug-file /absolute/evidence/claude.debug.log"`.
- Claude needs native MCP form elicitation; that is how the human is asked inside `report_convergence`.
- Activation is the user command `/empirica:empirica <goal>`. A model-invoked skill or read, or ordinary pasted chat,
  does not activate.
- The Stop hook never pauses for evidence. When the only blocker is a human confirmation it blocks (exit 2) with one
  directive: call `report_convergence`; the human is asked inside that call. Any other reason blocks with the real reason.
- Reviewer configuration: see the [governance guide](../../../../plugins/empirica/skills/empirica/references/governance.md).
- Settings to snapshot and restore: `/model` rewrites `model` in `~/.claude/settings.json`. Settings `env` must reach
  hooks and MCP; the recorded entrypoint is `cli`.
- Retained admission log: `make empirica-claude-mcp-log-check LOG=/absolute/evidence/claude.debug.log`.
- Methodologist sanity: `/methodologist:think`.
- `reviewer_preflight_refusal` cannot be induced on Claude without changing user settings while another Claude family is
  pinned; record it `NOT_REACHED` with that reason.
