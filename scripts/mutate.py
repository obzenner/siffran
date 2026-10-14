#!/usr/bin/env python3
"""``make mutate``: mutation testing of the changed lines (T3), each surviving mutant reported as a finding.

    python3 scripts/mutate.py [--files a.py,b.js:10-20] [--lines 10-20,30-31] [--runner CMD] [--dry-run]

* **Scope:** the changed lines of each changed source (``git diff -U0 HEAD``'s hunks) and every line of an untracked one,
  the way Google mutates a change under review, not the codebase. ``--files`` (``FILES=``) names whole files instead, a
  file may carry its own ``:start-end``; ``--lines`` (``LINES=``) gives the ranges of every named file without its own.
  Tests, docs, vendored code and the UI's build scripts are never mutated.
* **The UI** (``plugins/empirica/ui/{app,elements}/**.js``): one Stryker run (``stryker.config.json``) with
  ``--mutate file:start-end,…``. Its Vitest runner runs, for each mutant, only the tests that cover it (perTest) among
  those that import the file (``vitest.related``); incremental mode (``reports/stryker-incremental.json``, gitignored)
  reuses every result whose code and tests did not change, so a repeat run with nothing changed tests nothing.
* **Python:** mutmut 2.5.1 (``make dev-install``) with ``--use-patch-file``: a patch of the scope's lines, so it
  mutates only those. mutmut edits files in place, so it runs in a scratch copy (``rsync -a`` without ``.git``,
  ``node_modules`` or ``.venv``; nothing is linked or installed there: the worktree's ``.venv`` interpreter is called by
  its absolute path). Each mutant runs the tests that import its module, each the way its suite runs it, or
  ``--runner`` (``RUNNER=``).

* **Memory:** each Stryker worker is a Vitest + happy-dom process of about 600 MB, so the workers are capped at
  ``jobs()``: min(4, cores / 2) by default, ``--jobs`` (``JOBS=``) to choose; Stryker gets it as ``--concurrency``.
  Each worker's heap is capped at 768 MB (``testRunnerNodeArgs`` in ``stryker.config.json``): a mutant that allocates
  without end dies of out-of-memory, counted as killed, instead of growing until its timeout.
  mutmut 2.5.1 runs one mutant at a time in one scratch copy (it has no parallel mode), always within the cap.

It prints one line per surviving or uncovered mutant, ``file:line  <original> → <mutant>  survived (tests ran: N)`` or
``… no test covers it``, then the score per tool, and exits 1 if any mutant in scope survived. It never stages, commits
or writes the worktree's sources.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = "plugins/empirica/ui/"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
TEST_ROOTS = ("plugins/empirica/tests", "scripts/tests", "lib")
STRYKER_REPORT = "reports/mutation.json"  # relative to the UI, as stryker.config.json writes it
_TEST = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$)|\.test\.mjs$")
_NEVER = re.compile(r"(^|/)(vendor|node_modules|\.venv|dist|reports)/")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

@dataclass
class Plan:
    # each file -> its lines in scope: inclusive (start, end) ranges, or None for every line
    python: dict = field(default_factory=dict)  # by repository path
    ui: dict = field(default_factory=dict)  # by path relative to the UI's folder, as Stryker takes it


@dataclass
class Finding:
    file: str
    line: int
    original: str
    mutant: str
    status: str  # "survived" or "no coverage"
    tests: int

    def __str__(self) -> str:
        said = "no test covers it" if self.status == "no coverage" else f"survived (tests ran: {self.tests})"
        return f"{self.file}:{self.line}  {self.original} → {self.mutant}  {said}"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def hunks(diff: str) -> dict[str, list[tuple[int, int]]]:
    """The changed lines of each file of a ``git diff -U0`` (its new side): a deletion alone changes no line that is left."""
    out: dict[str, list[tuple[int, int]]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line[4:] == "/dev/null" else line[4:].removeprefix("b/")
            if current:
                out.setdefault(current, [])
        elif current and (m := _HUNK.match(line)):
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            if count:
                out[current].append((start, start + count - 1))
    return out


def changed(git=_git) -> dict:
    """The changed lines of every changed file (repository paths) against ``HEAD``, and every line (None) of an untracked one."""
    scope: dict = {f: r for f, r in hunks(git("diff", "-U0", "--no-color", "--no-ext-diff", "HEAD")).items() if r}
    for f in git("ls-files", "--others", "--exclude-standard").splitlines():
        if f:
            scope[f] = None
    return scope


def parse_ranges(text: str) -> list[tuple[int, int]]:
    """``10-20,30`` as [(10, 20), (30, 30)]."""
    out = []
    for part in (p.strip() for p in text.split(",")):
        if part:
            a, _, b = part.partition("-")
            out.append((int(a), int(b or a)))
    return out


def named(files: str, lines: str | None = None) -> dict:
    """``FILES=`` and ``LINES=``: each named file, with its own ``:start-end`` or else ``lines``, or every line."""
    out: dict = {}
    for entry in (f.strip() for f in files.split(",")):
        if not entry:
            continue
        path, _, own = entry.partition(":")
        ranges = parse_ranges(own) if own else (parse_ranges(lines) if lines else None)
        out[path] = None if out.get(path, []) is None or ranges is None else [*out.get(path, []), *ranges]
    return out


def plan(scope: dict, exists=lambda p: (ROOT / p).is_file()) -> Plan:
    """Which files each tool mutates, and which lines. A UI path may be given relative to the UI's folder."""
    work = Plan()
    for path, ranges in scope.items():
        if not exists(path) and exists(UI + path):
            path = UI + path
        if _TEST.search(path) or _NEVER.search(path) or not exists(path):
            continue
        if path.endswith(".py"):
            work.python[path] = ranges
        elif path.startswith(UI) and path.endswith(".js") and path[len(UI):].split("/", 1)[0] in ("app", "elements"):
            work.ui[path[len(UI):]] = ranges
    return work


def mutate_arg(ui: dict) -> str:
    """Stryker's ``--mutate``: ``file`` for every line, ``file:start-end`` once per range."""
    return ",".join(f if r is None else ",".join(f"{f}:{a}-{b}" for a, b in r) for f, r in ui.items())


def in_scope(line: int, ranges) -> bool:
    return ranges is None or any(a <= line <= b for a, b in ranges)


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def stryker_findings(report: dict, ui: dict) -> tuple[list[Finding], dict[str, int]]:
    """The survivors and the uncovered mutants in scope, and the counts by status (the report also keeps, for the
    incremental record, every mutant of earlier runs: those out of scope are left out)."""
    found, counts = [], {}
    for name, f in report["files"].items():
        if name not in ui:
            continue
        lines = f["source"].split("\n")
        for m in f["mutants"]:
            start, end = m["location"]["start"], m["location"]["end"]
            if not in_scope(start["line"], ui[name]):
                continue
            counts[m["status"]] = counts.get(m["status"], 0) + 1
            if m["status"] not in ("Survived", "NoCoverage"):
                continue
            # columns are 1-based, the end exclusive
            if start["line"] == end["line"]:
                original = lines[start["line"] - 1][start["column"] - 1:end["column"] - 1]
            else:
                original = "\n".join([lines[start["line"] - 1][start["column"] - 1:], *lines[start["line"]:end["line"] - 1],
                                      lines[end["line"] - 1][:end["column"] - 1]])
            found.append(Finding(UI + name, start["line"], _short(original), _short(m.get("replacement") or m["mutatorName"]),
                                 "no coverage" if m["status"] == "NoCoverage" else "survived", m.get("testsCompleted") or 0))
    return sorted(found, key=lambda x: (x.file, x.line)), counts


def score(counts: dict[str, int]) -> str:
    """Killed (or timed out) over every valid mutant, as Stryker scores it."""
    detected = counts.get("Killed", 0) + counts.get("Timeout", 0)
    valid = detected + counts.get("Survived", 0) + counts.get("NoCoverage", 0)
    parts = ", ".join(f"{n} {k.lower()}" for k, n in sorted(counts.items()))
    return f"{100 * detected / valid:.1f}% ({parts})" if valid else "no mutant in scope"


def patch(python: dict, read=lambda p: (ROOT / p).read_text(encoding="utf-8")) -> str:
    """A patch that adds the scope's lines, for mutmut's ``--use-patch-file`` (it mutates the lines a patch adds). Each
    file opens with a plain ``diff -u`` line: it keeps the files apart, and leaves the paths as they are (a git header's
    ``b/`` would stay on them)."""
    out = []
    for path, ranges in python.items():
        lines = read(path).split("\n")
        spans = ranges if ranges is not None else [(1, len(lines))]
        out += [f"diff -u {path} {path}", f"--- {path}", f"+++ {path}"]
        for a, b in spans:
            b = min(b, len(lines))
            if a <= b:
                out += [f"@@ -0,0 +{a},{b - a + 1} @@", *(f"+{x}" for x in lines[a - 1:b])]
    return "\n".join(out) + "\n"


def shown(diff: str, tests: int) -> Finding | None:
    """A mutmut ``show`` diff as a finding: its file and line, and the part of the line it changed."""
    path = line = at = None
    old, new = [], []
    for row in diff.splitlines():
        if row.startswith("--- "):
            path = row[4:]
        elif m := re.match(r"^@@ -(\d+)", row):
            at = int(m.group(1))  # the hunk's first line, context included
        elif at is None or row.startswith("+++"):
            continue
        elif row.startswith(" ") and not old:
            at += 1
        elif row.startswith("-"):
            line = at if line is None else line
            old.append(row[1:])
        elif row.startswith("+"):
            new.append(row[1:])
    if path is None or line is None:
        return None
    a, b = "\n".join(old), "\n".join(new)
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    # widen the changed part to whole tokens: a word stays a word, an operator an operator
    kind = lambda c: "space" if c.isspace() else "word" if c.isalnum() or c == "_" else "mark"  # noqa: E731
    first = a[i] if i < len(a) - j else b[i]
    while i > 0 and kind(a[i - 1]) != "space" and kind(a[i - 1]) == kind(first):
        i -= 1
    last = a[len(a) - j - 1] if i < len(a) - j else b[len(b) - j - 1]
    while j > 0 and kind(a[len(a) - j]) != "space" and kind(a[len(a) - j]) == kind(last):
        j -= 1
    return Finding(path, line, _short(a[i:len(a) - j]) or "∅", _short(b[i:len(b) - j]) or "∅", "survived", tests)


def tests_for(source: str) -> list[str]:
    """The test files that import ``source``'s module (``import m``, ``from pkg.m import``, ``from pkg import m``)."""
    stem = re.escape(Path(source).stem)
    pattern = re.compile(rf"^\s*(import\s+[\w.]*\b{stem}\b|from\s+[\w.]*\b{stem}\b|from\s+[\w.]+\s+import\s+.*\b{stem}\b)", re.M)
    found = []
    for root in TEST_ROOTS:
        for test in sorted((ROOT / root).rglob("test_*.py")):
            if pattern.search(test.read_text(encoding="utf-8", errors="replace")):
                found.append(str(test.relative_to(ROOT)))
    return found


def runner(tests: list[str], *, python: str) -> str:
    """One shell command that runs ``tests`` as their suites do, failing if any fails."""
    parts = []
    v2 = [Path(t).stem for t in tests if t.startswith("plugins/empirica/tests/v2/")]
    if v2:
        parts.append(f"(cd plugins/empirica/tests/v2 && PYTHONPATH=..:../..:../../../../scripts {python} -m unittest -q "
                     f"{' '.join(v2)})")
    parts += [f"{python} -m unittest -q {shlex.quote(t)}" for t in tests if not t.startswith("plugins/empirica/tests/v2/")
              and t.startswith("scripts/")]
    parts += [f"{python} {shlex.quote(t)}" for t in tests if not t.startswith(("plugins/empirica/tests/v2/", "scripts/"))]
    return " && ".join(parts)


def shell_runner(run: str) -> str:
    """``run`` as the one command mutmut can execute: ``sh -c`` with the whole of it quoted as one word."""
    return f"sh -c {shlex.quote(run)}"


def scratch_copy(repo: str, scratch: str) -> list[str]:
    """The copy mutmut edits: no history, no dependency folder, nothing linked."""
    return ["rsync", "-a", "--delete", "--exclude", ".git", "--exclude", "node_modules", "--exclude", ".venv",
            "--exclude", ".hypothesis", "--exclude", "__pycache__", f"{repo.rstrip('/')}/", f"{scratch.rstrip('/')}/"]


def jobs(value: str | None = None, cores: int | None = None) -> int:
    """How many mutants run at once: ``value`` (``JOBS=``) when given, else min(4, cores / 2), at least 1."""
    if value:
        n = int(value)
        if n < 1:
            raise ValueError(f"JOBS={value}: at least 1")
        return n
    return max(1, min(4, (cores if cores is not None else os.cpu_count() or 2) // 2))


def stryker_command(binary: str, ui: dict, workers: int) -> list[str]:
    """The Stryker run: the committed config, the scope's lines, and the worker cap."""
    return [binary, "run", "stryker.config.json", "--mutate", mutate_arg(ui), "--concurrency", str(workers)]


def mutmut_command(mutmut: list[str], source: str, run: str, patch_file: str) -> list[str]:
    """One mutmut run over ``source``'s patched lines: serial (mutmut 2.5.1 runs one mutant at a time, within any cap).
    mutmut splits its runner with ``shlex`` and executes the words, no shell, so the shell command ``run`` (``runner``'s or
    ``RUNNER=``'s: ``cd``, ``&&``) is handed to it as ``sh -c '<run>'``."""
    return [*mutmut, "run", "--paths-to-mutate", source, "--runner", shell_runner(run), "--tests-dir", "scripts/tests",
            "--use-patch-file", patch_file, "--no-progress", "--simple-output"]


def _stryker(ui: dict, workers: int) -> tuple[list[Finding], str, int]:
    folder = ROOT / UI
    binary = folder / "node_modules" / ".bin" / "stryker"
    if not binary.exists():
        print("Stryker is missing: run make ui-install", file=sys.stderr)
        return [], "not run", 1
    print(f"==> Stryker: {mutate_arg(ui)} ({workers} at once)", flush=True)
    # the sandbox is a copy of the UI alone: its tests find the plugin's fixtures and the contracts through this
    env = {**os.environ, "EMPIRICA_PLUGIN_ROOT": str(ROOT / "plugins/empirica")}
    done = subprocess.run(stryker_command(str(binary), ui, workers), cwd=folder, env=env, check=False)
    if done.returncode:
        return [], "failed", done.returncode
    found, counts = stryker_findings(json.loads((folder / STRYKER_REPORT).read_text(encoding="utf-8")), ui)
    return found, score(counts), 0


def _mutmut(python: dict, command: str | None) -> tuple[list[Finding], str, int]:
    if not VENV_PYTHON.exists():
        print("mutmut is missing: run make dev-install", file=sys.stderr)
        return [], "not run", 1
    scratch = tempfile.mkdtemp(prefix="empirica-mutate-")
    found: list[Finding] = []
    counts = {"killed": 0, "survived": 0}
    status = 0
    try:
        subprocess.run(scratch_copy(str(ROOT), scratch), check=True)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        mutmut = [str(VENV_PYTHON), "-m", "mutmut"]
        for source, ranges in python.items():
            tests = tests_for(source)
            run = command or runner(tests, python=str(VENV_PYTHON))
            if not run:
                print(f"==> mutmut: {source}: no test imports it; give RUNNER=", flush=True)
                status = 1
                continue
            print(f"==> mutmut: {source} {'every line' if ranges is None else ','.join(f'{a}-{b}' for a, b in ranges)}\n"
                  f"    runner: {run}", flush=True)
            patch_file = Path(scratch) / ".mutate.patch"
            patch_file.write_text(patch({source: ranges}), encoding="utf-8")
            done = subprocess.run(mutmut_command(mutmut, source, run, str(patch_file)), cwd=scratch, env=env, check=False, capture_output=True, text=True)
            if done.returncode & 1:
                print(done.stdout[-2000:] + done.stderr[-2000:], file=sys.stderr)
                status = 1
            ids = subprocess.run([*mutmut, "result-ids", "survived"], cwd=scratch, env=env, check=False,
                                 capture_output=True, text=True).stdout.split()
            killed = subprocess.run([*mutmut, "result-ids", "killed"], cwd=scratch, env=env, check=False,
                                    capture_output=True, text=True).stdout.split()
            counts["killed"] += len(killed)
            counts["survived"] += len(ids)
            for i in ids:
                diff = subprocess.run([*mutmut, "show", i], cwd=scratch, env=env, check=False, capture_output=True, text=True).stdout
                if f := shown(diff, len(tests) or 1):
                    found.append(f)
            shutil.rmtree(Path(scratch) / ".mutmut-cache", ignore_errors=True)
            (Path(scratch) / ".mutmut-cache").unlink(missing_ok=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    total = counts["killed"] + counts["survived"]
    said = f"{100 * counts['killed'] / total:.1f}% ({counts['killed']} killed, {counts['survived']} survived)" if total else "no mutant in scope"
    return found, said, status


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--files", default=os.environ.get("FILES") or None)
    parser.add_argument("--lines", default=os.environ.get("LINES") or None)
    parser.add_argument("--runner", default=os.environ.get("RUNNER") or None)
    parser.add_argument("--jobs", default=os.environ.get("JOBS") or None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.lines and not args.files:
        parser.error("LINES= needs FILES=")
    work = plan(named(args.files, args.lines) if args.files else changed())
    if not work.python and not work.ui:
        print("nothing to mutate: no changed source (FILES= names some)")
        return 0
    if args.dry_run:
        for source, ranges in work.python.items():
            print(f"python {source} {'every line' if ranges is None else ranges}: {args.runner or runner(tests_for(source), python=str(VENV_PYTHON)) or '(no test)'}")
        if work.ui:
            print(f"ui --mutate {mutate_arg(work.ui)}")
        return 0
    results = []
    if work.ui:
        results.append(("Stryker", *_stryker(work.ui, jobs(args.jobs))))
    if work.python:
        results.append(("mutmut", *_mutmut(work.python, args.runner)))
    found = [f for _, fs, _, _ in results for f in fs]
    print("")
    for f in found:
        print(f)
    for tool, _, said, _ in results:
        print(f"{tool}: {said}")
    failed = any(status for *_, status in results)
    return 1 if found or failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
