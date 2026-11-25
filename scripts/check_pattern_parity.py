#!/usr/bin/env python3
"""Cross-engine pattern parity for the registered v2 contract definitions (ADR-0075; contract-check).

The contract's patterns are specified for Python ``re`` and JavaScript ``RegExp`` **with the ``u``
flag**, code-point semantics: the two hosts read a pattern identically or the contract has said
something no host can keep. This check runs one corpus through both engines and fails on any verdict
mismatch that is not a declared open disagreement — a registry pattern whose anchor ended in ``$``
Python reads as *before a trailing newline only* and JavaScript as *before any trailing line
terminator* (LF, CR, U+2028, U+2029). Such pairs are pinned live here exactly as the harness pins
them: the moment an entry's mismatch heals, this check fails and forces the registry entry's removal
(the run/5 digest anchors were the registry's first pair; 07c5f66b healed them and the entries are
gone — the head below carries none).

The corpus is derived, never hand-listed per pattern: the pattern's own grammar witnesses, witness-
seeded suffix and substitution probes (the newline family included), and the standing cross-engine
families — blank, mixed, astral and lone-surrogate cases — that reach the classes a host language's
``\\s``/``\\S``/``\\w`` would otherwise decide differently. Dev-side: it needs Node (``node
scripts/node_pattern_engine.mjs``); Python side runs under the dev venv.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from contract_strategies import (KNOWN_OPEN, X_GRAMMAR, Contract, module_documents,  # noqa: E402
                                 OUTSIDE, pattern_witnesses)

NODE_ENGINE = ROOT / "scripts" / "node_pattern_engine.mjs"
#: The standing cross-engine families: every blank of the contract's own blank set, mixed ordinary
#: text, astral-plane text, and lone-surrogate cases the transport layer must refuse before any host
#: reads a pattern.
FAMILIES = ["", "a", "A", "0", " ", "\t", "\n", "\r", "\x0b", "\x0c", "\u00a0", "\u1680", "\u2000",
            "\u200a", "\u2028", "\u2029", "\u202f", "\u205f", "\u3000", "\ufeff", "a b", "a\tb",
            "a\nb", "a\rb", "ab\u00a0cd", "x\ufeffy", "\u2028start", "\u00e9", "\u06f1",
            "\U0001F600", "\U0001F600abc", "abc\U0001F600def", "\U0001D11E", "\ud800", "\udfff",
            "a\ud800", "\ud800a"]
#: The witness-seeded suffix probes: the line-terminator family plus an astral and a blank character.
SUFFIX_PROBES = ["\n", "\r", "\u2028", "\u2029", "\u00a0", "\U0001F600", "\ud800"]
#: The witness-seeded end-substitution probes: the same complement families the prefix
#: substitutions carry, plus the digit whose `\\d` class the engines read differently — the
#: Arabic-Indic digit U+0661 (E6c review, blocking 2): Python's `\\d` is Unicode-wide and
#: refuses it in `[^\\d]`, JavaScript's is ASCII-only and admits it, so any registered pattern
#: resting a class escape on its last character shows the split here (`\\w`, `\\s` and their
#: negations included; the digits stand for the whole Nd family).
END_SUBSTITUTION_PROBES = ["\n", "\r", "\x00", "\x7f", "\u0661", "\ud800", "\t", " ", "\ufeff"]
#: The only suffixes a ``$`` anchor may read differently — the declared open-disagreement family.
LINE_TERMINATORS = ("\n", "\r", "\u2028", "\u2029")


def corpus_for(pattern: str) -> list[str]:
    """The deterministic corpus of one pattern: its grammar witnesses, witness-seeded suffixes and
    substitutions, and the standing cross-engine families."""
    cases: list[str] = []
    witnesses = pattern_witnesses(pattern)
    for witness in witnesses[:6]:
        if witness not in cases:
            cases.append(witness)
    for witness in witnesses[:2]:
        for probe in SUFFIX_PROBES:
            for seeded in (witness + probe, probe + witness):
                if seeded not in cases:
                    cases.append(seeded)
        for probe in OUTSIDE[:8]:
            if witness:
                substituted = probe + witness[1:] if len(witness) > 1 else probe
                if substituted not in cases:
                    cases.append(substituted)
        for probe in END_SUBSTITUTION_PROBES:
            # the witness's last character replaced: reaches a class escape resting on the
            # anchored end, where the whole-string corpus cannot (the boundary family the
            # line-terminator anchor quirk made necessary has its end-side sibling here).
            if len(witness) > 1 and witness[:-1] + probe not in cases:
                cases.append(witness[:-1] + probe)
    for probe in FAMILIES:
        if probe not in cases:
            cases.append(probe)
    return cases


def patterns_of(contract: Contract) -> tuple[list[str], dict[str, list[str]]]:
    """Every pattern and grammar piece the registered closures use, and the declared
    open-disagreement patterns by the schema node that carries them."""
    seen: list[str] = []
    sources: dict[str, str] = {}
    for key in sorted(contract.registered):
        for _, node, node_id in contract._targets(key):
            for pattern in contract._patterns(node):
                if pattern not in seen:
                    seen.append(pattern)
                    sources[pattern] = node_id
            for piece in (node.get(X_GRAMMAR) or {}).values():
                if isinstance(piece, str) and piece not in seen:
                    seen.append(piece)
                    sources[piece] = node_id
    open_patterns: dict[str, list[str]] = {}
    for node_id in KNOWN_OPEN:
        for key in sorted(contract.registered):
            for _, node, walked_id in contract._targets(key):
                if walked_id == node_id:
                    for pattern in contract._patterns(node):
                        open_patterns.setdefault(pattern, []).append(node_id)
    return seen, open_patterns


def python_verdicts(records: list[dict]) -> dict:
    answers = {}
    for record in records:
        expression = re.compile(record["pattern"])
        for probe in record["cases"]:
            answers[(record["pattern"], probe)] = expression.search(probe) is not None
    return answers


def node_verdicts(records: list[dict]) -> dict | None:
    """``None`` when the machine has no Node: the check reports a skip rather than a fake verdict —
    CI runners carry no Node (doc/testing.md), a developer machine always does."""
    if not shutil.which("node"):
        return None
    done = subprocess.run(["node", str(NODE_ENGINE)], input=json.dumps(records),
                          capture_output=True, text=True, check=False, timeout=120, cwd=str(ROOT))
    if done.returncode != 0:
        raise SystemExit(f"contract-pattern-parity: the Node engine failed: {done.stderr.strip()}")
    return {(answer["pattern"], answer["case"]): bool(answer["verdict"])
            for answer in json.loads(done.stdout)}


def check() -> list[str]:
    """The parity findings: every cross-engine mismatch outside the declared open pairs, and the live
    pins of the open pairs themselves (each declared pattern keeps exhibiting its live divergence;
    a healed one fails the pin instead of being excused)."""
    contract = Contract(documents=module_documents())
    if contract.findings:
        return contract.findings
    patterns, open_patterns = patterns_of(contract)
    records = [{"pattern": pattern, "cases": corpus_for(pattern)} for pattern in patterns]
    from_python = python_verdicts(records)
    from_node = node_verdicts(records)
    if from_node is None:
        return []  # the caller decides what a machine without Node may skip
    findings: list[str] = []
    lives: dict[str, set[str]] = {pattern: set() for pattern in open_patterns}
    for (pattern, probe), python_accepts in sorted(from_python.items()):
        node_accepts = from_node.get((pattern, probe))
        if node_accepts is None:
            findings.append(f"{pattern}: the Node engine answered nothing for the case {probe!r}")
            continue
        if python_accepts == node_accepts:
            continue
        trailing = probe.endswith(LINE_TERMINATORS) or any(probe.endswith(stop) for stop in LINE_TERMINATORS)
        if pattern in open_patterns and trailing:
            lives[pattern].add(probe)  # declared open: the anchor quirk, pinned red while declared
            continue
        findings.append(f"{pattern}: Python {accepts_word(python_accepts)} and Node/u "
                        f"{accepts_word(node_accepts)} the case {probe!r}")
    for pattern, node_ids in sorted(open_patterns.items()):
        if not lives[pattern]:
            findings.append(f"{pattern} ({', '.join(sorted(node_ids))}): a declared open disagreement "
                            "that no corpus case exhibits — the anchor has healed; remove the registrar "
                            "entry (and the harness's, in contract_strategies.KNOWN_OPEN)")
    return findings


def accepts_word(verdict: bool) -> str:
    return "accepts" if verdict else "refuses"


def main() -> int:
    if not shutil.which("node"):
        print("contract-pattern-parity skipped: no Node on PATH (the developer gate and make check run "
              "it; a CI runner without Node cannot, doc/testing.md)")
        return 0
    return main_from_findings(check())


def main_from_findings(findings: list[str]) -> int:
    for line in findings:
        print(line)
    if findings:
        print(f"{len(findings)} pattern-parity finding(s)", file=sys.stderr)
        return 1
    print("contract patterns agree across Python re and JavaScript RegExp/u")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
