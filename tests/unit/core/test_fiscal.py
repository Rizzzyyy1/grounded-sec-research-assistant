"""Fiscal-calendar logic: the trap docs/DATA.md calls out as #1."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finsight.core.fiscal import fiscal_quarter_for, fiscal_year_end_for, fiscal_year_for

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("period_end", "fye", "expected"),
    [
        # Apple: 52/53-week year ending the last Saturday of September
        (date(2024, 9, 28), "09-30", 2024),
        (date(2021, 9, 25), "09-30", 2021),
        # Microsoft / P&G: 30 June
        (date(2024, 6, 30), "06-30", 2024),
        # NVIDIA: last Sunday of January -> labelled by the year it ENDS
        (date(2024, 1, 28), "01-31", 2024),
        (date(2023, 1, 29), "01-31", 2023),
        (date(2025, 1, 26), "01-31", 2025),
        # Walmart: 31 January
        (date(2024, 1, 31), "01-31", 2024),
        # Calendar year, including a 52/53-week year that ends in early January
        (date(2021, 12, 31), "12-31", 2021),
        (date(2022, 1, 1), "12-31", 2021),
        (date(2022, 1, 2), "12-31", 2021),
        (date(2024, 12, 28), "12-31", 2024),
    ],
)
def test_fiscal_year_labels(period_end: date, fye: str, expected: int) -> None:
    assert fiscal_year_for(period_end, fye) == expected


@pytest.mark.parametrize(
    ("period_end", "fye", "quarter"),
    [
        (date(2023, 12, 30), "09-30", 1),  # Apple Q1 FY24
        (date(2024, 3, 30), "09-30", 2),
        (date(2024, 6, 29), "09-30", 3),
        (date(2023, 9, 30), "06-30", 1),  # Microsoft Q1 FY24
        (date(2023, 12, 31), "06-30", 2),
        (date(2024, 3, 31), "06-30", 3),
        (date(2023, 4, 30), "01-31", 1),  # NVIDIA / Walmart Q1
        (date(2023, 7, 30), "01-31", 2),
        (date(2023, 10, 29), "01-31", 3),
        (date(2024, 3, 31), "12-31", 1),
    ],
)
def test_fiscal_quarters(period_end: date, fye: str, quarter: int) -> None:
    assert fiscal_quarter_for(period_end, fye) == quarter


@pytest.mark.parametrize(
    ("period_end", "fye", "expected"),
    [
        # Regression: mid-year quarter ends belong to the year that is still open.
        (date(2023, 12, 30), "09-30", 2024),  # Apple Q1 FY24 (was mislabelled FY23)
        (date(2024, 3, 30), "09-30", 2024),
        (date(2023, 9, 30), "06-30", 2024),  # Microsoft Q1 FY24
        (date(2023, 4, 30), "01-31", 2024),  # NVIDIA / Walmart Q1 FY24
        (date(2024, 3, 31), "12-31", 2024),
    ],
)
def test_mid_year_periods_belong_to_the_open_fiscal_year(
    period_end: date, fye: str, expected: int
) -> None:
    assert fiscal_year_for(period_end, fye) == expected


def test_leap_day_nominal_year_end_is_clamped() -> None:
    assert fiscal_year_end_for(date(2023, 2, 27), "02-29") == date(2023, 2, 28)
    assert fiscal_year_end_for(date(2024, 3, 1), "02-29") == date(2024, 2, 29)


@pytest.mark.parametrize("bad", ["13-01", "00-10", "02-30", "September", "9/30", ""])
def test_invalid_fiscal_year_end_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="MM-DD"):
        fiscal_year_for(date(2024, 1, 1), bad)


_FYES = st.sampled_from(["01-31", "03-31", "06-30", "09-30", "12-31"])


@given(fye=_FYES, year=st.integers(1995, 2060), drift=st.integers(-6, 6))
def test_period_end_within_a_week_of_nominal_keeps_its_year(
    fye: str, year: int, drift: int
) -> None:
    """52/53-week drift of up to ~a week never changes the fiscal year label."""
    month, day = int(fye[:2]), int(fye[3:])
    nominal = date(year, month, day)
    assert fiscal_year_for(nominal + timedelta(days=drift), fye) == year


@given(fye=_FYES, year=st.integers(1995, 2060), q=st.integers(1, 4), drift=st.integers(-6, 6))
def test_quarter_roundtrip_is_stable_under_drift(fye: str, year: int, q: int, drift: int) -> None:
    month, day = int(fye[:2]), int(fye[3:])
    prior_end = date(year - 1, month, day)
    quarter_end = prior_end + timedelta(days=round(q * 91.3125)) + timedelta(days=drift)
    assert fiscal_quarter_for(quarter_end, fye) == q
    assert fiscal_year_for(quarter_end, fye) == year
