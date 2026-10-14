"""The viewer's civil days: an IANA time zone, checked, as the observer's ``day_of`` (contract v1's ``calendar``).

The application reads no clock and no zone database (``tests/v2/test_timeline.NoClockTests``); the zone's days, 23 or
25 hours long across a change of offset, are this adapter's, from ``zoneinfo``. A zone this machine does not know, or a
name that is not a zone's, is a ``ValueError`` (the server's 400).
"""
from __future__ import annotations

import re
from datetime import datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from application import observer_index as _index

_NAME = re.compile(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*")


def tz_of(raw: str | None) -> str:
    """The viewer's IANA time zone (``UTC`` when none is given); ``ValueError`` for one this machine does not know."""
    if raw is None or raw == "":
        return "UTC"
    zone(raw)
    return raw


@lru_cache(maxsize=64)
def zone(tz: str):
    """``day_of`` for zone ``tz``: an epoch's ISO date there and the epoch of that day's first moment."""
    if tz == "UTC":
        return _index.utc_day
    if len(tz) > 64 or not _NAME.fullmatch(tz) or ".." in tz:
        raise ValueError("tz must be an IANA time zone")
    try:
        info = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError(f"unknown time zone {tz!r}") from None

    def day_of(at: float) -> tuple[str, float]:
        day = datetime.fromtimestamp(at, info).date()
        return day.isoformat(), datetime.combine(day, time(0), tzinfo=info).timestamp()

    return day_of
