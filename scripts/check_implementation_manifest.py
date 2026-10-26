#!/usr/bin/env python3
"""Check that ``IMPLEMENTATION_SOURCE_PATHS`` equals the closure of ``IMPLEMENTATION_ROOTS`` (both in
``plugins/empirica/adapters/sandbox/common.py``).

The rule is written once, in ``doc/testing.md`` ("The generic authority implementation manifest"). This derives the
closure from the roots and refuses a difference in either direction, an import that resolves to no file, a dynamic or
star import it cannot follow, an exemption that is stale or not unique, and a root the rule does not name. Stdlib only.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "empirica"
RULE = ROOT / "doc" / "testing.md"
#: The roots list in RULE lies between these two lines; it must name exactly ``IMPLEMENTATION_ROOTS``, in backticks.
ROOTS_START, ROOTS_END = "<!-- implementation-roots -->", "<!-- /implementation-roots -->"

#: Where an absolute Python import is looked up: the plugin root, then ``adapters/`` for the one path shim
#: (``adapters/pi/bridge.py`` puts ``adapters/`` on ``sys.path`` and imports ``bridge``).
PYTHON_SEARCH = ("", "adapters")
#: The third-party distributions the plugin runtime imports: installed, outside the plugin tree, not bound.
PYTHON_EXTERNAL = frozenset({"jsonschema", "referencing"})
#: Python dynamic loading is refused outright: any import of these modules and any use of these names.
PYTHON_DYNAMIC = frozenset({"__import__", "importlib", "runpy"})
#: The TypeScript lines that use ``createRequire`` or a non-literal ``import()``, by (file, stripped line), each of
#: which must occur exactly once, with why none can load a plugin source. The ``load`` line is a general loader: its
#: callers are trusted to pass only the resolved pi-subagents path.
TS_DYNAMIC_EXEMPT = {
    ("adapters/pi/src/runtime-owner.ts", 'import { createRequire } from "node:module";'):
        "imports the resolver used only by the next line",
    ("adapters/pi/src/runtime-owner.ts", "resolve: (specifier, fromFile) => createRequire(fromFile).resolve(specifier),"):
        "resolves, never loads, a specifier inside the separately installed pi-subagents package",
    ("adapters/pi/src/runtime-owner.ts", "load: (file) => import(pathToFileURL(file).href),"):
        "loads the separately installed pi-subagents package, outside the plugin tree",
}
#: ``from "x"`` and the side-effect ``import "x"``, whatever the binding list before them holds.
TS_STATIC = re.compile(r"""\b(?:from|import|export)\s*["']([^"']+)["']""")
#: ``import``/``require`` then ``(`` past any whitespace and comments, with the literal specifier if there is one.
TS_DYNAMIC = re.compile(r"""\b(?:import|require)(?:\s|/\*[\s\S]*?\*/|//[^\n]*)*\(\s*(?:["'](?P<literal>[^"']+)["']\s*\))?"""
                        r"""|\b(?P<create>createRequire)\b""")
#: Any other ``require`` (an alias, ``require?.(…)``, ``require.call``), looked for in the text without comments.
TS_REQUIRE = re.compile(r"""\brequire\b(?!\s*\(\s*["'][^"']+["']\s*\))""")
TS_COMMENT = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")


class Refused(Exception):
    pass


def _name(node: ast.AST) -> str | None:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None


def _python_module(plugin: Path, dotted: str) -> Path | None:
    for base in PYTHON_SEARCH:
        stem = plugin.joinpath(base, *dotted.split("."))
        for path in (stem.with_suffix(".py"), stem / "__init__.py"):
            if path.is_file():
                return path
    return None


def _bound_names(path: Path) -> set[str]:
    """The names a module binds at its top level (also inside a top-level if/try/with), never inside a def or class."""
    names: set[str] = set()
    pending = list(ast.parse(path.read_text(encoding="utf-8"), str(path)).body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name)}
        for field in ("body", "orelse", "finalbody", "handlers"):
            pending += getattr(node, field, [])
    return names


def _python_imports(plugin: Path, path: Path) -> list[tuple[str, int, str | None]]:
    """Every module a Python source imports, at any depth, as (name, line, the module a ``from`` name may belong to);
    a dynamic or star import is refused."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    relative = path.relative_to(plugin)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            package = path.parent.relative_to(plugin).parts
            base = ".".join(package[:len(package) - node.level + 1]) if node.level else ""
            module = ".".join(part for part in (base, node.module or "") if part)
            names = [module if not node.level else ""]
        else:
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [_name(node)] if isinstance(node, (ast.Name, ast.Attribute)) else [])
        for name in names:
            if name and name.split(".")[0] in PYTHON_DYNAMIC:
                raise Refused(f"dynamic import: {relative}:{node.lineno} uses {name}, which is refused outright")
        if isinstance(node, ast.Import):
            imports += [(alias.name, node.lineno, None) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                raise Refused(f"star import: {relative}:{node.lineno} cannot be followed")
            imports += [(module, node.lineno, None)] + [(f"{module}.{alias.name}", node.lineno, module)
                                                        for alias in node.names]
    return imports


def _python_edges(plugin: Path, path: Path) -> list[tuple[Path, int]]:
    edges = []
    for dotted, line, owner in _python_imports(plugin, path):
        found = _python_module(plugin, dotted)
        if found is None:
            if dotted.split(".")[0] in sys.stdlib_module_names | PYTHON_EXTERNAL:
                continue
            source = _python_module(plugin, owner) if owner else None
            if source and (source.name != "__init__.py" or dotted.rpartition(".")[2] in _bound_names(source)):
                continue  # ``from module import name``: a name the module (or the package's __init__) binds
            raise Refused(f"unresolved import: {path.relative_to(plugin)}:{line} imports {dotted}, which is in neither "
                          "the plugin, the standard library nor PYTHON_EXTERNAL")
        parts = found.relative_to(plugin).parts
        edges += [(plugin.joinpath(*parts[:depth], "__init__.py"), line) for depth in range(1, len(parts))
                  if plugin.joinpath(*parts[:depth], "__init__.py").is_file()]  # every enclosing package runs
        edges.append((found, line))
    return edges


def _ts_edges(plugin: Path, path: Path) -> list[tuple[Path, int]]:
    text = path.read_text(encoding="utf-8")
    relative = str(path.relative_to(plugin))
    line = lambda offset: text.count("\n", 0, offset) + 1  # noqa: E731
    specifiers = [(match.group(1), match.start()) for match in TS_STATIC.finditer(text)]
    uncommented = TS_COMMENT.sub(lambda comment: re.sub(r"[^\n]", " ", comment.group()), text)  # same offsets
    for match in (*TS_DYNAMIC.finditer(text), *TS_REQUIRE.finditer(uncommented)):
        source_line = text[text.rfind("\n", 0, match.start()) + 1:].split("\n", 1)[0].strip()
        if match.re is TS_DYNAMIC and match.group("literal") is not None:
            specifiers.append((match.group("literal"), match.start()))
        elif (relative, source_line) not in TS_DYNAMIC_EXEMPT:
            uses = ("uses `require` outside a literal call" if match.re is TS_REQUIRE
                    else "uses `createRequire`" if match.group("create") else "has a non-literal target")
            raise Refused(f"dynamic import: {relative}:{line(match.start())} {uses}")
    edges = []
    for specifier, offset in specifiers:
        if not specifier.startswith("."):
            continue  # a node: builtin or an installed package, outside the plugin tree
        target = (path.parent / specifier).resolve()
        target = target.with_suffix(".ts") if target.suffix == ".js" else target
        if not target.is_file():
            raise Refused(f"unresolved import: {relative}:{line(offset)} imports {specifier}")
        edges.append((target, line(offset)))
    return edges


def closure(plugin: Path, roots: tuple[str, ...]) -> dict[str, str]:
    """Every path the roots reach, mapped to the edge (``importer:line``) that first reached it, or ``root``."""
    plugin = plugin.resolve()  # TypeScript specifiers are resolved, so the base must be too
    reached: dict[str, str] = {}
    pending: list[Path] = []
    for root in roots:
        path = plugin / root
        files = sorted(path.glob("*.json")) if path.is_dir() else [path] if path.is_file() else []
        if not files:
            raise Refused(f"missing root: {root}")
        reached.update((str(file.relative_to(plugin)), "root") for file in files)
        pending += files
    while pending:
        path = pending.pop()
        edges = (_python_edges(plugin, path) if path.suffix == ".py"
                 else _ts_edges(plugin, path) if path.suffix == ".ts" else [])
        for target, line in edges:
            key = str(target.relative_to(plugin))
            if key not in reached:
                reached[key] = f"{path.relative_to(plugin)}:{line}"
                pending.append(target)
    return reached


def check(plugin: Path, roots: tuple[str, ...], listed: tuple[str, ...]) -> list[str]:
    """The refusals; empty exactly when the listed manifest equals the closure of the roots."""
    try:
        reached = closure(plugin, roots)
    except Refused as refusal:
        return [str(refusal)]
    refusals = ([f"unlisted: {path} (reached from {reached[path]})" for path in sorted(set(reached) - set(listed))]
                + [f"unreachable: {path} is listed but no root reaches it"
                   for path in sorted(set(listed) - set(reached))])
    for path, line in TS_DYNAMIC_EXEMPT:
        if path not in reached:
            continue
        count = [text.strip() for text in (plugin / path).read_text(encoding="utf-8").splitlines()].count(line)
        if count == 0:
            refusals.append(f"stale exemption: {path}: {line}")
        elif count > 1:
            refusals.append(f"duplicated exemption: {path}: {line} occurs {count} times")
    return refusals


def roots_in_rule(roots: tuple[str, ...], rule: str) -> list[str]:
    """The differences between the declared roots and the backticked paths of the rule's roots list, both ways."""
    written = set(re.findall(r"`([^`]+)`", rule.partition(ROOTS_START)[2].partition(ROOTS_END)[0]))
    return ([f"undocumented root: {root} is not in the roots list of doc/testing.md" for root in roots
             if root not in written]
            + [f"undeclared root: {path} is in the roots list of doc/testing.md but not in IMPLEMENTATION_ROOTS"
               for path in sorted(written - set(roots))])


def main() -> int:
    sys.path.insert(0, str(PLUGIN))
    from adapters.sandbox import common
    refusals = (check(PLUGIN, common.IMPLEMENTATION_ROOTS, common.IMPLEMENTATION_SOURCE_PATHS)
                + roots_in_rule(common.IMPLEMENTATION_ROOTS, RULE.read_text(encoding="utf-8")))
    for refusal in refusals:
        print(f"implementation manifest: {refusal}", file=sys.stderr)
    if not refusals:
        print(f"implementation manifest: {len(common.IMPLEMENTATION_SOURCE_PATHS)} files, exactly the closure of "
              f"{len(common.IMPLEMENTATION_ROOTS)} roots")
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
