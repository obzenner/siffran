#!/usr/bin/env python3
"""``make dup-check``: no new clone in production code between BASE and HEAD.

    python3 scripts/dup_check.py [--base REF] [--tokens N]

* **Detector:** jscpd, pinned in ``tools/dup-check`` (``make dup-install``), over the hand-written Python, TypeScript
  (``.ts``) and JavaScript (``.js``, ``.mjs``, ``.cjs``) of ``plugins/`` and ``scripts/``, at BASE and at HEAD
  (``git archive``: committed content only, so commit first). Left out: vendored copies, ``node_modules``, ``dist``,
  fixtures, golden files, reports, and the files a generator owns (``generated_outputs``: each declared where the
  generator's own freshness check reads it).
* **Clone:** the detector reports a fragment as pairs of occurrences with one occurrence as the representative; the
  occurrences linked by those reports are one cluster and every two of them are a pair, so a pair the detector did not
  print (two copies beside a representative) is judged too. A pair is its two files and the whitespace-normalized text
  of each occurrence (never the representative's), not its lines or how many times it occurs.
* **New:** a pair at HEAD with an occurrence on a line changed in BASE..HEAD (``git diff -U0 -M``, the hunks ``make
  mutate`` reads) whose pair, the files under their BASE names, BASE did not have.
* **Verdict:** a new pair with both occurrences in production code fails (exit 1); one with an occurrence in test code is
  a warning. Each is printed as ``file:start-end == file:start-end (N lines)``.
* **BASE** must be an ancestor of HEAD (default: the merge base with ``main``); **TOKENS** is the clone's least size (60).
* **Not seen:** a clone made only by deleting lines, and code moved without an edit from a test or an excluded path into
  production (git sees a rename that changes no line). A move git does not see as a rename is a new file, every line
  of it changed, and is checked.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import itertools
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutate  # noqa: E402  (the changed-line and test-path rules are its)

ROOT = Path(__file__).resolve().parents[1]
JSCPD = ROOT / "tools" / "dup-check" / "node_modules" / ".bin" / "jscpd"
SCAN = ("plugins", "scripts")
SOURCE = re.compile(r"\.(py|ts|js|mjs|cjs)$")
# What no author writes: vendored and installed code, build output, fixtures and golden files, local reports.
NOT_AUTHORED = re.compile(r"(^|/)(vendor|node_modules|dist|fixtures|[^/]*golden[^/]*|reports|\.venv|\.stryker-tmp)/")
DEFAULT_TOKENS = 60


class Occurrence(NamedTuple):
    path: str
    start: int
    end: int
    digest: str  # of the occurrence's whitespace-normalized text


@dataclass(frozen=True)
class Clone:
    """Two occurrences of one fragment."""
    first: Occurrence
    second: Occurrence

    @property
    def lines(self) -> int:
        return min(o.end - o.start + 1 for o in (self.first, self.second))

    def pair(self, name=lambda path: path) -> tuple:
        """What identifies the clone across two commits: each file (as ``name`` calls it) with the text it has there, not the lines."""
        return tuple(sorted((name(o.path), o.digest) for o in (self.first, self.second)))

    def __str__(self) -> str:
        (a, a1, a2, _), (b, b1, b2, _) = self.first, self.second
        return f"{a}:{a1}-{a2} == {b}:{b1}-{b2} ({self.lines} lines)"


_git = mutate.git_output


def _records(root: Path, *args: str) -> list[str]:
    """The NUL-terminated records of a ``git ... -z`` command, paths exactly as git has them."""
    return _git(root, *args).split("\0")[:-1]


def default_base(root: Path = ROOT) -> str:
    """The merge base of HEAD with ``main``."""
    for main in ("main", "origin/main"):
        done = subprocess.run(["git", "merge-base", main, "HEAD"], cwd=root, capture_output=True, text=True, check=False)
        if done.returncode == 0:
            return done.stdout.strip()
    raise SystemExit("dup-check: no main branch to take the merge base with; pass BASE=<ref>")


def require_ancestor(root: Path, base: str) -> None:
    """BASE must be on HEAD's history: otherwise the range would count work only the other branch has."""
    if subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=root, check=False).returncode != 0:
        raise SystemExit(f"dup-check: BASE {base[:12]} is not an ancestor of HEAD; use the merge base: BASE=$(git merge-base {base[:12]} HEAD)")


def changed_lines(root: Path, base: str) -> dict[str, list[tuple[int, int]]]:
    """The lines BASE..HEAD changed in each file (``make mutate``'s hunks), a pure rename changing none."""
    diff = _git(root, "diff", "-U0", "--no-color", "--no-ext-diff", "-M", "--src-prefix=a/", "--dst-prefix=b/", base, "HEAD")
    return mutate.hunks(diff)


def renames(root: Path, base: str) -> dict[str, str]:
    """Each renamed file's BASE name by its HEAD name."""
    out, tokens = {}, _records(root, "diff", "-M", "-z", "--name-status", base, "HEAD")
    while tokens:
        status, *tokens = tokens
        names, tokens = tokens[:2 if status[0] in "RC" else 1], tokens[2 if status[0] in "RC" else 1:]
        if status[0] == "R":
            out[names[1]] = names[0]
    return out


def generated_outputs() -> frozenset[str]:
    """The sources a generator owns, each declared by its generator and read by that generator's freshness check:
    every module ``gen_contract_bounds.TARGETS`` declares, which ``make contract-check`` compares with a regeneration."""
    import gen_contract_bounds
    return frozenset(path.relative_to(ROOT).as_posix() for path in gen_contract_bounds.TARGETS)


def is_test(path: str) -> bool:
    return bool(mutate.TEST_PATH.search(path))


def authored(path: str, generated: frozenset[str]) -> bool:
    return bool(SOURCE.search(path)) and not NOT_AUTHORED.search(path) and path not in generated


def snapshot(root: Path, ref: str, dest: Path, generated: frozenset[str]) -> None:
    """The authored sources of ``ref`` under ``dest``: what ``git archive`` gives of the scanned files that are neither
    vendored, fixtures nor a generator's output."""
    names = [n for n in _records(root, "ls-tree", "-r", "-z", "--name-only", ref, "--", *SCAN) if authored(n, generated)]
    if not names:
        return
    tar = subprocess.run(["git", "archive", "--format=tar", ref, "--", *names], cwd=root, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        archive.extractall(dest, filter="data")


def clones(report: dict, scanned: Path, read=lambda path: path.read_text(encoding="utf-8", errors="surrogateescape")) -> list[Clone]:
    """A detector report as clones. The report's pairs link occurrences; the occurrences one link chain reaches are a
    cluster of copies of one fragment, and every two of them are a clone, whichever the detector printed."""
    top = os.path.realpath(scanned)

    def place(side: dict) -> Occurrence:
        path = os.path.relpath(os.path.realpath(side["name"]), top)
        text = " ".join(" ".join(read(Path(top) / path).split("\n")[side["start"] - 1:side["end"]]).split())
        return Occurrence(path, side["start"], side["end"], hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest())

    parent: dict[Occurrence, Occurrence] = {}

    def root(o: Occurrence) -> Occurrence:
        while parent.setdefault(o, o) != o:
            parent[o] = parent[parent[o]]
            o = parent[o]
        return o

    for d in report["duplicates"]:
        parent[root(place(d["firstFile"]))] = root(place(d["secondFile"]))
    groups: dict[Occurrence, list[Occurrence]] = {}
    for o in sorted(parent):
        groups.setdefault(root(o), []).append(o)
    return [Clone(a, b) for members in groups.values() for a, b in itertools.combinations(members, 2)]


def jscpd_report(scanned: Path, tokens: int, jscpd: Path) -> dict:
    """jscpd's JSON report of the clones of at least ``tokens`` tokens in ``scanned``."""
    with tempfile.TemporaryDirectory() as out:
        subprocess.run([str(jscpd), str(scanned), "-k", str(tokens), "-f", "python,typescript,javascript", "-r", "json", "-o", out,
                        "--absolute", "--no-gitignore", "--no-tips", "--no-colors", "--silent"],
                       capture_output=True, text=True, check=True)
        return json.loads((Path(out) / "jscpd-report.json").read_text(encoding="utf-8"))


def overlaps(fragment: Occurrence, changed: dict[str, list[tuple[int, int]]]) -> bool:
    return any(a <= fragment.end and fragment.start <= b for a, b in changed.get(fragment.path, ()))


def new_clones(head: list[Clone], base: list[Clone], changed: dict, renamed: dict[str, str]) -> list[Clone]:
    """The clones at HEAD with an occurrence on a changed line whose pair, the files under their BASE names, BASE did not have."""
    old = {c.pair() for c in base}
    return sorted((c for c in head if c.pair(lambda path: renamed.get(path, path)) not in old
                   and (overlaps(c.first, changed) or overlaps(c.second, changed))), key=lambda c: (c.first, c.second))


def verdict(found: list[Clone]) -> tuple[list[Clone], list[Clone]]:
    """The failures (both occurrences production code) and the warnings (an occurrence in test code), pair by pair."""
    failures = [c for c in found if not (is_test(c.first.path) or is_test(c.second.path))]
    return failures, [c for c in found if c not in failures]


def check(root: Path, base: str, tokens: int, generated: frozenset[str], report_of: Callable[[Path, int], dict]):
    """The failures and the warnings of BASE..HEAD in ``root``, the detector's report of a scanned folder being ``report_of``."""
    require_ancestor(root, base)
    with tempfile.TemporaryDirectory() as tmp:
        found = {}
        for name, ref in (("base", base), ("head", "HEAD")):
            scanned = Path(tmp) / name
            scanned.mkdir()
            snapshot(root, ref, scanned, generated)
            found[name] = clones(report_of(scanned, tokens), scanned)
        return verdict(new_clones(found["head"], found["base"], changed_lines(root, base), renames(root, base)))


def main(argv: list[str], *, root: Path = ROOT, report_of: Callable[[Path, int], dict] | None = None,
         generated: frozenset[str] | None = None) -> int:
    """The command; ``report_of`` is the detector (jscpd, pinned, unless a caller gives its own), ``generated`` the declared outputs."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("BASE") or None)
    parser.add_argument("--tokens", type=int, default=int(os.environ.get("TOKENS") or DEFAULT_TOKENS))
    args = parser.parse_args(argv)
    if args.tokens < 1:
        parser.error("TOKENS must be a positive number")
    if report_of is None:
        if not JSCPD.is_file():
            print("dup-check: the pinned clone detector is missing: run make dup-install", file=sys.stderr)
            return 2
        report_of = lambda scanned, tokens: jscpd_report(scanned, tokens, JSCPD)  # noqa: E731
    base = _git(root, "rev-parse", args.base).strip() if args.base else default_base(root)
    failures, warnings = check(root, base, args.tokens, generated_outputs() if generated is None else generated, report_of)
    print(f"dup-check: {base[:8]}..{_git(root, 'rev-parse', 'HEAD')[:8]}, clones of at least {args.tokens} tokens")
    for title, group in (("FAIL new production clones", failures), ("WARN new clones involving test code", warnings)):
        if group:
            print(f"{title}:")
            for c in group:
                print(f"  {c}")
    if not failures and not warnings:
        print("no new clone")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
