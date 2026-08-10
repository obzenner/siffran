#!/usr/bin/env python3
"""M3 SpikeHarness — deterministic gate from a real check (ADR-13).

The gate is the exit code of an actual command in a subprocess (test, type-check,
lint — the deterministic trust boundary), never model judgment. Proven in the spike
at .claude/spike-m3.

gate == "pass" ⇔ the command exited 0; anything else (including timeout) is "fail".
The SpikeResult it prints is transient scratch (ADR-14) — capture it in .claude/
scratch, never commit.

subprocess runs with shell=False (argv list) — no shell metacharacter expansion. But
this is a TRUSTED-COMMAND RUNNER, not a sandbox: the command is model-controlled and runs
with the user's full environment and privileges (inherits PATH, no allowlist, no network
restriction). Treat the command source as trusted; do not point it at untrusted input.

Exit code (review 2.2): by default main() exits with the checked command's own status
(0 ⇔ gate=pass), so `spike_harness … && next` behaves like a real gate and CI/Bash see the
right colour. Use `--report-only` to always exit 0 and read the `gate` field from the JSON.

FOLD-2 EVIDENCE (ADR-20 P3 / ADR-21 M2): this module is the SOLE WRITER of spike records.
With `--claim <id> --run-dir <dir> --ts <stamp>` it writes an in-toto Statement binding the
claim to this run's real exit code (via evidence.py). That is the whole reason the record
cannot be forged: `gate` is derived from `returncode`, never from a model's assertion. Do not
add another caller of `evidence.write_spike` — a second writer would reopen the hole.

ONE SAMPLE IS NOT A VERDICT. `--repeat N` runs the check N times and passes only if every run
exited 0. A check with any nondeterminism — an unseeded property test, a timing or ordering
dependency — can pass once by luck, and that single green record then approves the claim for the
rest of the run. Use it for any check whose result is not obviously a pure function of the tree.

RE-GATING AFTER A FORMATTER (ADR-29). `--regate --run-dir DIR --ts STAMP` re-runs every spike whose
`files_hash` no longer matches the tree, using the command each record already stores. It does not
bless a stale record: each spike is re-executed and its verdict comes from a fresh exit code, so a
re-gate can and should discover that the formatting pass broke something. Exits nonzero if any
re-gated spike now fails.

Usage:  python3 spike_harness.py [--timeout SEC] [--repeat N] [--report-only]
                                 [--claim ID --run-dir DIR --ts STAMP [--file PATH ...]]
                                 <cmd> [args...]
        python3 spike_harness.py --regate --run-dir DIR --ts STAMP [--timeout SEC]
"""
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import TypedDict

DEFAULT_TIMEOUT = 300  # seconds; a spike check that runs longer is a failure signal
MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB cap per stream — a chatty command can't OOM the hook
_LAUNCH_FAIL_RC = 127  # command could not be launched (missing/permission/OS error)


class SpikeResult(TypedDict):
    """The transient gate payload (ADR-14). `gate` is 'pass' iff returncode == 0.

    Under `--repeat N` the top-level fields describe the RUN AS A WHOLE — `gate` is 'pass' only
    if every repetition passed, and `returncode` is the first failing code — with the per-run
    detail in `runs`. A single run leaves `runs` absent, so the payload is unchanged for callers
    that never repeat.
    """
    cmd: list[str]
    returncode: int | None
    gate: str
    timed_out: bool
    stdout_tail: list[str]
    stderr_tail: list[str]


MAX_LINE = 2000  # cap each retained line so one newline-free blob can't bloat the payload


def _tail(text: str, n: int = 5) -> list[str]:
    if not text:
        return []
    lines = text.strip().splitlines()[-n:]
    return [ln if len(ln) <= MAX_LINE else ln[:MAX_LINE] + "…" for ln in lines]


class _BoundedReader(threading.Thread):
    """Drain a pipe in chunks, retaining only the last MAX_OUTPUT_BYTES.

    This is the actual memory bound (review 1.5): the earlier version let
    `communicate()` buffer the entire stream before truncating, so a multi-GB emitter
    could OOM the hook. Here we keep a ring buffer of the tail and never hold more than
    the cap, regardless of how much the command writes.
    """

    def __init__(self, pipe):
        super().__init__(daemon=True)
        self._pipe = pipe
        self._buf = b""
        self.overflowed = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._pipe.read(65536)
                if not chunk:
                    break
                self._buf += chunk
                if len(self._buf) > MAX_OUTPUT_BYTES:
                    self._buf = self._buf[-MAX_OUTPUT_BYTES:]
                    self.overflowed = True
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._pipe.close()
            except OSError:
                pass

    def text(self) -> str:
        return self._buf.decode("utf-8", errors="replace")


def run_gate(cmd: list[str], timeout: float = DEFAULT_TIMEOUT) -> SpikeResult:
    """Run a deterministic check; the gate is its real exit code, nothing softer.

    Robust by construction (review 1.5): a timeout, non-UTF-8 output, or a launch failure
    (missing exe / permission / OSError) all resolve to gate=fail with the reason captured
    — no unhandled exception path. On timeout the whole PROCESS GROUP is killed so forked
    descendants don't survive. Output is drained through bounded ring buffers, so a chatty
    command can never exhaust memory even before truncation.
    """
    # New session/process group so a timeout can kill the whole tree (POSIX).
    preexec = os.setsid if hasattr(os, "setsid") else None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=preexec,
        )
    except (OSError, ValueError) as exc:
        return SpikeResult(cmd=cmd, returncode=_LAUNCH_FAIL_RC, gate="fail", timed_out=False,
                           stdout_tail=[], stderr_tail=[f"launch failed: {exc}"])

    out_reader, err_reader = _BoundedReader(proc.stdout), _BoundedReader(proc.stderr)
    out_reader.start()
    err_reader.start()
    try:
        proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.wait()
        timed_out = True
    out_reader.join(timeout=5)
    err_reader.join(timeout=5)

    err_tail = _tail(err_reader.text())
    if timed_out:
        return SpikeResult(cmd=cmd, returncode=None, gate="fail", timed_out=True,
                           stdout_tail=_tail(out_reader.text()),
                           stderr_tail=[f"timed out after {timeout}s"])
    return SpikeResult(
        cmd=cmd,
        returncode=proc.returncode,
        gate="pass" if proc.returncode == 0 else "fail",
        timed_out=False,
        stdout_tail=_tail(out_reader.text()),
        stderr_tail=err_tail,
    )


MAX_REPEAT = 100  # a repeat count above this is a mistake, not a stronger check


def run_gate_repeated(cmd: list[str], timeout: float = DEFAULT_TIMEOUT,
                      repeat: int = 1) -> SpikeResult:
    """Run the check `repeat` times; the gate passes only if EVERY run passed.

    WHY THIS EXISTS: a single sample cannot distinguish "this check passes" from "this check
    passed once". A flaky or randomised check — a property test with an unseeded generator, a
    check with a timing or ordering dependency — yields a green Fold-2 record from one lucky run,
    and that record then approves the claim forever (ADR-13's exit code is still the approver, but
    one sample of it). Repeating is the cheapest real defence: N independent exit codes, all of
    which must be 0.

    Short-circuits on the FIRST failure. A check that already failed cannot be rescued by later
    runs, and continuing would spend the user's time to learn nothing — so `runs` records the runs
    actually performed, not a padded list.

    Conjunctive on purpose: a majority rule would let a known-flaky check approve a claim, which
    is the exact property this flag exists to detect.
    """
    runs: list[dict] = []
    worst: SpikeResult | None = None
    for _ in range(repeat):
        result = run_gate(cmd, timeout)
        runs.append({"returncode": result["returncode"], "gate": result["gate"],
                     "timed_out": result["timed_out"],
                     # Per-run stdout digest, not the text: enough to see that repetitions
                     # differed (the signature of a nondeterministic check) without carrying N
                     # copies of the output into a record that must stay small.
                     "stdout_sha256": hashlib.sha256(
                         "\n".join(result["stdout_tail"]).encode("utf-8")).hexdigest()})
        worst = result
        if result["gate"] != "pass":
            break  # first failure decides the gate; later runs cannot un-fail it
    assert worst is not None  # parse_args guarantees repeat >= 1
    out = SpikeResult(**worst)
    if repeat > 1:
        out["runs"] = runs  # type: ignore[typeddict-unknown-key]
        out["repeat"] = repeat  # type: ignore[typeddict-unknown-key]
    return out


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the child's whole process group on timeout so descendants don't leak."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - Windows
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_args(argv: list[str]) -> tuple[list[str], float, bool, dict, int]:
    """Split leading flags from the command to run.

    timeout is clamped to a finite positive value (review 1.5: a bogus timeout must not
    disable the guard). The evidence flags (`--claim`, `--run-dir`, `--ts`, repeated
    `--file`) are optional: without them the harness behaves exactly as before, as a plain
    deterministic gate that records nothing.

    `--repeat N` is clamped to [1, MAX_REPEAT] and a malformed value falls back to 1 — the same
    fail-toward-the-default discipline as `--timeout`. Clamping rather than rejecting keeps the
    flag from being a way to make the harness refuse to run at all.
    """
    timeout = DEFAULT_TIMEOUT
    report_only = False
    repeat = 1
    ev: dict = {"claim": None, "run_dir": None, "ts": None, "files": []}
    while argv:
        if argv[0] == "--report-only":
            report_only = True
            argv = argv[1:]
        elif argv[0] == "--timeout" and len(argv) >= 2:
            try:
                t = float(argv[1])
                if t > 0 and t != float("inf"):
                    timeout = t
            except ValueError:
                pass
            argv = argv[2:]
        elif argv[0] == "--repeat" and len(argv) >= 2:
            try:
                repeat = max(1, min(MAX_REPEAT, int(argv[1])))
            except ValueError:
                pass
            argv = argv[2:]
        elif argv[0] == "--file" and len(argv) >= 2:
            ev["files"].append(argv[1])
            argv = argv[2:]
        elif argv[0] in ("--claim", "--run-dir", "--ts") and len(argv) >= 2:
            ev[argv[0][2:].replace("-", "_")] = argv[1]
            argv = argv[2:]
        else:
            break
    return argv, timeout, report_only, ev, repeat


def _result_hash(result: SpikeResult) -> str:
    """The record's `result` digest.

    For a single run: sha256 of the retained stdout, as before. Under `--repeat` that would be
    the digest of ONE arbitrary repetition's output — a value that varies between invocations of
    a nondeterministic check and therefore describes nothing. So a repeated run digests the
    ordered per-run stdout digests instead, which is stable for a deterministic check and
    visibly differs for a flaky one: the field keeps meaning what it says.
    """
    runs = result.get("runs")
    if runs:
        h = hashlib.sha256()
        for run in runs:
            h.update(run["stdout_sha256"].encode("ascii"))
            h.update(b"\0")
        return h.hexdigest()
    return hashlib.sha256("\n".join(result["stdout_tail"]).encode("utf-8")).hexdigest()


def _record_evidence(result: SpikeResult, ev: dict) -> dict | None:
    """Write the Fold-2 record for this run, from the REAL exit code.

    Returns a small status dict for the JSON output, or None when the evidence flags were not
    supplied. A write failure is reported, never fatal: the gate's exit code is still the
    verdict, and a run that could not persist evidence must fail the Stop gate later (no
    record ⇒ no approval) rather than crash the check the user asked for.

    `claim_text` is read from the claim graph so the in-toto subject digest binds to the claim
    as currently worded — the harness must not accept claim text from the command line, or a
    caller could bind a spike to a claim it never tested.
    """
    if not (ev.get("claim") and ev.get("run_dir") and ev.get("ts")):
        return None
    run_dir = Path(ev["run_dir"])
    try:
        graph_mod = _load("claimgraph")
        evidence = _load("evidence")
        g = graph_mod.load(graph_mod.default_graph_path(run_dir))
        if g is None or g == graph_mod.CORRUPT:
            return {"recorded": False,
                    "reason": f"no readable claim graph in {run_dir} — cannot bind evidence"}
        node = g["nodes"].get(ev["claim"])
        if node is None:
            return {"recorded": False, "reason": f"claim {ev['claim']!r} is not in the graph"}
        path = evidence.write_spike(
            run_dir, f"spike-{ev['claim']}", ev["claim"], node["text"],
            cmd=result["cmd"], gate=result["gate"], result_hash=_result_hash(result),
            files=[Path(f) for f in ev["files"]], ts=ev["ts"],
            # The real number of runs behind this verdict, so the record says how many samples
            # back it (ADR-27). `runs` is absent for a single run, hence the fallback.
            samples=len(result.get("runs") or [None]),
            # ADR-29: the real exit code of every run, in order. A single run has no `runs` list,
            # so its own returncode is the one-element list — the field then means the same thing
            # for repeated and unrepeated spikes.
            exit_codes=([r["returncode"] for r in result["runs"]] if result.get("runs")
                        else [result["returncode"]]),
        )
        return {"recorded": True, "gate": result["gate"], "path": str(path)}
    except (OSError, ValueError, AttributeError, KeyError) as exc:
        return {"recorded": False, "reason": f"{type(exc).__name__}: {exc}"}


def regate(run_dir: Path, ts: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Re-run every spike whose `files_hash` no longer matches the tree (ADR-29).

    WHY THIS EXISTS: a formatter is the common case, and it is a real invalidation. `cargo fmt`
    rewrites bytes, so `files_digest` legitimately changes and eight green spikes legitimately go
    stale — an agent reported exactly that, twice, and correctly noted that "each catch was
    legitimate". The defect was never the detection; it was that re-establishing eight verdicts was
    manual, so the honest response to a formatting pass was tedious enough to invite skipping it.

    This is deliberately NOT a way to bless a stale record. Each spike is re-executed and its new
    verdict comes from a fresh subprocess exit code, exactly as the first one did — the only thing
    saved is retyping the command. A spike that now FAILS is recorded as failing, which is the whole
    point: re-gating must be able to discover that the formatter broke something.

    Only STALE leaves are re-run. A spike still matching the tree is left alone, so this is cheap to
    run habitually and never rewrites a valid record's timestamp.
    """
    graph_mod, evidence = _load("claimgraph"), _load("evidence")
    graph = graph_mod.load(graph_mod.default_graph_path(run_dir))
    if graph is None or graph == graph_mod.CORRUPT:
        return {"regated": False, "reason": f"no readable claim graph in {run_dir}"}
    out = []
    for leaf in evidence.read_leaves(run_dir):
        if leaf["fold"] != evidence.FOLD2 or not leaf["files"]:
            continue
        if leaf["files_hash"] == evidence.files_digest([Path(f) for f in leaf["files"]]):
            continue  # still intact — nothing to do
        node = graph["nodes"].get(leaf["claim_id"])
        if node is None or not leaf["command"]:
            out.append({"claim": leaf["claim_id"], "regated": False,
                        "reason": "claim is gone from the graph"
                                  if node is None else "no command recorded to re-run"})
            continue
        result = run_gate_repeated(leaf["command"], timeout, max(1, leaf["samples"]))
        evidence.write_spike(
            run_dir, f"spike-{leaf['claim_id']}", leaf["claim_id"], node["text"],
            cmd=result["cmd"], gate=result["gate"], result_hash=_result_hash(result),
            files=[Path(f) for f in leaf["files"]], ts=ts, samples=max(1, leaf["samples"]),
            exit_codes=([r["returncode"] for r in result["runs"]] if result.get("runs")
                        else [result["returncode"]]),
        )
        out.append({"claim": leaf["claim_id"], "regated": True, "gate": result["gate"],
                    "samples": max(1, leaf["samples"])})
    failed = [r["claim"] for r in out if r.get("gate") == "fail"]
    return {"regated": True, "stale_found": len(out), "results": out, "failed": failed,
            "note": ("every stale spike was RE-EXECUTED; verdicts come from fresh exit codes, "
                     "not from blessing the old record")}


def main() -> int:
    # `--regate` is its own mode: it takes no command, because it re-runs the commands already
    # recorded in the run's own evidence.
    argv = sys.argv[1:]
    if "--regate" in argv:
        opts = {}
        for i, tok in enumerate(argv):
            if tok in ("--run-dir", "--ts", "--timeout") and i + 1 < len(argv):
                opts[tok[2:]] = argv[i + 1]
        if not opts.get("run-dir") or not opts.get("ts"):
            print(json.dumps({"error": "usage: spike_harness.py --regate --run-dir DIR --ts STAMP "
                                       "[--timeout SEC]"}))
            return 2
        try:
            timeout = float(opts.get("timeout") or DEFAULT_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        report = regate(Path(opts["run-dir"]), opts["ts"], timeout)
        print(json.dumps(report, indent=2))
        # Nonzero when a re-gate turned something red, so `--regate && next` behaves like a gate.
        return 1 if report.get("failed") else 0

    cmd, timeout, report_only, ev, repeat = parse_args(argv)
    if not cmd:
        print(json.dumps({"error": "usage: spike_harness.py [--timeout SEC] [--repeat N] "
                                    "[--report-only] "
                                    "[--claim ID --run-dir DIR --ts STAMP [--file PATH ...]] "
                                    "<cmd> [args...]"}))
        return 2
    result = run_gate_repeated(cmd, timeout, repeat)
    out = dict(result)
    recorded = _record_evidence(result, ev)
    if recorded is not None:
        out["evidence"] = recorded
    print(json.dumps(out, indent=2))
    if report_only:
        return 0  # always green; caller reads the `gate` field
    # Default: propagate the checked command's real status so the gate composes with
    # `&&`, CI, and Bash (review 2.2). Timeout/launch-failure normalise to a nonzero code.
    rc = result["returncode"]
    return rc if isinstance(rc, int) else 1


if __name__ == "__main__":
    sys.exit(main())
