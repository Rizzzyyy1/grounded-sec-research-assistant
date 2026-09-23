"""Numeric-consistency checks: what counts as a checkable figure, and (separately, more strictly)
what counts as a financial figure a claim can be excused from bracketing by."""

from __future__ import annotations

import pytest

from finsight.generation.verification import figures_in, financial_figures_in, unverified_numbers

pytestmark = pytest.mark.unit


def test_figures_in_finds_a_bare_number_a_standard_reference_would_also_match() -> None:
    # figures_in is deliberately permissive - it backs unverified_numbers, whose job is to catch a
    # fabricated figure, so it must not miss a real one just because it lacks $ or %.
    assert figures_in("revenue was 637959") == {"637959"}
    assert figures_in("the ISO 27001 standard") == {"27001"}


def test_financial_figures_in_accepts_dollar_and_percent_figures() -> None:
    assert financial_figures_in("Revenue was $637,959 million.") == {"637959"}
    assert financial_figures_in("Net income increased by 54%.") == {"54"}


def test_financial_figures_in_rejects_a_bare_number_with_no_dollar_or_percent_sign() -> None:
    # ERROR_ANALYSIS.md 3g: the nat-txt-035 case - "ISO 27001" is not a financial figure, and must
    # not be treated as one just because it happens to also appear in a retrieved passage.
    assert financial_figures_in("the ISO 27001 international standard") == set()
    assert financial_figures_in("in Item 1A of the filing") == set()


def test_financial_figures_in_still_excludes_fiscal_years_and_identifiers() -> None:
    assert financial_figures_in("for fiscal 2024 the company reported $58,471 million") == {"58471"}
    assert financial_figures_in("accession 0000000001-24-000001, a 10-K filing") == set()


def test_unverified_numbers_is_unaffected_by_the_stricter_financial_check() -> None:
    # unverified_numbers (used to flag an invented figure) keeps using figures_in, not
    # financial_figures_in - a bare, unsourced number should still be flagged even without $ or %.
    assert unverified_numbers("about 90% of its output", []) == ["90%"]
