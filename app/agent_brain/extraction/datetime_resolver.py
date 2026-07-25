"""Resolve extracted date/time PHRASES to absolute values (N4a).

`datetime_extractor` pulls the phrase as spoken ("next Monday", "2 PM").
This module turns that into a concrete date/time so downstream consumers get
something actionable instead of a string they must parse themselves.

Design notes
------------
* **No `dateparser` dependency.** The extractor emits a *closed* grammar —
  ``(next|this|last|by|before|on|coming)`` x
  ``(weekday|week|month|tomorrow|today)``, bare day words, and
  ``H[:MM] am/pm``. That is roughly twenty shapes, all resolvable with the
  stdlib. `dateparser` earns its weight on open-ended multilingual text
  (that is the deferred N4b scope), not here, and it would add
  tzlocal/regex/dateutil/pytz to the lightweight lane that N2 just trimmed.
* **Deterministic.** Every function takes an explicit ``now`` reference.
  Nothing calls ``datetime.now()`` internally, so results are reproducible
  and testable without freezing the clock.
* **Conservative.** Anything ambiguous or unrecognised returns ``None``. A
  wrong deadline on a task recommendation is worse than no deadline — the
  verbatim phrase is always preserved alongside, so no information is lost.

Resolution rules (all relative to ``now``):

===========================  =================================================
phrase                       resolves to
===========================  =================================================
``today`` / ``tonight``      today
``tomorrow``                 today + 1 day
``next <weekday>``           that weekday in the FOLLOWING week
``<weekday>`` (bare, ``on``  the next occurrence at or after today
``by``, ``before``,
``coming``, ``this``)
``last <weekday>``           the most recent occurrence strictly before today
``this week``                today
``next week``                today + 7 days
``last week``                today - 7 days
``this month``               today
``next month``               same day-of-month next month (clamped)
``last month``               same day-of-month last month (clamped)
===========================  =================================================
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta

_WEEKDAY_INDEX: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Qualifiers that mean "the upcoming one", vs. explicit next/last.
_UPCOMING = {"on", "by", "before", "coming", "this"}

_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*$", re.IGNORECASE)


def _shift_months(anchor: date, months: int) -> date:
    """Move `anchor` by whole months, clamping the day to the target month."""
    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _next_weekday(anchor: date, weekday: int, *, strictly_after: bool) -> date:
    delta = (weekday - anchor.weekday()) % 7
    if delta == 0 and strictly_after:
        delta = 7
    return anchor + timedelta(days=delta)


def _previous_weekday(anchor: date, weekday: int) -> date:
    delta = (anchor.weekday() - weekday) % 7
    return anchor - timedelta(days=delta or 7)


def resolve_date_phrase(phrase: str | None, *, now: date | datetime) -> date | None:
    """Resolve a date phrase to a concrete date, or ``None`` if unrecognised.

    Args:
        phrase: the phrase as spoken, e.g. ``"next Monday"``. May be None.
        now: the reference date (a ``datetime`` is reduced to its date).
    """
    if not phrase:
        return None

    anchor = now.date() if isinstance(now, datetime) else now
    tokens = phrase.strip().lower().split()
    if not tokens:
        return None

    # Bare single word: today / tonight / tomorrow / <weekday>
    if len(tokens) == 1:
        word = tokens[0]
        if word in ("today", "tonight"):
            return anchor
        if word == "tomorrow":
            return anchor + timedelta(days=1)
        if word in _WEEKDAY_INDEX:
            return _next_weekday(anchor, _WEEKDAY_INDEX[word], strictly_after=False)
        return None

    if len(tokens) != 2:
        return None

    qualifier, target = tokens

    # "<qualifier> tomorrow/today" — the qualifier adds nothing.
    if target in ("today", "tonight"):
        return anchor
    if target == "tomorrow":
        return anchor + timedelta(days=1)

    if target in _WEEKDAY_INDEX:
        weekday = _WEEKDAY_INDEX[target]
        if qualifier == "next":
            return _next_weekday(anchor, weekday, strictly_after=True)
        if qualifier == "last":
            return _previous_weekday(anchor, weekday)
        if qualifier in _UPCOMING:
            return _next_weekday(anchor, weekday, strictly_after=False)
        return None

    if target == "week":
        if qualifier == "next":
            return anchor + timedelta(days=7)
        if qualifier == "last":
            return anchor - timedelta(days=7)
        if qualifier == "this":
            return anchor
        return None

    if target == "month":
        if qualifier == "next":
            return _shift_months(anchor, 1)
        if qualifier == "last":
            return _shift_months(anchor, -1)
        if qualifier == "this":
            return anchor
        return None

    return None


def resolve_time_phrase(phrase: str | None) -> time | None:
    """Resolve a clock phrase (``"2 PM"``, ``"2:30pm"``) to a time, else None."""
    if not phrase:
        return None
    match = _TIME_RE.match(phrase)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3).lower()

    if not (1 <= hour <= 12) or not (0 <= minute <= 59):
        return None

    if meridiem == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12

    return time(hour=hour, minute=minute)


def resolve_deadline_iso(
    date_phrase: str | None,
    time_phrase: str | None = None,
    *,
    now: date | datetime,
) -> str | None:
    """Combine the phrases into an ISO-8601 string, or ``None``.

    Returns a date (``"2026-07-27"``) when only a date resolves, and a naive
    datetime (``"2026-07-27T14:00:00"``) when a time resolves too. A time
    without a resolvable date yields ``None`` — a bare "2 PM" has no day to
    attach to, and guessing one would risk a wrong deadline.
    """
    resolved_date = resolve_date_phrase(date_phrase, now=now)
    if resolved_date is None:
        return None

    resolved_time = resolve_time_phrase(time_phrase)
    if resolved_time is None:
        return resolved_date.isoformat()
    return datetime.combine(resolved_date, resolved_time).isoformat()
