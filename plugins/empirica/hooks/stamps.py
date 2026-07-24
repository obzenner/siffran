#!/usr/bin/env python3
"""Ordering stamps — comparing WHEN two recorded events happened (ADR-20 P1).

P1 needs one question answered: did the run announce its route BEFORE it began gathering
evidence? Two different writers record the two events, and this module is the single place that
decides which came first. It exists because comparing those stamps with `<=` is wrong.

WHY A DEDICATED MODULE, AND WHY NOT `<=`
----------------------------------------
The stamps are strings, and not always the same KIND of string. Hooks must not call
datetime.now() (ADR-19: a resumable run's hooks have to be deterministic), so a stamp is
whatever the caller could witness:

  * an ISO-8601 timestamp   — when the harness supplied a real one
  * `seq:<n>`               — a harness-supplied monotone counter
  * `pass:<n>`              — the manifest's own pass count, the last-resort fallback

Raw string ordering is wrong on this alphabet in two independent ways, and BOTH were live:

  1. WITHIN a numeric kind, lexicographic ≠ numeric: `'pass:10' <= 'pass:2'` is True, so pass 10
     is reported as earlier than pass 2.
  2. ACROSS kinds the comparison is meaningless — and this was the DEFAULT pairing, not an edge
     case. The skill announces its route with a real `--ts <ISO>`, while the PreToolUse hook
     usually gets no timestamp from the harness and falls back to `pass:<n>`. Since '2' < 'p',
     an ISO route stamp always sorted before a `pass:` tool stamp, so `route_ts <= tool_ts` was
     unconditionally True: P1 could never fire in the configuration the skill actually produces.
     Mirrored, the same flaw invents violations that never happened.

A checker that cannot fail is worse than no checker: it reports a guarantee it never verified.

THE FIX, IN TWO LAYERS
----------------------
  * `compare()` is KIND-AWARE and refuses to guess. Same kind → a real numeric/chronological
    answer. Different kinds, or anything unparseable → None, meaning "not comparable", never a
    fabricated order.
  * Because "not comparable" would leave P1 vacuous whenever the kinds differ, the manifest also
    records a WRITE SEQUENCE for each stamp (`*_seq`), assigned by the manifest itself under its
    own lock. That is a total order the harness witnessed directly, so it is authoritative and
    always comparable regardless of stamp kind. `route_verdict()` prefers it and falls back to
    kind-aware stamp comparison for manifests written before it existed.

Three outcomes, because the honest answer is sometimes "I cannot tell":

  OK           — the route provably preceded investigation
  VIOLATION    — investigation provably came first, or no route was ever announced
  INCONCLUSIVE — the stamps cannot be ordered. NOT reported as OK: silently passing an
                 unverifiable check is the vacuity this module exists to remove. The gate
                 surfaces it to the auditor instead.
"""
import datetime
import re

OK, VIOLATION, INCONCLUSIVE = "ok", "violation", "inconclusive"

# `seq:` and `pass:` are distinct kinds on purpose: both count upward, but they count DIFFERENT
# things (a harness event counter vs. the run's pass number), so a number from one says nothing
# about a number from the other.
_COUNTER = re.compile(r"^(seq|pass):(-?\d+(?:\.\d+)?)$")


def parse(stamp: object) -> tuple[str, object] | None:
    """A stamp as `(kind, comparable_value)`, or None if it is not a stamp we understand.

    Returning None is a real answer — the caller must treat it as "unknown ordering" rather
    than substituting a default, which is exactly how the original `<=` went wrong.
    """
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    text = stamp.strip()

    counter = _COUNTER.match(text)
    if counter:
        return counter.group(1), float(counter.group(2))

    # ISO-8601. fromisoformat handles offsets but (before 3.11) not a trailing 'Z'; normalise it
    # so the common UTC spelling is accepted on every supported version.
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    # Compare naive and aware stamps consistently by reading a naive one as UTC. Mixing them
    # would otherwise raise TypeError mid-comparison.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return "time", moment


def compare(left: object, right: object) -> int | None:
    """-1 / 0 / 1 as `left` is before / same as / after `right`; None when NOT comparable.

    Comparable means both stamps parse AND share a kind. A number of harness events and a
    number of passes are not the same quantity, so no cross-kind ordering is invented.
    """
    a, b = parse(left), parse(right)
    if a is None or b is None or a[0] != b[0]:
        return None
    if a[1] == b[1]:
        return 0
    return -1 if a[1] < b[1] else 1


def route_verdict(run: dict) -> tuple[str, str]:
    """Did routing precede investigation (ADR-20 P1)? Returns (verdict, reason).

    ONE implementation, shared by manifest.py and audit.py. They previously carried separate
    copies of this comparison, which is how the same defect came to exist in both.

    Ordering evidence is used in order of strength:
      1. `*_seq` — the manifest's own write sequence. Harness-assigned under the manifest lock
         at the moment each event was observed, so it is a total order and always comparable.
      2. the raw stamps — kind-aware, for manifests predating the sequence fields.
    """
    route_ts, tool_ts = run.get("route_ts"), run.get("first_tool_ts")
    route_seq, tool_seq = run.get("route_seq"), run.get("first_tool_seq")

    # Nothing investigated yet → nothing can have been done out of order.
    if tool_ts is None and tool_seq is None:
        return OK, "no investigative tool call recorded yet"

    # Investigated, but no route was ever announced. A definite violation and not an ordering
    # question at all: P1 requires the announcement up front, so its absence is the failure.
    if route_ts is None and route_seq is None:
        return VIOLATION, ("investigation began before any route was announced (ADR-20 P1: "
                           "routing is a commitment made up front, not a label applied "
                           "retroactively)")

    if isinstance(route_seq, int) and isinstance(tool_seq, int):
        order, basis = (route_seq > tool_seq) - (route_seq < tool_seq), "write order"
    else:
        order = compare(route_ts, tool_ts)
        basis = "timestamps"
        if order is None:
            return INCONCLUSIVE, (
                f"the route ({route_ts!r}) and first-investigation ({tool_ts!r}) stamps are not "
                f"comparable, so P1 ordering could not be verified either way — read the "
                f"transcript to establish whether the route was announced first")

    if order <= 0:
        return OK, f"route was announced before investigation began (by {basis})"
    return VIOLATION, (f"the route was announced ({route_ts}) AFTER investigation began "
                       f"({tool_ts}) — the routing decision was applied retroactively "
                       f"(ADR-20 P1)")
