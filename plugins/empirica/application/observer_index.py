"""The observer's store-wide facts for contract ``empirica-ui-view/v1``: pure functions over the per-run rows.

Each run row is what the core published for the run (``core.observer.project``: its ``standing``, ``ask``, ``asked_at``,
``flags``, audit, session and mode) plus the observer's own ``id`` and times. Nothing here reads a store or re-decides a
run: it counts, filters, pages and searches the published fields.

* ``short_id``: a run's compact id, a function of its key alone, so it never changes when other runs are added or removed,
  nor across restarts;
* ``places``: each run's place on its project's island, its ordinal in the project's start order (``started``, then key),
  published once the project is read in full: a run started later takes the next place, so nothing drawn moves. A run
  with no recorded start takes a place in the island's outer band (from ``UNTIMED_FROM``), so a new timed run moves it
  only once the timed runs fill the band; ``island_order``: the islands by their first run's start, then id, so a new
  project takes the next slot;
* ``calendar``: per project and civil day in the viewer's time zone (the adapter's ``day_of``), how many runs were last updated that
  day and the most urgent standing among them;
* ``facets`` and ``aggregates``: counts by standing, ask, project, host, mode and audit, and of the runs (and facts) with
  failed audits, evidence refutations and decisions, each optionally within ``since`` (an epoch; facts are counted by
  their ``at``). Every count says how many runs were not read in full (``not_read``), because unknown is never zero, and
  within a window how many facts have no time (``untimed``);
* ``marks``: the universe marks, columnar;
* ``asked_order``: the needs list's order, the oldest question first;
* ``ORDERS``: each bucket's declared order inside a page (published in the summary): the needs-a-human bucket the needs
  list's (``asked_at``, the oldest first), every other bucket the most recent activity first (``updated``, the newest
  first); a run with no recorded time after every timed one, then key;
* ``page`` and ``search``: one-line rows, filtered, ordered by standing precedence, then inside each bucket by its
  declared order (``ORDERS``), paged by a cursor. A flag within a window (``flag`` with ``since``) is the event's own time
  (``flagged``), the same time the aggregates count it by.

Known limitation (5.0.0): places and slots are recomputed from the runs as they are, never stored, so a deleted run, or a
run whose time is recovered late, may renumber the places after it (``contracts/empirica-ui-view/v1/README.md``).

Times are labels: a window counts a fact by its time. The orders that read them are declared: the needs list (oldest
question first), each bucket of a page (``ORDERS``: the needs-a-human bucket is the needs list, so the Inbox's "Needs a
human" is that list; every other bucket the most recent activity first) and an island's places (start order).
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from core.observer import ASKS, STANDINGS

#: Rows per page, and the most a request may ask for.
PAGE = 100
PAGE_LIMIT = 200
#: The facets a summary counts and a runs page filters by: name -> how a row gives its value (``None``: not read).
FACETS = {
    "standing": lambda r: r["standing"],
    "ask": lambda r: r["ask"],
    "project": lambda r: r["project"],
    "host": lambda r: r["session"]["host"] if r["session"] else None,
    "mode": lambda r: r["mode"],
    "audit": lambda r: r["audit"]["verdict"] if r["audit"] else None,
}
#: The marks' flags, as bits of one integer per run.
FLAGS = ("failed_audit", "refuted", "decided", "changed_since_audit")
#: The facts the value strip counts, each a list of times per run (``facts``). ``decisions`` is every human answer but
#: ``later`` (``core.observer.decisions``, the one owner), also kept per ask as ``decisions:<ask>`` (``DECIDED``).
FACTS = ("failed_audits", "refutations", "decisions")
DECIDED = tuple(f"decisions:{ask}" for ask in ASKS)
#: Every per-run time list ``facts`` gives (and the tally keeps).
COUNTED = FACTS + DECIDED
ID_SIZE = 12
#: The first place of an island's outer band: the runs with no recorded start take places from here (after every timed
#: one, when the timed runs reach it), so a new timed run moves none of them until it fills the band.
UNTIMED_FROM = 96
#: The facts each event flag is timed by (``flagged`` within a window); ``changed_since_audit`` is a state, not an event,
#: so within a window it is the run's ``updated``.
EVENT_OF = {"failed_audit": "failed_audits", "refuted": "refutations", "decided": "decisions"}


def short_id(key: str) -> str:
    """A run's compact id: the first hex digits of the digest of its key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:ID_SIZE]


class AmbiguousId(LookupError):
    """A short id that more than one run's key has (``resolve``): never answered with one of them."""

    def __init__(self, wanted: str, keys: Iterable[str]) -> None:
        super().__init__(f"ambiguous id {wanted!r}")
        self.wanted, self.keys = wanted, tuple(sorted(keys))


def resolve(pairs: Iterable[tuple[str, Any]], wanted: str) -> Any:
    """The value whose key is ``wanted``, else the one whose key's ``short_id`` is; ``None`` when none is. Several keys with
    that short id are ``AmbiguousId`` (raised): an explicit collision, never the first found."""
    by_id = []
    for key, value in pairs:
        if key == wanted:
            return value
        if short_id(key) == wanted:
            by_id.append((key, value))
    if len(by_id) > 1:
        raise AmbiguousId(wanted, (k for k, _ in by_id))
    return by_id[0][1] if by_id else None


def start_order(row: Mapping[str, Any]) -> tuple[bool, float, str]:
    """An island's order: by ``started`` (a run with no recorded start after every timed one), then key."""
    return row["started"] is None, row["started"] or 0.0, row["key"]


def places(rows: Iterable[Mapping[str, Any]], complete: Iterable[str]) -> dict[str, int | None]:
    """Each run's place on its project's island (by key): a timed run's ordinal among the project's timed rows, each
    attempt one, in ``start_order``; a run with no recorded start the next place of the outer band (from
    ``UNTIMED_FROM``, or after every timed run once they reach it), by key. A run started after every other takes the
    next place, so a new run, attempt or project moves no place already given (an untimed one only once the timed runs
    fill the band), and a reload or a restart gives the same places. ``None`` while the project is not read in full (not
    in ``complete``): until then a run read later could still come first."""
    done, by = set(complete), {}
    for r in rows:
        by.setdefault(r["project"], []).append(r)
    out: dict[str, int | None] = {}
    for project, held in by.items():
        timed = sorted((r for r in held if r["started"] is not None), key=start_order)
        untimed = sorted((r for r in held if r["started"] is None), key=lambda r: r["key"])
        band = max(UNTIMED_FROM, len(timed))
        for n, r in [*enumerate(timed), *((band + k, r) for k, r in enumerate(untimed))]:
            out[r["key"]] = n if project in done else None
    return out


def island_order(projects: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The islands' order (each project's slot): by when its earliest run started (``since``; a project with none after
    every timed one), then id, so a project whose first run starts later takes the next slot and none moves."""
    return sorted(projects, key=lambda p: (p["since"] is None, p["since"] or 0.0, p["id"]))


def utc_day(at: float) -> tuple[str, float]:
    """The civil day in UTC of epoch ``at``: its ISO date and the epoch of its first moment (pure arithmetic, Hinnant's
    ``civil_from_days``; this layer reads no clock and no zone database: another zone's days are the adapter's,
    ``adapters.observer.civil``)."""
    days = math.floor(at / 86_400)
    z = days + 719_468
    era = (z if z >= 0 else z - 146_096) // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1_460 + doe // 36_524 - doe // 146_096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d, m = doy - (153 * mp + 2) // 5 + 1, mp + 3 if mp < 10 else mp - 9
    return f"{yoe + era * 400 + (m <= 2):04d}-{m:02d}-{d:02d}", float(days * 86_400)


def utc_only(tz: str):
    """The default zones: UTC alone (``ValueError`` for any other; an adapter gives the observer the rest)."""
    if tz != "UTC":
        raise ValueError(f"unknown time zone {tz!r}")
    return utc_day


def calendar(rows: Iterable[Mapping[str, Any]], projects: list[str], tz: str, day_of) -> dict[str, Any]:
    """The calendar's cells, columnar: for each project (an index into ``projects``) and civil day in ``tz`` (``day_of``:
    an epoch's ISO date ``YYYY-MM-DD`` there and the epoch of that day's first moment) with runs last updated that day,
    how many (``runs``) and the most urgent standing among them (``standing``, an index into ``STANDINGS``: precedence is
    urgency). A run with no ``updated`` is on no day. Ordered by project, then day."""
    index, rank = {p: n for n, p in enumerate(projects)}, {s: n for n, s in enumerate(STANDINGS)}
    cells: dict[tuple[int, str], list] = {}
    for r in rows:
        if r["updated"] is None:
            continue
        day, start = day_of(r["updated"])
        cell = cells.setdefault((index[r["project"]], day), [start, 0, rank[r["standing"]]])
        cell[1] += 1
        cell[2] = min(cell[2], rank[r["standing"]])
    ordered = sorted(cells.items())
    return {"tz": tz, "project": [k[0] for k, _ in ordered], "day": [k[1] for k, _ in ordered],
            "start": [v[0] for _, v in ordered], "runs": [v[1] for _, v in ordered],
            "standing": [v[2] for _, v in ordered]}


def asked_order(need: Mapping[str, Any]) -> tuple[bool, float, str]:
    """The needs list's order: the oldest question first (``asked_at``; one with no recorded time after every timed one),
    then key. Never grouped by project."""
    return need["asked_at"] is None, need["asked_at"] or 0.0, need["key"]


def token(parts: Iterable[str]) -> str:
    """A digest of lines, cheap at volume (an ETag: equal exactly when every part is)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


def facts(detail: Mapping[str, Any]) -> dict[str, list[float | None]]:
    """The times of a run's countable facts (from its detail): failed audits, evidence refutations and human answers."""
    answered = [d for d in detail.get("decisions") or () if d["answer"] != "later"]
    return {"failed_audits": [a["at"] for a in detail.get("audits") or () if a["verdict"] == "failed"],
            "refutations": [d["at"] for d in detail.get("dropped") or () if d["why"] in {"refuted", "spike_failed"}],
            "decisions": [d["at"] for d in answered],
            **{f"decisions:{ask}": [d["at"] for d in answered if d["asked"] == ask] for ask in ASKS}}


def _read(row: Mapping[str, Any]) -> bool:
    return row["diagnostic"] is None


def _inside(at: float | None, since: float | None) -> bool | None:
    """Whether a time is inside the window; ``None`` when there is a window and the time is unknown."""
    if since is None:
        return True
    return None if at is None else at >= since


def aggregates(rows: Iterable[Mapping[str, Any]], run_facts: Mapping[str, Mapping[str, list]],
               since: float | None = None) -> dict[str, Any]:
    """Counts over the runs (``rows``, with their ``facts`` by key), within ``since`` when given: a run is in the window
    when its ``updated`` is, a fact when its ``at`` is. ``by`` counts the read runs per facet value; ``runs`` and ``facts``
    count the runs with, and the number of, failed audits, refutations and decisions."""
    rows = list(rows)
    read = [r for r in rows if _read(r)]
    windowed = [r for r in read if _inside(r["updated"], since)]
    untimed = sum(_inside(r["updated"], since) is None for r in read)
    by = {}
    for name, value in FACETS.items():
        values = Counter(map(value, windowed))
        values.pop(None, None)
        by[name] = dict(sorted(values.items()))
    counts: dict[str, dict[str, int]] = {"runs": dict.fromkeys(COUNTED, 0), "facts": dict.fromkeys(COUNTED, 0),
                                         "untimed": dict.fromkeys(COUNTED, 0)}
    for r in read:
        held = run_facts.get(r["key"])
        if not held:
            continue
        for name in COUNTED:
            inside = [_inside(t, since) for t in held.get(name, ())]
            counts["runs"][name] += any(t is True for t in inside)
            counts["facts"][name] += sum(t is True for t in inside)
            counts["untimed"][name] += sum(t is None for t in inside)
    return _shaped(since, len(rows) - len(read), untimed, by, counts)


def _shaped(since, not_read: int, untimed: int, by: dict, counts: Mapping[str, Mapping[str, int]]) -> dict[str, Any]:
    """The aggregates as published: ``total`` (the read runs counted: every read run has one standing, so it is the
    standing counts' sum, made here once), ``runs``, ``facts`` and ``untimed`` for ``FACTS``, and ``decisions_by`` the
    same three for each ask (``ASKS``)."""
    return {"since": since, "total": sum(by["standing"].values()), "not_read": not_read, "untimed_runs": untimed, "by": by,
            **{part: {name: counts[part][name] for name in FACTS} for part in ("runs", "facts", "untimed")},
            "decisions_by": {ask: {part: counts[part][f"decisions:{ask}"] for part in ("runs", "facts", "untimed")}
                             for ask in ASKS}}


def flagged(row: Mapping[str, Any], held: Mapping[str, list] | None, flag: str, since: float | None) -> bool:
    """Whether the run carries ``flag`` within the window from ``since`` (snapped; ``None``: ever). An event flag
    (``EVENT_OF``) is within the window when one of its events is, by the event's own ``at`` (the aggregates' time), never
    by the run's latest write; ``changed_since_audit`` when the run holds it and was updated inside the window."""
    if row["flags"] is None or not row["flags"][flag]:
        return False
    since = snapped(since)
    if since is None:
        return True
    if flag in EVENT_OF:
        return any(_inside(t, since) is True for t in (held or {}).get(EVENT_OF[flag], ()))
    return _inside(row["updated"], since) is True


def marks(rows: list[Mapping[str, Any]], projects: list[str], complete: Iterable[str],
          run_facts: Mapping[str, Mapping[str, list]] | None = None, since: float | None = None) -> dict[str, Any]:
    """The universe marks, one column per field, aligned by index: the run's ``id``, its project (an index into
    ``projects``), its ``place`` (``places``; null until its project is read in full), its ``attempt`` (the generation),
    standing (an index into ``standings``), ``updated``, ``started``, ``flags`` (bits in ``FLAGS`` order) and
    ``window_flags``, the flags it carries within the summary's window (``flagged``: an event by its own time; without a
    window, ``flags``)."""
    index = {p: n for n, p in enumerate(projects)}
    rank = {s: n for n, s in enumerate(STANDINGS)}
    at = places(rows, complete)
    run_facts = run_facts or {}
    return {"window_flags": [sum(1 << n for n, f in enumerate(FLAGS) if flagged(r, run_facts.get(r["key"]), f, since))
                             for r in rows],
            "standings": list(STANDINGS), "flag_bits": list(FLAGS),
            "id": [r["id"] for r in rows], "project": [index[r["project"]] for r in rows],
            "place": [at[r["key"]] for r in rows], "attempt": [r["generation"] for r in rows],
            "standing": [rank[r["standing"]] for r in rows],
            "updated": [r["updated"] for r in rows], "started": [r["started"] for r in rows],
            "flags": [sum(1 << n for n, f in enumerate(FLAGS) if (r["flags"] or {}).get(f)) for r in rows]}


def line(row: Mapping[str, Any], label: str) -> dict[str, Any]:
    """A run as one line of a page: what it is, where it stands, what it asks and since when, where, and when it changed."""
    session = row["session"] or {}
    return {"key": row["key"], "id": row["id"], "goal": row["goal"], "standing": row["standing"], "ask": row["ask"],
            "asked_at": row["asked_at"], "project": row["project"], "project_label": label,
            "host": session.get("host"), "session": session.get("label"), "updated": row["updated"],
            "generation": row["generation"]}


#: Each bucket's order inside a page, declared once (and published in the summary's ``orders``): which time it reads
#: (``by``, a row field) and which comes ``first``. The needs-a-human bucket is the needs list's (``asked_order``: the
#: oldest question first); every other bucket the most recent activity first. In every bucket a run with no recorded time
#: comes after every timed one, then key.
ORDERS: dict[str, dict[str, str]] = {s: {"by": "asked_at", "first": "oldest"} if s == "needs_human"
                                     else {"by": "updated", "first": "newest"} for s in STANDINGS}
_SIGN = {"oldest": 1.0, "newest": -1.0}


def bucket_time(row: Mapping[str, Any]) -> float | None:
    """The time a row is ordered by inside its bucket (``ORDERS``): its ``asked_at`` or its ``updated``."""
    return row[ORDERS[row["standing"]]["by"]]


def order(row: Mapping[str, Any]) -> tuple[int, bool, float, str]:
    """A page's order: standing precedence; inside a bucket its declared order (``ORDERS``: the needs-a-human bucket the
    oldest question first, every other the most recent activity first; an untimed run after every timed one); then key."""
    rank = STANDINGS.index(row["standing"])
    at = bucket_time(row)
    return rank, at is None, 0.0 if at is None else _SIGN[ORDERS[row["standing"]]["first"]] * at, row["key"]


class StaleCursor(ValueError):
    """A cursor given in another published view than the current one: the walk restarts (``token``: the current view's)."""

    def __init__(self, token: str) -> None:
        super().__init__("stale cursor")
        self.token = token


VIEW_MARK = 16  # the hex digits of the view token a cursor carries


def _mark(view_token: str) -> str:
    return view_token.removeprefix("sha256:")[:VIEW_MARK]


def cursor_of(row: Mapping[str, Any], view_token: str) -> str:
    """Where the next page starts: the view it was given in (its token's first digits), then after this row (its bucket,
    the time its bucket is ordered by, ``-`` for none, and key)."""
    rank, at = STANDINGS.index(row["standing"]), bucket_time(row)
    return f"{_mark(view_token)}~{rank}:{'-' if at is None else repr(float(at))}:{row['key']}"


def cursor_after(cursor: str | None) -> tuple[str, tuple[int, bool, float, str]] | None:
    """A cursor's view mark and where it resumes: after this page order (``order``); ``ValueError`` for one this index
    never gave."""
    if not cursor:
        return None
    mark, sep, rest = cursor.partition("~")
    rank, _, rest = rest.partition(":")
    at, _, key = rest.partition(":")
    if (not sep or len(mark) != VIEW_MARK or any(c not in "0123456789abcdef" for c in mark)
            or not rank.isdigit() or int(rank) >= len(STANDINGS) or not key or not at):
        raise ValueError("bad cursor")
    if at == "-":
        return mark, (int(rank), True, 0.0, key)
    try:
        when = float(at)
    except ValueError:
        raise ValueError("bad cursor") from None
    if not math.isfinite(when):
        raise ValueError("bad cursor")
    return mark, (int(rank), False, _SIGN[ORDERS[STANDINGS[int(rank)]]["first"]] * when, key)


def since_of(raw: str | None) -> float | None:
    """A window's start: an epoch in seconds, finite; ``None`` for no window."""
    if raw is None or raw == "":
        return None
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("since must be a finite epoch")
    return value


def limit_of(raw: str | None) -> int:
    if raw is None or raw == "":
        return PAGE
    if not raw.isdigit() or not 1 <= int(raw) <= PAGE_LIMIT:
        raise ValueError(f"limit must be 1..{PAGE_LIMIT}")
    return int(raw)


def matches(row: Mapping[str, Any], filters: Mapping[str, str], held: Mapping[str, list] | None = None) -> bool:
    """Whether a row has every filtered facet value (``since``: updated inside the window; with a ``flag``, the flag
    within the window instead, ``flagged`` over the run's facts ``held``: the time the aggregates count it by)."""
    for name, wanted in filters.items():
        if name == "since":
            if "flag" not in filters and _inside(row["updated"], snapped(since_of(wanted))) is not True:
                return False
        elif name == "flag":
            since = filters.get("since")
            if not flagged(row, held, wanted, since_of(since) if since is not None else None):
                return False
        elif str(FACETS[name](row)) != wanted:
            return False
    return True


def page(rows: list[Mapping[str, Any]], labels: Mapping[str, str], keep, cursor: str | None,
         limit: int, view_token: str) -> dict[str, Any]:
    """The rows ``keep`` admits in page order after ``cursor``, at most ``limit`` of them, how many it admits in all
    (``total``), the cursor of the next page (``None`` on the last) and the view's ``token``. ``rows`` must already be in
    page order. A cursor given in another view (any run changed since) is ``StaleCursor``: never a page that could skip
    or repeat a row."""
    parsed = cursor_after(cursor)
    if parsed is not None and parsed[0] != _mark(view_token):
        raise StaleCursor(view_token)
    after = None if parsed is None else parsed[1]
    found, total = [], 0
    for row in rows:
        if not keep(row):
            continue
        total += 1
        if (after is None or order(row) > after) and len(found) <= limit:
            found.append(row)
    more = len(found) > limit
    found = found[:limit]
    return {"rows": [line(r, labels[r["project"]]) for r in found], "total": total,
            "next": cursor_of(found[-1], view_token) if more else None, "token": view_token}


def attempts_of(rows: Iterable[Mapping[str, Any]], row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every attempt at the run ``row`` is one of (the same project and run), by generation, itself included. Order is the
    attempt's generation, never a time."""
    return sorted((r for r in rows if r["project"] == row["project"] and r["run"] == row["run"]),
                  key=lambda r: r["generation"])


def sheet(detail: Mapping[str, Any], row: Mapping[str, Any], attempts: list[Mapping[str, Any]]) -> dict[str, Any]:
    """A run's detail as ``GET /api/run`` answers it: the detail, the run's own row (what a reader of the detail alone
    could not know: its standing, state, claims' words and graph) and every attempt's row, so a sheet is one request."""
    return {**detail, "row": row, "attempts": attempts}


def sheet_tag(answer: Mapping[str, Any]) -> str:
    """The ETag of a sheet: the detail's version and each attempt's, equal exactly when nothing it shows has changed."""
    return token([answer["version"], *(a["version"] for a in answer["attempts"])])


def haystack(row: Mapping[str, Any], label: str) -> str:
    """What a search covers, case-folded: the goal, every claim's text, the project label and the session label."""
    session = row["session"] or {}
    return "\n".join(str(x) for x in [row["goal"] or "", *(c["text"] for c in row["claims"] or ()), label,
                                      session.get("label") or ""]).casefold()


def words(query: str) -> list[str]:
    return [w for w in query.casefold().split() if w]


def known(values: Iterable[str], name: str) -> None:
    """Refuse a filter value no row could have (a closed vocabulary)."""
    vocab = {"standing": STANDINGS, "ask": ASKS, "flag": FLAGS}.get(name)
    for value in values:
        if vocab is not None and value not in vocab:
            raise ValueError(f"unknown {name} {value!r}")


HOUR = 3_600  # a window's grain: windows are snapped to whole UTC hours, and contributions kept per hour


def hour_of(at: float) -> int:
    return int(at // HOUR)


def snapped(since: float | None) -> float | None:
    """A window's start as it is counted: the start of its UTC hour."""
    return None if since is None else float(hour_of(since) * HOUR)


class _Bucket:
    """One UTC hour's contributions: the facet values of the read runs updated in it, the facts timed in it and, per
    fact, the runs whose latest timed one is in it. ``gen`` is the tally generation that may change it in place (an
    older one is shared with a published window and is copied before a change)."""

    __slots__ = ("gen", "refs", "by", "facts", "runs")

    def __init__(self, gen: int) -> None:
        self.gen, self.refs = gen, 0
        self.by = {name: Counter() for name in FACETS}
        self.facts, self.runs = Counter(), Counter()

    def copy(self, gen: int) -> "_Bucket":
        other = _Bucket(gen)
        other.refs, other.facts, other.runs = self.refs, Counter(self.facts), Counter(self.runs)
        other.by = {name: Counter(c) for name, c in self.by.items()}
        return other


def _add(counter: Counter, name: Any, sign: int) -> None:
    counter[name] += sign
    if not counter[name]:
        del counter[name]


class Window:
    """What a published view counts windows from: the hour buckets as they were when it was published (shared, never
    changed again) and what no window depends on. ``aggregates(since)`` sums the hours from ``since``'s on: O(hours in
    the window), never O(runs); ``counts['window_buckets']`` says how many it visited."""

    def __init__(self, buckets, hours, untimed_runs, untimed_facts, not_read, counts) -> None:
        self._buckets, self._hours = buckets, hours
        self._untimed_runs, self._untimed_facts, self._not_read = untimed_runs, untimed_facts, not_read
        self._counts = counts

    def aggregates(self, since: float) -> dict[str, Any]:
        import bisect
        by = {name: Counter() for name in FACETS}
        runs, facts = Counter(), Counter()
        start = bisect.bisect_left(self._hours, hour_of(since))
        for hour in self._hours[start:]:
            bucket = self._buckets[hour]
            for name, counter in bucket.by.items():
                by[name].update(counter)
            facts.update(bucket.facts)
            runs.update(bucket.runs)
        self._counts["window_buckets"] += len(self._hours) - start
        return _shaped(snapped(since), self._not_read, self._untimed_runs,
                       {name: dict(sorted((k, v) for k, v in c.items() if v)) for name, c in by.items()},
                       {"runs": runs, "facts": facts, "untimed": self._untimed_facts})


class Tally:
    """The store-wide counts kept up to date one run at a time: ``apply(row, facts, +1)`` adds a run's contribution and
    ``apply(row, facts, -1)`` takes it away, so a pass that changes K runs updates the counts 2K times and never recounts
    the store. Holds the facets and aggregates over every time and the same per UTC hour (``_Bucket``), the coverage,
    each project's run count, its runs that need a human and its earliest start, the runs that need a human, the page order and an order-free digest of
    every run's key and version (for the summary's token). ``window()`` shares the hour buckets with a published view
    (O(hours) references; a bucket changed later is copied first), whose windows are snapped to the whole hour."""

    def __init__(self, counts: Counter | None = None) -> None:
        self._counts = Counter() if counts is None else counts
        self.by = {name: Counter() for name in FACETS}
        self.runs = Counter()
        self.facts = Counter()
        self.read = self.unreadable = self.partial = self.indexed = 0
        self.per_project: Counter = Counter()
        self.needs_per_project: Counter = Counter()  # each project's runs that need a human (the Universe's "K need you")
        self.standings_per_project: Counter = Counter()  # (project, standing): every run of a project, by its standing
        self.started: dict[str, list[float]] = {}
        self.needs: dict[str, dict[str, Any]] = {}
        self.ordered: list[tuple[tuple[int, bool, float, str], str]] = []  # (page order, key), sorted
        self.mix = 0  # the sum of every run's line digest, modulo 2**256: equal exactly when the set of lines is
        self._gen = 0
        self._buckets: dict[int, _Bucket] = {}
        self.hours: list[int] = []  # the hours holding anything, sorted
        self.untimed_runs = 0  # read runs with no ``updated``
        self.untimed_facts: Counter = Counter()  # facts with no time, of read runs

    def _bucket(self, hour: int, sign: int) -> _Bucket:
        """The hour's bucket, writable (copied when a published window shares it), its contributions counted."""
        import bisect
        bucket = self._buckets.get(hour)
        if bucket is None:
            bucket = self._buckets[hour] = _Bucket(self._gen)
            bisect.insort(self.hours, hour)
        elif bucket.gen != self._gen:
            bucket = self._buckets[hour] = bucket.copy(self._gen)
        bucket.refs += sign
        return bucket

    def _settle(self, hour: int) -> None:
        import bisect
        if self._buckets[hour].refs == 0:
            del self._buckets[hour]
            del self.hours[bisect.bisect_left(self.hours, hour)]

    def apply(self, row: Mapping[str, Any], held: Mapping[str, list] | None, sign: int) -> None:
        import bisect
        self._counts["aggregate_updates"] += 1
        self.indexed += sign
        self.per_project[row["project"]] += sign
        self.standings_per_project[(row["project"], row["standing"])] += sign
        if row["started"] is not None:
            started = self.started.setdefault(row["project"], [])
            if sign > 0:
                bisect.insort(started, row["started"])
            else:
                del started[bisect.bisect_left(started, row["started"])]
        if row["diagnostic"] is None:
            self.read += sign
            hour = None if row["updated"] is None else hour_of(row["updated"])
            bucket = None if hour is None else self._bucket(hour, sign)
            if hour is None:
                self.untimed_runs += sign
            for name, value in FACETS.items():
                v = value(row)
                if v is not None:
                    _add(self.by[name], v, sign)
                    if bucket is not None:
                        _add(bucket.by[name], v, sign)
            if hour is not None:
                self._settle(hour)
            for name in COUNTED:
                times = (held or {}).get(name, ())
                self.runs[name] += sign * bool(times)
                self.facts[name] += sign * len(times)
                timed = [t for t in times if t is not None]
                self.untimed_facts[name] += sign * (len(times) - len(timed))
                for t in timed:
                    h = hour_of(t)
                    _add(self._bucket(h, sign).facts, name, sign)
                    self._settle(h)
                if timed:
                    h = hour_of(max(timed))
                    _add(self._bucket(h, sign).runs, name, sign)
                    self._settle(h)
        elif row["diagnostic"] == "unreadable":
            self.unreadable += sign
        else:
            self.partial += sign
        entry = (order(row), row["key"])
        if sign > 0:
            bisect.insort(self.ordered, entry)
        else:
            del self.ordered[bisect.bisect_left(self.ordered, entry)]
        if row["standing"] == "needs_human":
            self.needs_per_project[row["project"]] += sign
            if sign > 0:
                session = row["session"] or {}
                self.needs[row["key"]] = {"id": row["id"], "key": row["key"], "ask": row["ask"],
                                          "asked_at": row["asked_at"], "host": session.get("host") if row["session"] else None,
                                          "session": session.get("label") if row["session"] else None}
            else:
                self.needs.pop(row["key"], None)
        line_digest = int.from_bytes(hashlib.sha256(f"{row['key']} {row['version']}".encode()).digest(), "big")
        self.mix = (self.mix + sign * line_digest) % (1 << 256)

    def aggregates(self) -> dict[str, Any]:
        """What ``aggregates(rows, facts)`` counts over every time, from the kept counts."""
        return _shaped(None, self.indexed - self.read, 0, {name: dict(sorted(c.items())) for name, c in self.by.items()},
                       {"runs": self.runs, "facts": self.facts, "untimed": Counter()})

    def window(self) -> Window:
        """The hour buckets as they are now, for a published view: shared, and copied before the next change."""
        self._gen += 1
        return Window(dict(self._buckets), list(self.hours), self.untimed_runs, Counter(self.untimed_facts),
                      self.indexed - self.read, self._counts)

    def standings(self, project: str) -> dict[str, int]:
        """How many of ``project``'s runs stand at each standing (every run, read or not: they sum to its ``runs``); the
        standings it has none of left out."""
        return {s: self.standings_per_project[(project, s)] for s in STANDINGS if self.standings_per_project[(project, s)]}

    def since(self, project: str) -> float | None:
        started = self.started.get(project)
        return started[0] if started else None
