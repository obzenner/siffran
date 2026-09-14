#!/usr/bin/env python3
"""Empirica 2.0 architecture target-state validator (D3).

A dependency-free static guard that makes the v2 ownership/subtraction rules machine-checkable.
It detects architecture drift *without* becoming a runtime framework or freezing incidental file
layout. The validator is structural: it parses AST import edges, counts effective runtime inventory,
and scans scoped runtime/Make surfaces for forbidden files, symbols, fields, actions, and protocol
literals. It owns no domain reason tables; the accepted D2 PublicContract/host-profiles registry is
loaded only to assert the configured required identity references resolve.

This target is intentionally NOT composed into ``check-static`` in D3. The current pre-D6/D7 tree is
expected to be RED: the reported violations are the red acceptance list for D6/D7, not a baseline to
silently except. D3-M composes the target only after the target-state implementation is green.

Stdlib only. No plugin/runtime import or execution. The check functions are pure: each takes explicit
inputs (config values, file lists, roots) and returns a list of :class:`Diagnostic`. ``main`` is the
single CLI shell that loads config, discovers files, runs the checks, renders sorted diagnostics, and
sets the exit code (0 = green, 1 = violations). ``--self-test`` runs the committed synthetic suite.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --- rule IDs (stable, sorted output) -----------------------------------------

RULE_BUDGET = "ARCH-BUDGET"
RULE_DEP_PY = "ARCH-DEP-PY"
RULE_DEP_TS = "ARCH-DEP-TS"
RULE_THIN_HOOK = "ARCH-THIN-HOOK"
RULE_FORBIDDEN_PATH = "ARCH-FORBIDDEN-PATH"
RULE_FORBIDDEN_SYMBOL = "ARCH-FORBIDDEN-SYMBOL"
RULE_V1_PROTOCOL = "ARCH-V1-PROTOCOL"
RULE_ADAPTER_ADJUDICATOR = "ARCH-ADAPTER-ADJUDICATOR"
RULE_CONTRACT_REF = "ARCH-CONTRACT-REF"
RULE_MAKE_LIFECYCLE = "ARCH-MAKE-LIFECYCLE"
RULE_PARSE = "ARCH-PARSE"

ALL_RULES = (
    RULE_BUDGET,
    RULE_DEP_PY,
    RULE_DEP_TS,
    RULE_THIN_HOOK,
    RULE_FORBIDDEN_PATH,
    RULE_FORBIDDEN_SYMBOL,
    RULE_V1_PROTOCOL,
    RULE_ADAPTER_ADJUDICATOR,
    RULE_CONTRACT_REF,
    RULE_MAKE_LIFECYCLE,
    RULE_PARSE,
)


@dataclass(frozen=True)
class Diagnostic:
    """A single rule-coded, actionable finding with a stable path:line location."""

    rule_id: str
    path: str
    line: int
    message: str

    def sort_key(self) -> tuple:
        return (self.rule_id, self.path, self.line, self.message)


# --- config + discovery --------------------------------------------------------

def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def rel_posix(path: Path, root: Path) -> str:
    """Path relative to ``root`` as a POSIX string (for stable diagnostics)."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def matches_test_pattern(rel: str, patterns: list) -> bool:
    """A file is excluded when any pattern matches the path, a path component, or the filename."""
    parts = Path(rel).parts
    filename = Path(rel).name
    for pat in patterns:
        if fnmatch.fnmatchcase(rel, pat):
            return True
        for component in parts:
            if fnmatch.fnmatchcase(component, pat):
                return True
        if fnmatch.fnmatchcase(filename, pat):
            return True
    return False


def discover_runtime_files(config: dict, repo_root: Path) -> list:
    """Every ``.py``/``.ts`` file under the package root, excluding test paths."""
    pkg_root = Path(repo_root) / config["runtime_roots"]["package_root"]
    exts = tuple(config["effective_runtime"]["extensions"])
    patterns = config["excluded_test_patterns"]
    files = []
    for path in sorted(pkg_root.rglob("*")):
        if not path.is_file() or path.suffix not in exts:
            continue
        rel = path.relative_to(pkg_root).as_posix()
        if matches_test_pattern(rel, patterns):
            continue
        files.append(path)
    return files


def count_lines(path: Path) -> int:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def inventory(config: dict, repo_root: Path) -> tuple:
    """Return (total, baseline, maximum, delta, file_count, baseline_ref) for the summary."""
    files = discover_runtime_files(config, repo_root)
    er = config["effective_runtime"]
    exts = set(er["extensions"])
    total = sum(count_lines(f) for f in files if f.suffix in exts)
    baseline = er["baseline"]
    maximum = er["maximum"]
    return (total, baseline, maximum, total - baseline, len(files), er.get("baseline_ref", ""))


# --- layer mapping + import resolution ----------------------------------------

def normalize_module(mod: str, prefixes=None) -> str:
    """Strip a recognized package prefix (``empirica.`` / ``plugins.empirica.``) so absolute
    package-qualified imports classify to the same layer as shorthand/relative spellings.

    External modules with coincidentally similar suffixes (``myempirica.application``) do not match:
    a prefix matches only when ``mod == prefix`` or ``mod`` starts with ``prefix + '.'``.
    """
    if not mod:
        return mod
    for prefix in prefixes or []:
        if mod == prefix:
            return ""
        if mod.startswith(prefix + "."):
            return mod[len(prefix) + 1:]
    return mod


def layer_of_module(mod: str, prefixes=None):
    """Map a resolved module name to a layer id, or ``None`` if external (stdlib/third-party).

    ``prefixes`` (config ``import_package_prefixes``) are stripped first so that
    ``empirica.application.s`` and ``plugins.empirica.application.s`` resolve identically to
    ``application.s``; the layer boundary does not depend on the incidental import spelling."""
    mod = normalize_module(mod, prefixes)
    if not mod:
        return None
    head = mod.split(".")[0]
    if head == "core":
        return "core"
    if head == "application":
        return "application"
    if head == "adapters":
        parts = mod.split(".")
        return f"adapters.{parts[1]}" if len(parts) > 1 else "adapters"
    if head == "vendor":
        return "vendor"
    if head == "hooks":
        return "hooks"
    return None


def file_layer(path: Path, pkg_root: Path, layers: dict):
    """The layer a file belongs to, by longest matching layer root."""
    rel = rel_posix(path, pkg_root)
    best = None
    best_len = -1
    for layer_id, layer in layers.items():
        for root in layer["roots"]:
            root_posix = Path(root).as_posix()
            if rel == root_posix or rel.startswith(root_posix + "/"):
                if len(root_posix) > best_len:
                    best = layer_id
                    best_len = len(root_posix)
    return best


def file_package(path: Path, pkg_root: Path) -> str:
    """Dotted package of a file relative to the package root (for relative-import resolution)."""
    rel = path.resolve().relative_to(pkg_root.resolve())
    return ".".join(rel.parts[:-1])


def resolve_relative(pkg: str, level: int, module):
    """Resolve a relative import base module against the file's package."""
    base_parts = pkg.split(".") if pkg else []
    drop = level - 1
    if drop > 0:
        base_parts = base_parts[: max(0, len(base_parts) - drop)]
    base = ".".join(base_parts)
    if module:
        return (base + "." + module) if base else module
    return base


def importfrom_candidates(pkg: str, node: ast.ImportFrom) -> set:
    """Candidate absolute module strings touched by a ``from ... import`` statement."""
    level = node.level or 0
    if level > 0:
        base = resolve_relative(pkg, level, node.module)
    else:
        base = node.module or ""
    candidates = set()
    if base:
        candidates.add(base)
        for alias in node.names:
            candidates.add(base + "." + alias.name)
    else:
        for alias in node.names:
            candidates.add(alias.name)
    return candidates


def _import_forbidden(mod: str, forbidden: list) -> bool:
    return any(mod == fw or mod.startswith(fw + ".") for fw in forbidden)


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None


def _parse_with_error(path: Path, rel: str):
    """Parse and return ``(tree, diagnostic_or_None)``; a malformed production module is
    fail-closed: the caller emits the diagnostic so no AST-dependent check is silently green."""
    try:
        return ast.parse(path.read_text(encoding="utf-8")), None
    except SyntaxError as exc:
        return None, Diagnostic(RULE_PARSE, rel, exc.lineno or 1, f"python syntax error: {exc.msg}")
    except (OSError, UnicodeDecodeError) as exc:
        return None, Diagnostic(RULE_PARSE, rel, 1, f"python read error: {exc}")


def docstring_node_ids(tree: ast.AST) -> set:
    """``id()`` of string-Constant nodes that are docstrings (first statement of a body)."""
    ids = set()

    def mark_first(body):
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            ids.add(id(body[0].value))

    mark_first(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mark_first(node.body)
    return ids


# --- check 1: effective runtime inventory -------------------------------------

def check_effective_runtime(config: dict, pkg_root: Path, all_files: list) -> list:
    er = config["effective_runtime"]
    exts = set(er["extensions"])
    baseline = er["baseline"]
    maximum = er["maximum"]
    total = sum(count_lines(f) for f in all_files if f.suffix in exts)
    delta = total - baseline
    if total > maximum:
        return [Diagnostic(
            RULE_BUDGET, config["runtime_roots"]["package_root"], 1,
            f"effective runtime {total} lines exceeds maximum {maximum} "
            f"(baseline {baseline} at {er.get('baseline_ref', '?')}, delta {delta:+d}); "
            f"net deletion required, generated/vendor counted")]
    return []


# --- check 2: python dependency direction -------------------------------------

def check_python_dependencies(config: dict, pkg_root: Path, py_files: list) -> list:
    layers = config["layers"]
    prefixes = config.get("import_package_prefixes", [])
    diags = []
    for path in py_files:
        fl = file_layer(path, pkg_root, layers)
        if fl is None:
            continue
        forbidden = layers[fl].get("forbidden_imports", [])
        rel = rel_posix(path, pkg_root)
        tree = _parse(path)
        if tree is None:
            continue
        pkg = file_package(path, pkg_root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                lineno = node.lineno
                candidates = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                lineno = node.lineno
                candidates = importfrom_candidates(pkg, node)
            else:
                continue
            matched = set()
            for mod in candidates:
                norm = normalize_module(mod, prefixes)
                tl = layer_of_module(norm, prefixes)
                if tl is None or tl == fl:
                    continue
                if _import_forbidden(norm, forbidden):
                    matched.add(tl)
            if matched:
                diags.append(Diagnostic(
                    RULE_DEP_PY, rel, lineno,
                    f"{fl} imports forbidden layer(s) {sorted(matched)}"))
    return diags


# --- check 3: typescript dependency direction (cross-host) --------------------

def _host_of_rel(rel: str, host_dirs: list):
    """The host adapter directory a relative path lives under, or ``None``."""
    for d in host_dirs:
        d_posix = Path(d).as_posix()
        if rel == d_posix or rel.startswith(d_posix + "/"):
            return d_posix
    return None


def _ts_extract_loads(tokens: list) -> list:
    """Return ``[(line, spec_or_None, kind)]`` for every module load in the token
    stream.  *spec* is the decoded specifier for literal loads, or ``None`` for a
    nonliteral ``import()``/``require()`` (fail-closed).  *kind* is one of:
    ``import-from``, ``export-from``, ``side-effect``, ``import-equals``,
    ``require``, ``dynamic-import``, ``nonliteral``.

    Recognizes static import/export-from, side-effect import, import-equals
    require, literal require, and literal dynamic import — all via the single
    lexical token surface rather than raw regex accumulation."""
    loads = []
    consumed = set()
    n = len(tokens)

    def is_op(idx, val):
        return idx < n and tokens[idx][0] == "op" and tokens[idx][1] == val

    def is_ident(idx, val):
        return idx < n and tokens[idx][0] == "ident" and tokens[idx][1] == val

    def is_str(idx):
        return idx < n and tokens[idx][0] == "string" and not tokens[idx][3]

    def sole_literal_arg(open_paren_idx):
        """The parenthesized argument is a single literal string (``("x")``) and not a
        concatenation or expression (``("x" + y)``).  Returns the string token or None."""
        if not is_str(open_paren_idx + 1):
            return None
        if not is_op(open_paren_idx + 2, ")"):
            return None
        return tokens[open_paren_idx + 1]

    for i in range(n):
        if i in consumed:
            continue
        t = tokens[i]
        if t[0] != "ident":
            continue
        if t[1] == "import":
            tline = t[2]
            if is_op(i + 1, "("):
                lit = sole_literal_arg(i + 1)
                loads.append((tline, lit[1] if lit else None,
                              "dynamic-import" if lit else "nonliteral"))
            elif is_str(i + 1):
                loads.append((tokens[i + 1][2], tokens[i + 1][1], "side-effect"))
            else:
                j = i + 1
                while j < n and not is_op(j, ";"):
                    if is_op(j, "=") and is_ident(j + 1, "require") and is_op(j + 2, "("):
                        lit = sole_literal_arg(j + 2)
                        if lit:
                            loads.append((lit[2], lit[1], "import-equals"))
                        else:
                            loads.append((tline, None, "nonliteral"))
                        consumed.add(j + 1)
                        break
                    if is_ident(j, "from") and is_str(j + 1):
                        loads.append((tokens[j + 1][2], tokens[j + 1][1], "import-from"))
                        break
                    j += 1
        elif t[1] == "export":
            j = i + 1
            while j < n and not is_op(j, ";"):
                if is_ident(j, "from") and is_str(j + 1):
                    loads.append((tokens[j + 1][2], tokens[j + 1][1], "export-from"))
                    break
                j += 1
        elif t[1] == "require" and i not in consumed:
            tline = t[2]
            if is_op(i + 1, "("):
                lit = sole_literal_arg(i + 1)
                if lit:
                    loads.append((lit[2], lit[1], "require"))
                else:
                    loads.append((tline, None, "nonliteral"))
        elif t[1] in _TS_REQUIRE_GLOBALS:
            # Common bracketed/dotted global require: globalThis["require"](...) or
            # globalThis.require(...).  The loader is reached indirectly, so the
            # dependency is unknown — fail closed rather than trust the specifier.
            tline = t[2]
            j = i + 1
            if is_op(j, ".") and is_ident(j + 1, "require") and is_op(j + 2, "("):
                loads.append((tline, None, "nonliteral"))
                consumed.add(j + 1)
            elif is_op(j, "[") and is_str(j + 1) and tokens[j + 1][1] == "require" \
                    and is_op(j + 2, "]") and is_op(j + 3, "("):
                loads.append((tline, None, "nonliteral"))
                consumed.update({j + 1, j + 2})
    return loads


def check_typescript_dependencies(config: dict, pkg_root: Path, ts_files: list) -> list:
    host_adapters = config["forbidden_cross_host_dependencies"]["host_adapters"]
    # host_adapters are dotted layer ids (adapters.claude); map to directory paths.
    host_dirs = [h.replace(".", "/") for h in host_adapters]
    diags = []
    for path in ts_files:
        rel = rel_posix(path, pkg_root)
        source_host = _host_of_rel(rel, host_dirs)
        if source_host is None:
            continue  # TS outside any host adapter is not a cross-host source
        tokens = _ts_tokens(path.read_text(encoding="utf-8"))
        for line, spec, kind in _ts_extract_loads(tokens):
            if kind == "nonliteral":
                diags.append(Diagnostic(
                    RULE_DEP_TS, rel, line,
                    f"TS host adapter '{source_host}' has nonliteral import()/require() "
                    f"loader; fail closed (dependency unknown)"))
                continue
            if spec is None or spec.startswith("node:"):
                continue
            target_host = None
            if spec.startswith("."):
                try:
                    rrel = (path.parent / spec).resolve().relative_to(pkg_root.resolve()).as_posix()
                except ValueError:
                    continue
                target_host = _host_of_rel(rrel, host_dirs)
            else:
                spec_mod = spec.replace("/", ".")
                for d in host_dirs:
                    d_mod = d.replace("/", ".")
                    if spec_mod == d_mod or spec_mod.startswith(d_mod + "."):
                        target_host = d
                        break
            if target_host and target_host != source_host:
                diags.append(Diagnostic(
                    RULE_DEP_TS, rel, line,
                    f"TS host adapter '{source_host}' imports another host adapter "
                    f"'{target_host}' via '{spec}'"))
    return diags


# --- check 4: thin hooks -------------------------------------------------------

def _is_main_guard(node) -> bool:
    """True for the ``if __name__ == "__main__"`` entry guard (excluded from the bound)."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) \
            and test.left.id == "__name__":
        return any(isinstance(c, ast.Constant) and c.value == "__main__"
                   for c in test.comparators)
    return False


def count_executable_statements(tree) -> int:
    """Count executable ``ast.stmt`` nodes recursively. Imports (bootstrapping) and the
    ``if __name__ == "__main__"`` guard wrapper are excluded, but the guard's body and any
    nested function/class bodies count — so a one-function hook hiding 50 statements of
    policy still exceeds the configured bound and cannot evade the rule."""
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and not isinstance(node, (ast.Import, ast.ImportFrom)):
            if _is_main_guard(node):
                continue
            n += 1
    return n


def check_thin_hooks(config: dict, pkg_root: Path) -> list:
    th = config["thin_hooks"]
    prefixes = config.get("import_package_prefixes", [])
    hooks_root = Path(pkg_root) / th["root"]
    if not hooks_root.is_dir():
        return []
    max_statements = th["max_statements"]
    max_functions = th["max_functions"]
    forbidden_imports = th["forbidden_domain_imports"]
    diags = []
    for path in sorted(hooks_root.rglob("*.py")):
        rel = rel_posix(path, pkg_root)
        tree = _parse(path)
        if tree is None:
            diags.append(Diagnostic(RULE_THIN_HOOK, rel, 1, "hook is not parseable"))
            continue
        n_statements = count_executable_statements(tree)
        n_functions = sum(1 for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        if n_statements > max_statements:
            diags.append(Diagnostic(
                RULE_THIN_HOOK, rel, 1,
                f"hook has {n_statements} executable statements (max {max_statements}); "
                f"not a thin bootstrap"))
        if n_functions > max_functions:
            diags.append(Diagnostic(
                RULE_THIN_HOOK, rel, 1,
                f"hook defines {n_functions} functions (max {max_functions}); owns domain logic"))
        pkg = file_package(path, pkg_root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    nm = normalize_module(alias.name, prefixes)
                    if _import_forbidden(nm, forbidden_imports):
                        diags.append(Diagnostic(
                            RULE_THIN_HOOK, rel, node.lineno,
                            f"hook imports domain layer '{alias.name}'"))
            elif isinstance(node, ast.ImportFrom):
                base = resolve_relative(pkg, node.level or 0, node.module) if node.level else (node.module or "")
                nbase = normalize_module(base, prefixes)
                for alias in node.names:
                    cand = (nbase + "." + alias.name) if nbase else alias.name
                    if _import_forbidden(cand, forbidden_imports) or (nbase and _import_forbidden(nbase, forbidden_imports)):
                        diags.append(Diagnostic(
                            RULE_THIN_HOOK, rel, node.lineno,
                            f"hook imports domain layer '{cand}'"))
    return diags


# --- check 5: forbidden files/symbols ------------------------------------------

def _forbidden_sets(config: dict) -> tuple:
    ff = config["forbidden_fields"]
    fa = config["forbidden_actions"]
    names = set()
    attrs = set()
    strs = set()
    for group in ff.values():
        if isinstance(group, dict):
            names.update(group.get("names", []))
            attrs.update(group.get("attributes", []))
            strs.update(group.get("string_constants", []))
    funcs = set(ff.get("function_names", []))
    names.update(fa.get("constants", []))
    attrs.update(fa.get("constants", []))
    strs.update(fa.get("literal_values", []))
    return names, attrs, strs, funcs


def check_forbidden_paths(config: dict, pkg_root: Path) -> list:
    diags = []
    for forbidden in config["forbidden_paths"]:
        if (Path(pkg_root) / forbidden).exists():
            diags.append(Diagnostic(
                RULE_FORBIDDEN_PATH, forbidden, 1,
                f"forbidden path exists: {forbidden}"))
    return diags


def check_forbidden_symbols(config: dict, pkg_root: Path, py_files: list) -> list:
    """Forbidden names/attributes/string-literals/functions across ALL discovered production
    Python. ``py_files`` already excludes tests/docs/contracts (runtime discovery), so the
    check reaches core and vendor too: a forbidden form relocated to core cannot evade it."""
    names, attrs, strs, funcs = _forbidden_sets(config)
    diags = []
    for path in py_files:
        rel = rel_posix(path, pkg_root)
        tree = _parse(path)
        if tree is None:
            continue
        doc_ids = docstring_node_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if node.id in names:
                    diags.append(Diagnostic(
                        RULE_FORBIDDEN_SYMBOL, rel, node.lineno,
                        f"forbidden symbol/field '{node.id}'"))
            elif isinstance(node, ast.Attribute):
                if node.attr in attrs:
                    diags.append(Diagnostic(
                        RULE_FORBIDDEN_SYMBOL, rel, node.lineno,
                        f"forbidden field '{node.attr}'"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in doc_ids and node.value in strs:
                    diags.append(Diagnostic(
                        RULE_FORBIDDEN_SYMBOL, rel, node.lineno,
                        f"forbidden literal '{node.value}'"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in funcs:
                    diags.append(Diagnostic(
                        RULE_FORBIDDEN_SYMBOL, rel, node.lineno,
                        f"forbidden function '{node.name}'"))
    return diags


# --- check 4b: parse integrity (fail-closed) -----------------------------------

def check_parse_integrity(config: dict, pkg_root: Path, py_files: list) -> list:
    """A production Python parse failure is itself a fail-closed diagnostic with path:line;
    no AST-dependent check may leave a malformed module silently architecture-clean."""
    diags = []
    for path in py_files:
        rel = rel_posix(path, pkg_root)
        _, err = _parse_with_error(path, rel)
        if err is not None:
            diags.append(err)
    return diags


# --- check 5b: direct kind='evidence' boolean approval (structural) -------------

def _is_kind_operand(node) -> bool:
    """True when *node* is exactly ``kind``, ``obj.kind``, or ``obj['kind']`` —
    the three nonliteral operands the direct-evidence rule matches."""
    if isinstance(node, ast.Name) and node.id == "kind":
        return True
    if isinstance(node, ast.Attribute) and node.attr == "kind":
        return True
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value == "kind":
            return True
        if hasattr(ast, "Index") and isinstance(sl, ast.Index) \
                and isinstance(sl.value, ast.Constant) and sl.value.value == "kind":
            return True
    return False


def check_direct_evidence_approval(config: dict, pkg_root: Path, py_files: list) -> list:
    """Structurally detect the direct ``kind == 'evidence'`` boolean-approval equality
    (and its mirror ``'evidence' == kind``).  The nonliteral operand must be exactly
    ``kind``, ``obj.kind``, or ``obj['kind']`` — unrelated classification such as
    ``source_type == 'evidence'`` and evidence-object construction are not flagged."""
    literal = config.get("forbidden_symbols", {}).get("evidence_approval_literal")
    if not literal:
        return []
    diags = []
    for path in py_files:
        rel = rel_posix(path, pkg_root)
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                continue
            operands = [node.left, *node.comparators]
            if not any(isinstance(o, ast.Constant) and o.value == literal for o in operands):
                continue
            nonliteral = [o for o in operands
                          if not (isinstance(o, ast.Constant) and o.value == literal)]
            if any(_is_kind_operand(o) for o in nonliteral):
                diags.append(Diagnostic(
                    RULE_FORBIDDEN_SYMBOL, rel, node.lineno,
                    f"direct kind='{literal}' boolean approval comparison"))
    return diags


# --- check 5c: narrow location-preserving TypeScript token surface -------------

_TS_MULTI_OPS3 = ("===", "!==", ">>>", "**=", "...", "<<=", ">>=")
_TS_MULTI_OPS2 = ("==", "!=", "<=", ">=", "=>", "&&", "||", "++", "--", "+=", "-=",
                  "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "?.", "??")
_TS_SIMPLE_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
    "v": "\v", "0": "\0", "\\": "\\", "'": "'", '"': '"', "`": "`",
}
_TS_EQ_OPS = ("==", "===", "!=", "!==")

# Keywords after which a '/' starts a regex literal rather than division (JS lexing
# convention): these cannot end an expression, so '/' cannot be dividing by them.
_TS_REGEX_KW = {
    "return", "typeof", "instanceof", "in", "of", "do", "else", "case", "new",
    "delete", "void", "yield", "await", "throw", "if", "while", "for", "switch",
    "catch",
}
# Reserved/statement keywords that are not function callees, so a '(' after one
# is a grouping paren, not a call paren (used when stripping operand parens).
_TS_NON_CALLEE_KW = {
    "return", "if", "while", "for", "switch", "case", "do", "else", "throw",
    "try", "catch", "finally", "function", "class", "extends", "super", "this",
    "true", "false", "null", "undefined", "break", "continue", "default",
    "let", "const", "var", "export", "import", "from", "as", "type", "enum",
    "satisfies", "abstract", "declare", "module", "namespace", "interface",
    "implements", "private", "protected", "public", "readonly", "static",
    "override", "keyof", "infer", "is", "out", "asserts",
}
# Unary-prefix keywords: a '(' after one wraps a unary operand (e.g. ``typeof (x)``),
# so the paren is internal to a larger expression, not a wrapping comparison operand.
_TS_INNER_PAREN_KW = {"typeof", "delete", "void", "await", "yield", "new"}
# Operators that bind at least as tightly as equality, or start member/call access:
# when one precedes a '(', that '(' is internal (or a call), not the grouping paren
# of a comparison operand, so it must not be stripped.
_TS_INNER_PAREN_OPS = {
    "==", "===", "!=", "!==", "+", "-", "*", "/", "%", "**", "<", ">", "<=",
    ">=", "&", "|", "^", "<<", ">>", ">>>", "!", "~", ".", "?.", "[",
}
# Operators tighter than equality that extend an operand past a ')': a leading
# '(' whose matching ')' is followed by one of these did not wrap the whole
# operand (e.g. ``(kind) + x``), so it is left unstripped.
_TS_OPERAND_CONTINUING_OPS = {
    "+", "-", "*", "/", "%", "**", "<", ">", "<=", ">=", "&", "|", "^", "<<",
    ">>", ">>>", ".", "?.", "[", "(",
}
_TS_OPERAND_CONTINUING_KW = {"in", "instanceof"}
# Common globals through which an indirect ``require`` is reached; both the
# dotted (``globalThis.require``) and bracketed (``globalThis["require"]``)
# forms fail closed because the loader is nonliteral/unknown.
_TS_REQUIRE_GLOBALS = ("globalThis", "self", "window", "global")


def _ts_decode_escape(text: str, i: int) -> tuple:
    """Decode one JS/TS quoted-string escape starting at ``text[i] == '\\'``.
    Returns ``(decoded_str, consumed)`` where *consumed* counts from the backslash.
    Handles simple (``\\n``, ``\\t`` …), hex (``\\xNN``), unicode (``\\uNNNN``),
    and codepoint (``\\u{N+}``) escapes; a ``\\<newline>`` line continuation yields ""."""
    n = len(text)
    if i + 1 >= n:
        return "", 1
    nxt = text[i + 1]
    if nxt in _TS_SIMPLE_ESCAPES:
        return _TS_SIMPLE_ESCAPES[nxt], 2
    if nxt == "x":
        h = text[i + 2:i + 4]
        if len(h) == 2 and all(c in "0123456789abcdefABCDEF" for c in h):
            return chr(int(h, 16)), 4
        return "", 2
    if nxt == "u":
        if i + 2 < n and text[i + 2] == "{":
            j = i + 3
            h = []
            while j < n and text[j] != "}":
                h.append(text[j])
                j += 1
            hs = "".join(h)
            if j < n and text[j] == "}" and hs and all(c in "0123456789abcdefABCDEF" for c in hs):
                try:
                    return chr(int(hs, 16)), j - i + 1
                except (ValueError, OverflowError):
                    pass
            return "", 2
        h = text[i + 2:i + 6]
        if len(h) == 4 and all(c in "0123456789abcdefABCDEF" for c in h):
            return chr(int(h, 16)), 6
        return "", 2
    if nxt == "\n":
        return "", 2
    return nxt, 2


def _ts_slash_is_regex(prev) -> bool:
    """Decide whether a ``/`` starts a regex literal or is division, from the previous
    *emitted* token (the standard JS lexing convention).  A regex may follow an
    operator, punctuation, or a keyword that cannot end an expression; division
    follows an identifier, number, string, or a closing bracket."""
    if prev is None:
        return True
    pt, pv = prev[0], prev[1]
    if pt == "op":
        return pv not in (")", "]", "}")
    if pt == "ident":
        return pv in _TS_REGEX_KW
    return False  # num / string end an expression -> division


def _ts_scan_regex(text: str, i: int, line: int):
    """Skip a regex literal starting at ``text[i] == '/'``, returning ``(new_i, line)``
    or ``None`` when it is not a well-formed single-line regex (it spans a line or is
    never closed).  Character classes ``[...]`` and ``\\`` escapes are honoured so a
    ``/`` or ``]`` inside them does not prematurely close the literal; trailing
    flags (``g``, ``i``, …) are consumed.  No tokens are emitted, so policy-looking
    text in a regex cannot be mistaken for code — while line numbers are preserved."""
    n = len(text)
    j = i + 1
    in_class = False
    while j < n:
        c = text[j]
        if c == "\n":
            return None  # regex literals cannot contain a line terminator
        if c == "\\" and j + 1 < n:
            j += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
            j += 1
            continue
        if c == "[":
            in_class = True
            j += 1
            continue
        if c == "/":
            j += 1
            break
        j += 1
    else:
        return None  # unterminated
    while j < n and text[j].isalpha():
        j += 1
    return (j, line)


def _ts_tokens(text: str, start_line: int = 1) -> list:
    """Tokenize TypeScript source, skipping ``//`` and ``/* */`` comments and regex
    literals.  Returns a list of ``(type, value, line, is_template)`` tuples.

    Quoted strings and *no-substitution* template literals are escape-decoded so a
    forbidden literal hidden in either is visible.  Interpolated template literals are
    opaque (``value=None``) — an interpolated module specifier stays nonliteral — but
    their ``${...}`` expression bodies are tokenized recursively (at the correct line
    offset) so policy-relevant comparisons remain visible.  Regex literals are skipped
    whole (with line preservation) so policy-looking contents do not become code.

    This is a narrow lexical surface for structural checks, not a soundness proof:
    arbitrary ``eval``/generated/computed code is outside this static contract.
    Token types: ``string``, ``ident``, ``op``, ``num``."""
    tokens = []
    i = 0
    n = len(text)
    line = start_line
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f":
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] == "\n":
                    line += 1
                i += 1
            i = min(i + 2, n)
            continue
        if c == "/":
            if _ts_slash_is_regex(tokens[-1] if tokens else None):
                res = _ts_scan_regex(text, i, line)
                if res is not None:
                    i, line = res
                    continue
            # otherwise: division operator, fall through to the op handlers
        if c == "'" or c == '"':
            quote = c
            tok_line = line
            i += 1
            buf = []
            while i < n:
                ch = text[i]
                if ch == quote:
                    i += 1
                    break
                if ch == "\\" and i + 1 < n:
                    decoded, consumed = _ts_decode_escape(text, i)
                    if decoded:
                        buf.append(decoded)
                    for cc in text[i:i + consumed]:
                        if cc == "\n":
                            line += 1
                    i += consumed
                    continue
                if ch == "\n":
                    line += 1
                    break
                buf.append(ch)
                i += 1
            tokens.append(("string", "".join(buf), tok_line, False))
            continue
        if c == "`":
            tok_line = line
            i += 1
            buf = []
            has_interp = False
            while i < n:
                ch = text[i]
                if ch == "`":
                    i += 1
                    break
                if ch == "$" and i + 1 < n and text[i + 1] == "{":
                    has_interp = True
                    dollar_line = line
                    i += 2
                    body_start = i
                    depth = 1
                    while i < n and depth > 0:
                        ic = text[i]
                        if ic in ("'", '"', "`"):
                            sq = ic
                            i += 1
                            while i < n and text[i] != sq:
                                if text[i] == "\\" and i + 1 < n:
                                    if text[i + 1] == "\n":
                                        line += 1
                                    i += 2
                                    continue
                                if text[i] == "\n":
                                    line += 1
                                i += 1
                            if i < n:
                                i += 1
                            continue
                        if ic == "\\" and i + 1 < n:
                            if text[i + 1] == "\n":
                                line += 1
                            i += 2
                            continue
                        if ic == "{":
                            depth += 1
                        elif ic == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        if ic == "\n":
                            line += 1
                        i += 1
                    body_text = text[body_start:i]
                    if i < n and text[i] == "}":
                        i += 1
                    # Recursively tokenize the expression body at the right line
                    # offset, so a comparison such as ``kind === 'evidence'`` inside
                    # ``${...}`` is visible.  The template itself stays nonliteral.
                    tokens.extend(_ts_tokens(body_text, dollar_line))
                    continue
                if ch == "\\" and i + 1 < n:
                    decoded, consumed = _ts_decode_escape(text, i)
                    if decoded:
                        buf.append(decoded)
                    for cc in text[i:i + consumed]:
                        if cc == "\n":
                            line += 1
                    i += consumed
                    continue
                if ch == "\n":
                    line += 1
                buf.append(ch)
                i += 1
            if has_interp:
                tokens.append(("string", None, tok_line, True))
            else:
                tokens.append(("string", "".join(buf), tok_line, False))
            continue
        if c.isalpha() or c == "_" or c == "$":
            start = i
            while i < n and (text[i].isalnum() or text[i] in "_$"):
                i += 1
            tokens.append(("ident", text[start:i], line, False))
            continue
        if c.isdigit():
            start = i
            while i < n and text[i].isalnum():
                i += 1
            tokens.append(("num", text[start:i], line, False))
            continue
        three = text[i:i + 3]
        if three in _TS_MULTI_OPS3:
            tokens.append(("op", three, line, False))
            i += 3
            continue
        two = text[i:i + 2]
        if two in _TS_MULTI_OPS2:
            tokens.append(("op", two, line, False))
            i += 2
            continue
        if c in "=<>!+-*/%&|^~?.,;:()[]{}@":
            tokens.append(("op", c, line, False))
            i += 1
            continue
        i += 1
    return tokens


def _ts_match_open(tokens, close_idx):
    """Index of the '(' matching the ')' at ``close_idx``, or ``None`` if unbalanced."""
    depth = 0
    for idx in range(close_idx, -1, -1):
        t = tokens[idx]
        if t[0] == "op" and t[1] == ")":
            depth += 1
        elif t[0] == "op" and t[1] == "(":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _ts_match_close(tokens, open_idx, n):
    """Index of the ')' matching the '(' at ``open_idx``, or ``None`` if unbalanced."""
    depth = 0
    for idx in range(open_idx, n):
        t = tokens[idx]
        if t[0] == "op" and t[1] == "(":
            depth += 1
        elif t[0] == "op" and t[1] == ")":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _ts_strip_left_parens(tokens, end, n):
    """``tokens[:end]`` is the left side of a comparison.  Repeatedly strip a trailing
    wrapping ``(...)`` pair that groups the whole operand (not a call ``foo(...)`` or an
    internal sub-expression ``... + (...)``).  Returns the operand content sublist.

    Stripping is allowed when the token before '(' is an operator (other than the
    tight ones in ``_TS_INNER_PAREN_OPS``) or a non-callee keyword, or there is no
    preceding token.  ``&&``/``||`` allow stripping because equality binds tighter."""
    toks = list(tokens[:end])
    while toks and toks[-1][0] == "op" and toks[-1][1] == ")":
        close = len(toks) - 1
        open_idx = _ts_match_open(toks, close)
        if open_idx is None:
            break
        if open_idx == 0:
            toks = toks[1:close]
            continue
        before = toks[open_idx - 1]
        if before[0] == "op" and before[1] in _TS_INNER_PAREN_OPS:
            break
        if before[0] == "ident":
            if before[1] in _TS_INNER_PAREN_KW or before[1] not in _TS_NON_CALLEE_KW:
                break
        toks = toks[open_idx + 1:close]
    return toks


def _ts_strip_right_parens(tokens, start, n):
    """``tokens[start:]`` is the right side of a comparison.  Repeatedly strip a leading
    wrapping ``(...)`` pair that groups the whole operand (not a call or an internal
    sub-expression).  Returns the operand content sublist.

    The leading '(' is stripped together with its matching ')' (found by depth, not by
    position), so a surrounding ``if (...)`` closing paren after the operand does not
    block the strip.  Over-stripping is prevented by requiring the token after the
    matching ')' to be a boundary, not an operand-continuing operator (so ``(x) + y``
    is not reduced to ``x``)."""
    toks = list(tokens[start:n])
    while len(toks) >= 2 and toks[0][0] == "op" and toks[0][1] == "(":
        close = _ts_match_close(toks, 0, len(toks))
        if close is None or close < 2:
            break
        after = toks[close + 1] if close + 1 < len(toks) else None
        if after is not None:
            if after[0] == "op" and after[1] in _TS_OPERAND_CONTINUING_OPS:
                break
            if after[0] == "ident" and after[1] in _TS_OPERAND_CONTINUING_KW:
                break
        toks = toks[1:close]
    return toks


def _ts_left_operand(tokens, eq_idx, literal):
    """Return ``(is_kind, is_literal, line)`` for the operand ending just before
    ``tokens[eq_idx]`` (an equality operator).  Balanced wrapping parentheses are
    normalized, and ``obj.kind`` / ``obj?.kind`` / ``obj['kind']`` are recognized."""
    if eq_idx < 1:
        return False, False, 0
    content = _ts_strip_left_parens(tokens, eq_idx, len(tokens))
    if not content:
        return False, False, 0
    prev = content[-1]
    if prev[0] == "string" and not prev[3] and prev[1] == literal:
        return False, True, prev[2]
    if prev[0] == "ident" and prev[1] == "kind":
        return True, False, prev[2]
    if prev[0] == "op" and prev[1] == "]" and len(content) >= 3 \
            and content[-2][0] == "string" and not content[-2][3] \
            and content[-2][1] == "kind" \
            and content[-3][0] == "op" and content[-3][1] == "[":
        return True, False, content[-2][2]
    return False, False, 0


def _ts_right_operand(tokens, eq_idx, literal):
    """Return ``(is_kind, is_literal, line)`` for the operand starting just after
    ``tokens[eq_idx]`` (an equality operator).  Balanced wrapping parentheses are
    normalized, and ``obj.kind`` / ``obj?.kind`` / ``obj['kind']`` are recognized in
    either comparison direction."""
    n = len(tokens)
    if eq_idx + 1 >= n:
        return False, False, 0
    content = _ts_strip_right_parens(tokens, eq_idx + 1, n)
    if not content:
        return False, False, 0
    nxt = content[0]
    if nxt[0] == "string" and not nxt[3] and nxt[1] == literal:
        return False, True, nxt[2]
    if nxt[0] == "ident" and nxt[1] == "kind":
        # bare 'kind' is an operand; 'kind.x' / 'kind?.x' (member access on kind) is not
        if len(content) < 2 or not (content[1][0] == "op" and content[1][1] in (".", "?.")):
            return True, False, nxt[2]
        return False, False, 0
    if nxt[0] == "ident" and len(content) >= 3 \
            and content[1][0] == "op" and content[1][1] in (".", "?.") \
            and content[2][0] == "ident" and content[2][1] == "kind":
        return True, False, content[2][2]
    if nxt[0] == "ident" and len(content) >= 4 \
            and content[1][0] == "op" and content[1][1] == "[" \
            and content[2][0] == "string" and not content[2][3] and content[2][1] == "kind" \
            and content[3][0] == "op" and content[3][1] == "]":
        return True, False, content[2][2]
    return False, False, 0


def _ts_direct_evidence_hits(tokens, rel, literal):
    """Detect direct ``kind == '<literal>'`` equality comparisons in a TS token stream:
    one operand is the literal string, the other is ``kind``, ``.kind``, or ``['kind']``."""
    diags = []
    seen = set()
    n = len(tokens)
    for i in range(n):
        t = tokens[i]
        if t[0] != "op" or t[1] not in _TS_EQ_OPS:
            continue
        lk, ll, lline = _ts_left_operand(tokens, i, literal)
        rk, rl, rline = _ts_right_operand(tokens, i, literal)
        if (ll and rk) or (rl and lk):
            line = lline or rline or t[2]
            if line not in seen:
                seen.add(line)
                diags.append(Diagnostic(
                    RULE_FORBIDDEN_SYMBOL, rel, line,
                    f"direct kind='{literal}' boolean approval comparison"))
    return diags


def check_typescript_forbidden_symbols(config: dict, pkg_root: Path, ts_files: list) -> list:
    """Narrow token/string-aware TypeScript equivalent of the Python forbidden-symbol
    check: quoted string literals, identifiers, and ``.field`` accesses matching the
    configured forbidden sets.  Comments are skipped (the tokenizer handles that);
    template literals are opaque.  The evidence-approval literal is handled by the
    separate direct-evidence check, not here, so evidence construction is not flagged."""
    names, attrs, strs, _funcs = _forbidden_sets(config)
    diags = []
    for path in ts_files:
        rel = rel_posix(path, pkg_root)
        tokens = _ts_tokens(path.read_text(encoding="utf-8"))
        seen = set()
        for idx in range(len(tokens)):
            t = tokens[idx]
            if t[0] == "string" and not t[3] and t[1] is not None and t[1] in strs:
                key = (t[2], "str", t[1])
                if key not in seen:
                    seen.add(key)
                    diags.append(Diagnostic(RULE_FORBIDDEN_SYMBOL, rel, t[2],
                                            f"forbidden literal '{t[1]}'"))
            elif t[0] == "ident":
                is_attr = idx > 0 and tokens[idx - 1][0] == "op" and tokens[idx - 1][1] == "."
                if is_attr:
                    if t[1] in attrs:
                        key = (t[2], "attr", t[1])
                        if key not in seen:
                            seen.add(key)
                            diags.append(Diagnostic(RULE_FORBIDDEN_SYMBOL, rel, t[2],
                                                    f"forbidden field '{t[1]}'"))
                elif t[1] in names:
                    key = (t[2], "ident", t[1])
                    if key not in seen:
                        seen.add(key)
                        diags.append(Diagnostic(RULE_FORBIDDEN_SYMBOL, rel, t[2],
                                                f"forbidden symbol/field '{t[1]}'"))
    return diags


def check_typescript_direct_evidence_approval(config: dict, pkg_root: Path, ts_files: list) -> list:
    """Token-level equivalent of the Python direct-evidence-approval check: detect a
    comparison (``==``/``===``/``!=``/``!==``) where one operand is the evidence literal
    string and the other is ``kind``, ``.kind``, or ``['kind']``.  Construction and
    non-kind classification are not flagged — identical narrowing to the Python rule."""
    literal = config.get("forbidden_symbols", {}).get("evidence_approval_literal")
    if not literal:
        return []
    diags = []
    for path in ts_files:
        rel = rel_posix(path, pkg_root)
        tokens = _ts_tokens(path.read_text(encoding="utf-8"))
        diags.extend(_ts_direct_evidence_hits(tokens, rel, literal))
    return diags


# --- check 6: forbidden protocol/action/state text ----------------------------

def check_v1_protocol(config: dict, pkg_root: Path, py_files: list, ts_files: list) -> list:
    protocols = set(config["forbidden_runtime_protocols"])
    diags = []
    for path in py_files:
        rel = rel_posix(path, pkg_root)
        tree = _parse(path)
        if tree is None:
            continue
        doc_ids = docstring_node_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value in protocols and id(node) not in doc_ids:
                diags.append(Diagnostic(
                    RULE_V1_PROTOCOL, rel, node.lineno,
                    f"forbidden runtime protocol literal '{node.value}'"))
    for path in ts_files:
        rel = rel_posix(path, pkg_root)
        tokens = _ts_tokens(path.read_text(encoding="utf-8"))
        seen = set()
        for t in tokens:
            if t[0] == "string" and not t[3] and t[1] is not None and t[1] in protocols:
                key = (t[2], t[1])
                if key not in seen:
                    seen.add(key)
                    diags.append(Diagnostic(
                        RULE_V1_PROTOCOL, rel, t[2],
                        f"forbidden runtime protocol literal '{t[1]}'"))
    return diags


# --- check 7: adapter policy boundary (domain adjudicators) --------------------

def check_adapter_adjudicators(config: dict, pkg_root: Path, py_files: list) -> list:
    adjudicators = set(config["forbidden_symbols"]["domain_adjudicators"])
    diags = []
    for path in py_files:
        rel = rel_posix(path, pkg_root)
        if not rel.startswith("adapters/"):
            continue
        tree = _parse(path)
        if tree is None:
            continue
        seen = set()
        for node in ast.walk(tree):
            hit = None
            if isinstance(node, ast.Name) and node.id in adjudicators:
                hit = f"references domain adjudicator '{node.id}'"
            elif isinstance(node, ast.Attribute) and node.attr in adjudicators:
                hit = f"references domain adjudicator '{node.attr}'"
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in adjudicators:
                        hit = f"imports domain adjudicator '{alias.name}'"
                        break
            if hit and (node.lineno, hit) not in seen:
                seen.add((node.lineno, hit))
                diags.append(Diagnostic(RULE_ADAPTER_ADJUDICATOR, rel, node.lineno, hit))
    return diags


# --- check 8: public-contract alignment ----------------------------------------

def check_contract_references(config: dict, repo_root: Path) -> list:
    diags = []
    rc = config["required_contract_references"]
    pc_path = Path(repo_root) / rc["public_contract"]
    if not pc_path.exists():
        diags.append(Diagnostic(
            RULE_CONTRACT_REF, rc["public_contract"], 1,
            f"required public contract not found: {rc['public_contract']}"))
        pc = {}
    else:
        try:
            pc = json.loads(pc_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - report any load failure deterministically
            diags.append(Diagnostic(
                RULE_CONTRACT_REF, rc["public_contract"], 1,
                f"required public contract is not valid JSON: {exc}"))
            pc = {}
    for key, expected in (("id", rc["id"]), ("version", rc["version"]), ("protocol", rc["protocol"])):
        actual = pc.get(key)
        if actual != expected:
            diags.append(Diagnostic(
                RULE_CONTRACT_REF, rc["public_contract"], 1,
                f"public contract {key}={actual!r} != required {expected!r}"))
    rp = config["required_profile_references"]
    hp_path = Path(repo_root) / rp["host_profiles"]
    if not hp_path.exists():
        diags.append(Diagnostic(
            RULE_CONTRACT_REF, rp["host_profiles"], 1,
            f"required host profiles not found: {rp['host_profiles']}"))
        hp = {}
    else:
        try:
            hp = json.loads(hp_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            diags.append(Diagnostic(
                RULE_CONTRACT_REF, rp["host_profiles"], 1,
                f"required host profiles is not valid JSON: {exc}"))
            hp = {}
    if hp.get("protocol") != rp["protocol"]:
        diags.append(Diagnostic(
            RULE_CONTRACT_REF, rp["host_profiles"], 1,
            f"host profiles protocol={hp.get('protocol')!r} != required {rp['protocol']!r}"))
    profile_ids = {p.get("profile_id") for p in hp.get("profiles", []) if isinstance(p, dict)}
    for pid in rp["required_profile_ids"]:
        if pid not in profile_ids:
            diags.append(Diagnostic(
                RULE_CONTRACT_REF, rp["host_profiles"], 1,
                f"required host profile id not found: {pid}"))
    return diags


# --- check 9: make lifecycle ---------------------------------------------------

def _is_target_def(line: str, name: str) -> bool:
    return re.match(rf"^{re.escape(name)}\s*:", line) is not None


def _recipe_lines(lines: list, target: str) -> list:
    recipe = []
    in_target = False
    for line in lines:
        if _is_target_def(line, target):
            in_target = True
            continue
        if in_target:
            if line.startswith("\t"):
                recipe.append(line)
            elif line.strip() == "":
                continue
            else:
                in_target = False
    return recipe


def check_make_lifecycle(config: dict, repo_root: Path) -> list:
    diags = []
    makefile = Path(repo_root) / "Makefile"
    if not makefile.exists():
        return [Diagnostic(RULE_MAKE_LIFECYCLE, "Makefile", 1, "Makefile not found")]
    text = makefile.read_text(encoding="utf-8")
    lines = text.splitlines()
    target = config["make_target"]
    script_name = Path(config["make_validator_script"]).name
    target_def_lines = [idx for idx, line in enumerate(lines, 1)
                        if _is_target_def(line, target)]
    if not target_def_lines:
        diags.append(Diagnostic(
            RULE_MAKE_LIFECYCLE, "Makefile", 1,
            f"make target '{target}' is not defined"))
    else:
        if not any("##" in lines[idx - 1] for idx in target_def_lines):
            diags.append(Diagnostic(
                RULE_MAKE_LIFECYCLE, "Makefile", target_def_lines[0],
                f"make target '{target}' has no '##' help annotation"))
        recipe = _recipe_lines(lines, target)
        if not any(script_name in line for line in recipe):
            diags.append(Diagnostic(
                RULE_MAKE_LIFECYCLE, "Makefile", 1,
                f"make target '{target}' does not invoke validator script '{script_name}'"))
    for bad in config["forbidden_make_text"]:
        if any(_is_target_def(line, bad) for line in lines):
            diags.append(Diagnostic(
                RULE_MAKE_LIFECYCLE, "Makefile", 1,
                f"forbidden make target '{bad}' is present"))
    terms = config["make_history_rewrite_terms"]
    for index, line in enumerate(lines, 1):
        if not line.startswith("\t"):
            continue
        bare = line.lstrip("\t").lstrip("@-").lstrip()
        # Inspect the WHOLE recipe shell text: a history mutation after `echo`/`printf`
        # or a `;`-separated command (e.g. `@echo git push; git push origin main`) must
        # not evade the rule by virtue of the line's first command.
        for term in terms:
            if term in bare:
                diags.append(Diagnostic(
                    RULE_MAKE_LIFECYCLE, "Makefile", index,
                    f"make recipe performs git history mutation '{term}'"))
                break
    return diags


# --- orchestration + rendering -------------------------------------------------

def run_all(config: dict, repo_root: Path) -> list:
    """Run every check and return sorted, de-duplicated diagnostics."""
    pkg_root = Path(repo_root) / config["runtime_roots"]["package_root"]
    all_files = discover_runtime_files(config, repo_root)
    py_files = [f for f in all_files if f.suffix == ".py"]
    ts_files = [f for f in all_files if f.suffix == ".ts"]
    diags = []
    diags += check_effective_runtime(config, pkg_root, all_files)
    diags += check_parse_integrity(config, pkg_root, py_files)
    diags += check_python_dependencies(config, pkg_root, py_files)
    diags += check_typescript_dependencies(config, pkg_root, ts_files)
    diags += check_thin_hooks(config, pkg_root)
    diags += check_forbidden_paths(config, pkg_root)
    diags += check_forbidden_symbols(config, pkg_root, py_files)
    diags += check_direct_evidence_approval(config, pkg_root, py_files)
    diags += check_typescript_forbidden_symbols(config, pkg_root, ts_files)
    diags += check_typescript_direct_evidence_approval(config, pkg_root, ts_files)
    diags += check_v1_protocol(config, pkg_root, py_files, ts_files)
    diags += check_adapter_adjudicators(config, pkg_root, py_files)
    diags += check_contract_references(config, repo_root)
    diags += check_make_lifecycle(config, repo_root)
    return sorted(set(diags), key=lambda d: d.sort_key())


def render(diagnostics: list, inv: tuple | None = None) -> str:
    lines = []
    if inv is not None:
        total, baseline, maximum, delta, nfiles, ref = inv
        lines.append(
            f"effective runtime: {total} lines across {nfiles} files "
            f"(baseline {baseline} at {ref}, maximum {maximum}, delta {delta:+d})")
    if not diagnostics:
        lines.append("architecture: ok — no target-state violations")
        return "\n".join(lines)
    by_rule: dict = {}
    for diag in diagnostics:
        by_rule[diag.rule_id] = by_rule.get(diag.rule_id, 0) + 1
    summary = ", ".join(f"{rule}={count}" for rule, count in sorted(by_rule.items()))
    lines.append(f"architecture: {len(diagnostics)} target-state violation(s) [{summary}]")
    for diag in diagnostics:
        lines.append(f"  {diag.rule_id} {diag.path}:{diag.line} {diag.message}")
    return "\n".join(lines)


def run_self_test() -> int:
    test_file = Path(__file__).resolve().parent / "tests" / "test_validate_empirica_architecture.py"
    if not test_file.exists():
        print(f"self-test: synthetic suite not found: {test_file}", file=sys.stderr)
        return 2
    result = subprocess.run([sys.executable, str(test_file)], cwd=str(test_file.parent.parent.parent))
    return result.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Empirica 2.0 architecture target-state validator.")
    parser.add_argument("--config", default="plugins/empirica/architecture.json",
                        help="path to architecture.json (default: plugins/empirica/architecture.json)")
    parser.add_argument("--root", default=".",
                        help="repository root (default: current directory)")
    parser.add_argument("--self-test", action="store_true",
                        help="run the committed synthetic test suite instead of validating the repo")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    config = load_config(Path(args.root) / args.config if not Path(args.config).is_absolute() else Path(args.config))
    diagnostics = run_all(config, Path(args.root))
    print(render(diagnostics, inventory(config, Path(args.root))))
    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
