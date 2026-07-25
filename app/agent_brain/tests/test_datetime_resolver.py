"""Coverage for date/time phrase resolution (N4a).

All cases use an explicit reference date, so nothing depends on the wall
clock. REFERENCE is a Wednesday — chosen so "next Monday" and a bare
"Monday" resolve to *different* dates, which is the rule most likely to be
got wrong.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.agent_brain.extraction.datetime_resolver import (
    resolve_date_phrase,
    resolve_deadline_iso,
    resolve_time_phrase,
)

REFERENCE = date(2026, 7, 22)  # Wednesday
assert REFERENCE.weekday() == 2


# --------------------------------------------------------------------------- #
# Dates                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        # Bare day words
        ("today", date(2026, 7, 22)),
        ("tonight", date(2026, 7, 22)),
        ("tomorrow", date(2026, 7, 23)),
        # Bare weekday -> next occurrence at or after today
        ("friday", date(2026, 7, 24)),
        ("wednesday", date(2026, 7, 22)),  # today itself
        ("monday", date(2026, 7, 27)),
        # "next <weekday>" -> the FOLLOWING week's occurrence
        ("next monday", date(2026, 7, 27)),
        ("next wednesday", date(2026, 7, 29)),  # NOT today
        # Upcoming qualifiers behave like the bare form
        ("by friday", date(2026, 7, 24)),
        ("before friday", date(2026, 7, 24)),
        ("on thursday", date(2026, 7, 23)),
        ("coming monday", date(2026, 7, 27)),
        ("this friday", date(2026, 7, 24)),
        # Past
        ("last monday", date(2026, 7, 20)),
        ("last wednesday", date(2026, 7, 15)),  # a week back, not today
        # Weeks / months
        ("this week", date(2026, 7, 22)),
        ("next week", date(2026, 7, 29)),
        ("last week", date(2026, 7, 15)),
        ("this month", date(2026, 7, 22)),
        ("next month", date(2026, 8, 22)),
        ("last month", date(2026, 6, 22)),
        # Qualifier + relative day
        ("by tomorrow", date(2026, 7, 23)),
        ("by today", date(2026, 7, 22)),
    ],
)
def test_resolve_date_phrase(phrase: str, expected: date) -> None:
    assert resolve_date_phrase(phrase, now=REFERENCE) == expected


def test_case_and_whitespace_insensitive() -> None:
    assert resolve_date_phrase("  NeXt MoNdAy  ", now=REFERENCE) == date(2026, 7, 27)


@pytest.mark.parametrize(
    "phrase",
    [None, "", "   ", "sometime", "next quarter", "by whenever", "the 5th", "next next monday"],
)
def test_unresolvable_returns_none(phrase: str | None) -> None:
    # Conservative by design: a wrong deadline is worse than no deadline.
    assert resolve_date_phrase(phrase, now=REFERENCE) is None


@pytest.mark.parametrize("phrase", ["maybe monday", "maybe week", "maybe month"])
def test_unknown_qualifier_returns_none(phrase: str) -> None:
    # A two-token phrase whose qualifier isn't recognised must not be guessed
    # at, even though the target word (weekday/week/month) is understood.
    assert resolve_date_phrase(phrase, now=REFERENCE) is None


def test_accepts_datetime_reference() -> None:
    ref = datetime(2026, 7, 22, 15, 30)
    assert resolve_date_phrase("tomorrow", now=ref) == date(2026, 7, 23)


def test_month_end_is_clamped() -> None:
    # 31 Jan + 1 month has no 31 Feb; clamp to the last valid day.
    assert resolve_date_phrase("next month", now=date(2026, 1, 31)) == date(2026, 2, 28)
    assert resolve_date_phrase("last month", now=date(2026, 3, 31)) == date(2026, 2, 28)


def test_leap_year_clamp() -> None:
    assert resolve_date_phrase("next month", now=date(2028, 1, 31)) == date(2028, 2, 29)


def test_year_boundary() -> None:
    assert resolve_date_phrase("next month", now=date(2026, 12, 15)) == date(2027, 1, 15)
    assert resolve_date_phrase("last month", now=date(2026, 1, 15)) == date(2025, 12, 15)


# --------------------------------------------------------------------------- #
# Times                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("2 PM", time(14, 0)),
        ("2pm", time(14, 0)),
        ("2:30pm", time(14, 30)),
        ("10 am", time(10, 0)),
        ("12 am", time(0, 0)),  # midnight
        ("12 pm", time(12, 0)),  # noon
        ("11:59 PM", time(23, 59)),
    ],
)
def test_resolve_time_phrase(phrase: str, expected: time) -> None:
    assert resolve_time_phrase(phrase) == expected


@pytest.mark.parametrize("phrase", [None, "", "14:00", "25 pm", "0 am", "2:75pm", "noon", "2"])
def test_unresolvable_time_returns_none(phrase: str | None) -> None:
    assert resolve_time_phrase(phrase) is None


# --------------------------------------------------------------------------- #
# Combined ISO output                                                         #
# --------------------------------------------------------------------------- #


def test_iso_date_only() -> None:
    assert resolve_deadline_iso("next monday", now=REFERENCE) == "2026-07-27"


def test_iso_date_and_time() -> None:
    assert resolve_deadline_iso("next monday", "2 PM", now=REFERENCE) == "2026-07-27T14:00:00"


def test_iso_time_without_date_is_none() -> None:
    # A bare "2 PM" has no day to attach to; guessing one risks a wrong deadline.
    assert resolve_deadline_iso(None, "2 PM", now=REFERENCE) is None


def test_iso_unresolvable_date_ignores_time() -> None:
    assert resolve_deadline_iso("next quarter", "2 PM", now=REFERENCE) is None


def test_iso_unresolvable_time_falls_back_to_date() -> None:
    assert resolve_deadline_iso("tomorrow", "half past two", now=REFERENCE) == "2026-07-23"
