# Methodologist Codex adapter

The Codex package is rooted at `plugins/methodologist`. Its
`.codex-plugin/plugin.json` points `skills` at the existing `./skills/` tree and
therefore does not copy or fork methodology semantics. Native implicit or
`$think` activation runs that shared skill directly in stateless simple mode.
This layout follows the official [plugin packaging][plugins] and [skill
activation][skills] contracts.

Codex CLI 0.146.0 also supports plugin-bundled MCP servers, and the same
`.mcp.json` is shared by two harnesses — Codex (via `.codex-plugin/plugin.json`)
and Claude Code (which auto-loads the plugin-root `.mcp.json`). The two resolve
paths differently: Codex launches the server with the installed plugin root as
its working directory, whereas Claude Code launches it in the user's session
directory and instead exports `CLAUDE_PLUGIN_ROOT` to the subprocess. A static
path therefore cannot satisfy both — a relative path breaks under Claude Code,
and `${CLAUDE_PLUGIN_ROOT}` is a Claude-only substitution Codex passes through
literally. So `args` is a one-line `python3 -c` bootstrap that resolves
`mcp_server.py` from `CLAUDE_PLUGIN_ROOT` (Claude Code) with a fallback to the
process cwd (Codex's plugin root). The server exposes one read-only tool,
`methodologist_select`, and only translates MCP JSON-RPC to the existing
`methodologist/v1` bridge. The host-neutral core validates the registry name and
canonical six-phase plan; the shared skill and methodology Markdown remain
authoritative for reasoning and execution.

This adapter intentionally provides no slash command, task widget, persistence,
or hooks. An ambiguous two-candidate call returns a decision requirement for
Codex to ask in conversation; it does not pretend MCP itself supplies human UI.

Run the deterministic and real-host checks from the repository root:

```sh
make methodologist-codex-check
make methodologist-codex-smoke  # existing Codex login or OPENAI_API_KEY
```

The smoke pins `@openai/codex@0.146.0`, creates isolated temporary `HOME` and
`CODEX_HOME` directories, installs this repository as a marketplace, and
verifies marketplace, skill, and MCP invocation. Methodologist itself bundles
no lifecycle hooks; Empirica is a separate marketplace entry whose hooks must
be installed and trusted independently.

[plugins]: https://developers.openai.com/plugins/build/plugins
[skills]: https://developers.openai.com/codex/skills
