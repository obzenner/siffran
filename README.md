# siffran

Personal Claude Code plugin marketplace — methodology-driven tools for rigorous engineering.

## Install

Add the marketplace (one-time):

```
/plugin marketplace add obzenner/siffran
```

Install a plugin:

```
/plugin install methodologist@siffran
```

## Available plugins

| Plugin | Description |
|--------|-------------|
| `methodologist` | Methodology router — formal reasoning, decomposition, invariant analysis, contradiction, abstraction refinement, empirical falsification. Use `/think` or `/think <methodology>`. |

## Repo structure

```
siffran/
├── .claude-plugin/
│   └── marketplace.json
├── plugins/
│   └── <plugin-name>/
│       ├── .claude-plugin/
│       │   └── plugin.json
│       └── skills/
│           └── <skill-name>/
│               ├── SKILL.md
│               └── <supporting files>
└── README.md
```

## Local development

Load a plugin directly:

```bash
claude --plugin-dir ./plugins/methodologist
```

Validate:

```
/plugin validate .
```
