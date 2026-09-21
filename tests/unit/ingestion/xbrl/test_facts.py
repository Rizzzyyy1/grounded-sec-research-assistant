"""companyfacts parsing: every documented SEC data trap has a test here (docs/DATA.md §4)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from finsight.core.schemas import FiscalPeriod, FormType
from finsight.ingestion.xbrl.facts import ParsedFacts, parse_company_facts

pytestmark = pytest.mark.unit

FYE = "09-30"  # Apple-style year end


def row(
    tag: str,
    end: str,
    val: float,
    *,
    start: str | None = None,
    unit: str = "USD",
    form: str = "10-K",
    filed: str = "2024-11-01",
    accn: str = "0000320193-24-000123",
    fy: int = 0,
    fp: str = "FY",
) -> tuple[str, str, dict[str, Any]]:
    body: dict[str, Any] = {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if start:
        body["start"] = start
    return tag, unit, body


def payload(*rows: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for tag, unit, body in rows:
        facts.setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append(body)
    return {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": facts}}


def parse(*rows: tuple[str, str, dict[str, Any]], fye: str = FYE, **kw: Any) -> ParsedFacts:
    return parse_company_facts(payload(*rows), ticker="aapl", fiscal_year_end=fye, **kw)


def find(parsed: ParsedFacts, metric: str, year: int, period: FiscalPeriod = FiscalPeriod.FY):  # type: ignore[no-untyped-def]
    matches = [
        f
        for f in parsed.facts
        if (f.metric, f.fiscal_year, f.fiscal_period) == (metric, year, period)
    ]
    assert len(matches) <= 1, f"duplicate facts for {metric} {year} {period}"
    return matches[0] if matches else None


REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


# ---------------- trap 1: fy/fp describe the filing, not the period
def test_fiscal_year_comes_from_dates_not_from_sec_fy_field() -> None:
    # The FY2024 annual value, re-reported in the FY2025 10-K, is tagged fy=2025.
    parsed = parse(row(REV, "2024-09-28", 391_035, start="2023-10-01", fy=2025))
    fact = find(parsed, "revenue", 2024)
    assert fact is not None
    assert fact.fiscal_period is FiscalPeriod.FY
    assert fact.ticker == "AAPL"
    assert fact.cik == "0000320193"


def test_same_period_in_two_filings_yields_one_fact() -> None:
    parsed = parse(
        row(
            REV, "2024-09-28", 391_035, start="2023-10-01", fy=2024, accn="A-24", filed="2024-11-01"
        ),
        row(
            REV, "2024-09-28", 391_035, start="2023-10-01", fy=2025, accn="A-25", filed="2025-10-31"
        ),
    )
    assert len([f for f in parsed.facts if f.metric == "revenue"]) == 1
    assert len(parsed.versions) == 2  # both kept as history


# ------------------------------------------------------------------ trap 2: restatements
def test_latest_filed_value_wins_and_history_is_retained() -> None:
    parsed = parse(
        row(REV, "2023-09-30", 383_285, start="2022-10-01", filed="2023-11-03", accn="A-23"),
        row(REV, "2023-09-30", 380_000, start="2022-10-01", filed="2024-11-01", accn="A-24"),
    )
    fact = find(parsed, "revenue", 2023)
    assert fact is not None
    assert fact.value == 380_000
    assert fact.filed == date(2024, 11, 1)
    assert sorted(v.value for v in parsed.versions) == [380_000, 383_285]


# ------------------------------------------------------------------ trap 7: tag drift
def test_tag_fallback_is_resolved_per_period() -> None:
    parsed = parse(
        row("SalesRevenueNet", "2018-09-29", 265_595, start="2017-10-01"),
        row(REV, "2022-09-24", 394_328, start="2021-09-26"),
    )
    assert find(parsed, "revenue", 2018).tag == "SalesRevenueNet"  # type: ignore[union-attr]
    assert find(parsed, "revenue", 2022).tag == REV  # type: ignore[union-attr]


def test_higher_priority_tag_wins_when_both_exist_for_a_period() -> None:
    parsed = parse(
        row(REV, "2024-09-28", 100, start="2023-10-01"),
        row("Revenues", "2024-09-28", 110, start="2023-10-01"),
    )
    fact = find(parsed, "revenue", 2024)
    assert (fact.tag, fact.value) == ("Revenues", 110)  # type: ignore[union-attr]


# ------------------------------------------------------------------ period classification
def test_quarters_annual_and_ytd_are_told_apart_by_length() -> None:
    parsed = parse(
        row(REV, "2023-12-30", 119, start="2023-10-01", form="10-Q"),  # Q1 (3 months)
        row(REV, "2024-03-30", 90, start="2023-12-31", form="10-Q"),  # Q2 standalone
        row(REV, "2024-03-30", 209, start="2023-10-01", form="10-Q"),  # 6-month YTD: dropped
        row(REV, "2024-09-28", 391, start="2023-10-01"),  # FY
    )
    assert find(parsed, "revenue", 2024, FiscalPeriod.Q1).value == 119  # type: ignore[union-attr]
    assert find(parsed, "revenue", 2024, FiscalPeriod.Q2).value == 90  # type: ignore[union-attr]
    assert find(parsed, "revenue", 2024).value == 391  # type: ignore[union-attr]
    assert not [f for f in parsed.facts if f.duration_days and 150 < f.duration_days < 200]


def test_three_month_values_inside_a_10k_are_quarters_not_fy() -> None:
    """Regression from real data: SEC tags these 'fp=FY' because they sit in a 10-K."""
    parsed = parse(row(REV, "2018-06-30", 53_265, start="2018-04-01", fy=2018, fp="FY"))
    assert find(parsed, "revenue", 2018, FiscalPeriod.Q3).value == 53_265  # type: ignore[union-attr]
    assert find(parsed, "revenue", 2018, FiscalPeriod.FY) is None


def test_balance_sheet_dates_map_to_fy_or_quarter() -> None:
    parsed = parse(
        row("Assets", "2024-09-28", 365, unit="USD"),
        row("Assets", "2024-06-29", 331, unit="USD", form="10-Q"),
        row("Assets", "2023-12-30", 353, unit="USD", form="10-Q"),
    )
    assert find(parsed, "total_assets", 2024).value == 365  # type: ignore[union-attr]
    assert find(parsed, "total_assets", 2024, FiscalPeriod.Q3).value == 331  # type: ignore[union-attr]
    assert find(parsed, "total_assets", 2024, FiscalPeriod.Q1).value == 353  # type: ignore[union-attr]
    assert all(f.period_type == "instant" and f.start is None for f in parsed.facts)


def test_january_year_end_labels_by_year_it_ends() -> None:
    parsed = parse(row(REV, "2024-01-28", 60_922, start="2023-01-30"), fye="01-31")
    assert find(parsed, "revenue", 2024) is not None


# ------------------------------------------------------------------ trap 4: Q4 is never reported
def _annual_and_ytd(tag: str = REV, unit: str = "USD", fy: float = 1000, nine: float = 700) -> list:  # type: ignore[type-arg]
    return [
        row(tag, "2024-09-28", fy, start="2023-10-01", unit=unit),
        row(
            tag, "2024-06-29", nine, start="2023-10-01", unit=unit, form="10-Q", filed="2024-08-02"
        ),
    ]


def test_q4_is_derived_from_fy_minus_nine_months_and_flagged() -> None:
    q4 = find(parse(*_annual_and_ytd()), "revenue", 2024, FiscalPeriod.Q4)
    assert q4 is not None
    assert q4.value == 300
    assert q4.derived is True
    assert q4.start == date(2024, 6, 30)
    assert q4.end == date(2024, 9, 28)


def test_reported_q4_is_preferred_over_derivation() -> None:
    rows = [*_annual_and_ytd(), row(REV, "2024-09-28", 310, start="2024-06-30")]
    q4 = find(parse(*rows), "revenue", 2024, FiscalPeriod.Q4)
    assert q4 is not None
    assert (q4.value, q4.derived) == (310, False)


def test_per_share_values_are_not_additive_so_no_q4() -> None:
    rows = _annual_and_ytd("EarningsPerShareDiluted", unit="USD/shares", fy=6.08, nine=4.5)
    assert find(parse(*rows), "eps_diluted", 2024, FiscalPeriod.Q4) is None


def test_balance_sheet_items_never_get_a_q4() -> None:
    parsed = parse(row("Assets", "2024-09-28", 365))
    assert find(parsed, "total_assets", 2024, FiscalPeriod.Q4) is None


# ------------------------------------------------------------------ filtering
def test_only_10k_and_10q_are_used() -> None:
    parsed = parse(
        row(REV, "2024-09-28", 1, start="2023-10-01", form="8-K"),
        row(REV, "2023-09-30", 2, start="2022-10-01", form="10-K/A"),
        row(REV, "2022-09-24", 3, start="2021-09-26", form="10-K"),
    )
    assert [f.fiscal_year for f in parsed.facts if f.metric == "revenue"] == [2022]


def test_wrong_shape_for_metric_is_ignored() -> None:
    # Assets is an instant metric; a duration-shaped row for it must not be accepted.
    assert parse(row("Assets", "2024-09-28", 5, start="2023-10-01")).facts == []


def test_min_fiscal_year_filter() -> None:
    parsed = parse(
        row(REV, "2019-09-28", 1, start="2018-09-30"),
        row(REV, "2022-09-24", 2, start="2021-09-26"),
        min_fiscal_year=2021,
    )
    assert [f.fiscal_year for f in parsed.facts] == [2022]


def test_unknown_units_and_tags_are_ignored() -> None:
    assert parse(row(REV, "2024-09-28", 1, start="2023-10-01", unit="EUR")).facts == []
    assert parse(row("SomeCustomTag", "2024-09-28", 1, start="2023-10-01")).facts == []


# ------------------------------------------------------------------ derived gaps
def test_gross_profit_is_derived_when_not_reported() -> None:
    parsed = parse(
        row(REV, "2024-09-28", 1000, start="2023-10-01"),
        row("CostOfGoodsAndServicesSold", "2024-09-28", 600, start="2023-10-01"),
    )
    gp = find(parsed, "gross_profit", 2024)
    assert gp is not None
    assert (gp.value, gp.derived, gp.tag) == (400, True, "derived:revenue-cost_of_revenue")


def test_reported_gross_profit_is_not_overwritten() -> None:
    parsed = parse(
        row(REV, "2024-09-28", 1000, start="2023-10-01"),
        row("CostOfGoodsAndServicesSold", "2024-09-28", 600, start="2023-10-01"),
        row("GrossProfit", "2024-09-28", 399, start="2023-10-01"),
    )
    gp = find(parsed, "gross_profit", 2024)
    assert (gp.value, gp.derived) == (399, False)  # type: ignore[union-attr]


def test_no_gross_profit_without_cost_of_revenue_as_for_a_bank() -> None:
    assert (
        find(parse(row("Revenues", "2024-09-28", 1000, start="2023-10-01")), "gross_profit", 2024)
        is None
    )


def test_total_liabilities_derived_only_when_missing() -> None:
    parsed = parse(row("Assets", "2024-09-28", 500), row("StockholdersEquity", "2024-09-28", 200))
    tl = find(parsed, "total_liabilities", 2024)
    assert (tl.value, tl.derived) == (300, True)  # type: ignore[union-attr]
    reported = parse(
        row("Assets", "2024-09-28", 500),
        row("StockholdersEquity", "2024-09-28", 200),
        row("Liabilities", "2024-09-28", 290),
    )
    assert find(reported, "total_liabilities", 2024).derived is False  # type: ignore[union-attr]


# ------------------------------------------------------------------ output contract
def test_output_is_deterministic_and_sorted() -> None:
    rows = [
        row(REV, "2024-09-28", 1, start="2023-10-01"),
        row("Assets", "2024-09-28", 2),
        row(REV, "2023-09-30", 3, start="2022-10-01"),
    ]
    a, b = parse(*rows), parse(*reversed(rows))
    assert [f.model_dump() for f in a.facts] == [f.model_dump() for f in b.facts]
    keys = [(f.metric, f.fiscal_year) for f in a.facts]
    assert keys == sorted(keys)


def test_facts_validate_against_schema_and_forms() -> None:
    parsed = parse(row(REV, "2024-09-28", 1, start="2023-10-01", form="10-K"))
    assert parsed.facts[0].form is FormType.TEN_K


def test_total_liabilities_derivation_subtracts_mezzanine_equity() -> None:
    """Tesla-style redeemable NCI sits between liabilities and equity."""
    parsed = parse(
        row("Assets", "2024-09-28", 500),
        row(
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "2024-09-28",
            200,
        ),
        row("RedeemableNoncontrollingInterestEquityCarryingAmount", "2024-09-28", 30),
    )
    assert find(parsed, "total_liabilities", 2024).value == 270  # type: ignore[union-attr]
    assert find(parsed, "total_equity", 2024).value == 200  # type: ignore[union-attr]
    assert find(parsed, "shareholders_equity", 2024).value == 200  # type: ignore[union-attr]


def test_parent_equity_and_total_equity_diverge_when_there_is_nci() -> None:
    parsed = parse(
        row("StockholdersEquity", "2024-09-28", 180),
        row(
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "2024-09-28",
            200,
        ),
    )
    assert (
        find(parsed, "shareholders_equity", 2024).value == 180
    )  # parent only: ROE denominator  # type: ignore[union-attr]
    assert find(parsed, "total_equity", 2024).value == 200  # type: ignore[union-attr]
