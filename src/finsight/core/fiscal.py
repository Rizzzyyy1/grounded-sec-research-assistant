"""Fiscal-calendar arithmetic.

SEC metadata is unreliable about fiscal periods (see docs/DATA.md, trap #1), so we derive them
ourselves from two facts we can trust: the *period end date* of a filing and the company's
nominal *fiscal year end* (``MM-DD``, from ``configs/universe.yaml``).

Convention
----------
A fiscal year is labelled by the calendar year in which it **ends**. This matches how Apple,
Microsoft, NVIDIA, Walmart and P&G label theirs (NVIDIA's year ended 28 Jan 2024 is "fiscal
2024"). Companies that label by start year (some retailers) would need a per-company override.

A period belongs to the fiscal year that *contains* it: the first nominal year end falling on or
after the period end. 52/53-week calendars end on a weekday near the nominal date (28 Sep 2024
for a "30 Sep" year end, or 2 Jan 2022 for a "31 Dec" one), so a small tolerance lets a period
that ends a few days *after* the nominal date still count towards the year that just closed.
(Snapping to the *nearest* year end would be wrong: it mislabels mid-year quarters.)
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

_DAYS_PER_MONTH = 30.4375
# 52/53-week years end up to ~a week from the nominal date; 10 days leaves margin without ever
# reaching the next quarter end (~91 days away).
_DRIFT_TOLERANCE = timedelta(days=10)


def _parse_fye(fye: str) -> tuple[int, int]:
    try:
        month_s, day_s = fye.split("-")
        month, day = int(month_s), int(day_s)
        date(2000, month, day)  # validates the combination; a leap year, so 02-29 is legal
    except ValueError as exc:
        raise ValueError(f"fiscal year end must be a valid 'MM-DD', got {fye!r}") from exc
    return month, day


def _nominal(year: int, month: int, day: int) -> date:
    """The nominal fiscal-year-end date in ``year``, clamped for short months / leap days."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def fiscal_year_end_for(period_end: date, fye: str) -> date:
    """Nominal end date of the fiscal year that contains a period ending on ``period_end``."""
    month, day = _parse_fye(fye)
    cutoff = period_end - _DRIFT_TOLERANCE
    for year in (period_end.year - 1, period_end.year, period_end.year + 1):
        candidate = _nominal(year, month, day)
        if candidate >= cutoff:
            return candidate
    raise AssertionError("unreachable: a year end always falls within 366 days")  # pragma: no cover


def fiscal_year_for(period_end: date, fye: str) -> int:
    """Fiscal year label (year in which the fiscal year ends) for a period ending on a date."""
    return fiscal_year_end_for(period_end, fye).year


def fiscal_quarter_for(period_end: date, fye: str) -> int:
    """Fiscal quarter (1-4) that a period ending on ``period_end`` closes.

    Uses elapsed months since the previous nominal year end, rounded to the nearest quarter, so
    52/53-week drift of a few days never flips the answer.
    """
    month, day = _parse_fye(fye)
    this_end = fiscal_year_end_for(period_end, fye)
    prior_end = _nominal(this_end.year - 1, month, day)
    months = (period_end - prior_end).days / _DAYS_PER_MONTH
    return max(1, min(4, round(months / 3)))
