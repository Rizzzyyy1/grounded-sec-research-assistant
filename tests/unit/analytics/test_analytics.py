"""Analytics: hand-computed values and algebraic properties."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finsight.analytics.dupont import attribute_change, dupont_3, dupont_5
from finsight.analytics.peers import peer_table, percentile_rank
from finsight.analytics.ratios import (
    RATIOS,
    RatioError,
    average,
    cagr,
    compute_ratio,
    format_value,
    safe_div,
    yoy,
)
from finsight.analytics.risk_diff import diff_risk_factors
from finsight.analytics.sentiment import SEED_LEXICON, tone
from finsight.analytics.trends import (
    flag_anomalies,
    growth_series,
    linear_slope,
    rolling_mean,
    series_cagr,
    zscores,
)

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ ratios
def test_core_ratios_by_hand() -> None:
    c = {"gross_profit": 180.0, "revenue": 400.0, "net_income": 100.0, "shareholders_equity": 50.0,
         "total_assets": 350.0, "operating_income": 120.0, "interest_expense": 4.0,
         "current_assets": 150.0, "current_liabilities": 100.0, "inventory": 30.0,
         "operating_cash_flow": 130.0, "capex": 30.0, "long_term_debt": 90.0}  # fmt: skip
    assert compute_ratio("gross_margin", c) == pytest.approx(0.45)
    assert compute_ratio("net_margin", c) == pytest.approx(0.25)
    assert compute_ratio("roe", c) == pytest.approx(2.0)
    assert compute_ratio("current_ratio", c) == pytest.approx(1.5)
    assert compute_ratio("quick_ratio", c) == pytest.approx(1.2)
    assert compute_ratio("interest_coverage", c) == pytest.approx(30.0)
    assert compute_ratio("debt_to_equity", c) == pytest.approx(1.8)
    assert compute_ratio("free_cash_flow", c) == 100.0


def test_average_basis_ratios_need_prior_year_inputs() -> None:
    cur, prior = {"net_income": 100.0, "shareholders_equity": 60.0}, {"shareholders_equity": 40.0}
    assert compute_ratio("roe_avg", cur, prior) == pytest.approx(2.0)  # 100 / avg(60, 40)
    with pytest.raises(RatioError, match="prior year"):
        compute_ratio("roe_avg", cur)


def test_bad_inputs_raise_instead_of_returning_nonsense() -> None:
    with pytest.raises(RatioError, match="zero"):
        compute_ratio("net_margin", {"net_income": 1.0, "revenue": 0.0})
    with pytest.raises(RatioError, match="needs"):
        compute_ratio("gross_margin", {"revenue": 1.0})
    with pytest.raises(RatioError, match="unknown ratio"):
        compute_ratio("magic", {})
    with pytest.raises(RatioError):
        safe_div(1, 0)


def test_days_metrics_and_formatting() -> None:
    dso = compute_ratio("days_sales_outstanding", {"accounts_receivable": 50.0, "revenue": 365.0})
    assert dso == pytest.approx(50.0)
    assert format_value(0.462, "ratio") == "46.2%"
    assert format_value(1.5, "multiple") == "1.50x"
    assert format_value(12.34, "days") == "12.3 days"
    assert format_value(1234567.0, "usd") == "$1,234,567"


def test_growth_helpers() -> None:
    assert yoy(110, 100) == pytest.approx(0.10)
    assert yoy(-50, -100) == pytest.approx(0.50)  # loss halved: an improvement of 50%
    assert cagr(100, 121, 2) == pytest.approx(0.10)
    assert average(10, 20) == 15
    with pytest.raises(RatioError):
        cagr(-1, 5, 2)
    with pytest.raises(RatioError):
        cagr(1, 5, 0)
    with pytest.raises(RatioError):
        yoy(1, 0)


def test_every_registered_ratio_documents_its_formula_and_inputs() -> None:
    for name, spec in RATIOS.items():
        assert spec.name == name and spec.formula and spec.inputs and spec.label


# ------------------------------------------------------------------ DuPont
def test_dupont3_factors_multiply_to_roe() -> None:
    d = dupont_3(net_income=100, revenue=400, assets=350, equity=50)
    assert d.factors["net_margin"] == pytest.approx(0.25)
    assert d.roe == pytest.approx(100 / 50)


def test_dupont5_factors_multiply_to_roe() -> None:
    d = dupont_5(net_income=80, pretax_income=100, ebit=120, revenue=400, assets=350, equity=50)
    assert d.roe == pytest.approx(80 / 50)
    assert d.factors["tax_burden"] == pytest.approx(0.8)


@given(
    ni=st.floats(1, 1e3), rev=st.floats(1, 1e4), assets=st.floats(1, 1e5), eq=st.floats(1, 1e5),
    d_ni=st.floats(0.5, 2), d_rev=st.floats(0.5, 2), d_assets=st.floats(0.5, 2), d_eq=st.floats(0.5, 2),
)  # fmt: skip
def test_property_dupont_identity_and_attribution_sums_exactly(
    ni: float, rev: float, assets: float, eq: float, d_ni: float, d_rev: float, d_assets: float, d_eq: float
) -> None:  # fmt: skip
    before = dupont_3(ni, rev, assets, eq)
    after = dupont_3(ni * d_ni, rev * d_rev, assets * d_assets, eq * d_eq)
    assert before.roe == pytest.approx(ni / eq, rel=1e-9)
    contributions = attribute_change(before, after)
    assert sum(contributions.values()) == pytest.approx(math.log(after.roe / before.roe), abs=1e-9)


def test_attribution_needs_positive_factors_and_matching_shapes() -> None:
    good = dupont_3(10, 100, 200, 50)
    with pytest.raises(RatioError, match="positive"):
        attribute_change(good, dupont_3(-10, 100, 200, 50))
    with pytest.raises(RatioError, match="different"):
        attribute_change(
            good,
            dupont_5(net_income=8, pretax_income=10, ebit=12, revenue=100, assets=200, equity=50),
        )


# ------------------------------------------------------------------ trends
def test_growth_series_skips_gaps_and_zero_bases() -> None:
    assert growth_series({2021: 100, 2022: 110, 2023: 121}) == pytest.approx(
        {2022: 0.10, 2023: 0.10}
    )
    assert growth_series({2021: 100, 2023: 121}) == {}  # a missing year: no fabricated growth
    assert growth_series({2021: 0.0, 2022: 5.0}) == {}


def test_series_cagr_rolling_zscore_slope() -> None:
    assert series_cagr({2021: 100, 2022: 110, 2023: 121}) == pytest.approx(0.10)
    assert rolling_mean([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]
    assert zscores([1, 1, 1]) == [0.0, 0.0, 0.0]
    assert linear_slope({2021: 10, 2022: 12, 2023: 14}) == pytest.approx(2.0)
    with pytest.raises(RatioError):
        series_cagr({2021: 1})


def test_anomaly_flagging() -> None:
    series = dict.fromkeys(range(2015, 2025), 100.0) | {2020: 500.0}
    assert set(flag_anomalies(series, threshold=2.0)) == {2020}
    assert flag_anomalies({2021: 1.0, 2022: 1.0}) == {}


# ------------------------------------------------------------------ peers
def test_peer_table_ranks_best_first_with_median_distance() -> None:
    rows = peer_table({"A": 10.0, "B": 30.0, "C": 20.0})
    assert [r.ticker for r in rows] == ["B", "C", "A"] and [r.rank for r in rows] == [1, 2, 3]
    assert rows[0].vs_median == pytest.approx(10.0)
    assert rows[0].percentile == 100.0 and rows[2].percentile == 0.0
    assert peer_table({}) == []


def test_percentile_rank_handles_ties_and_singletons() -> None:
    assert percentile_rank(5, [5, 5, 5]) == 50.0
    assert percentile_rank(1, [1]) == 100.0


# ------------------------------------------------------------------ risk diff
OLD = [
    "Our business depends on suppliers located in Asia and disruption could harm production.",
    "We face intense competition from larger companies with greater financial resources.",
    "Changes in tax law could materially increase our effective tax rate.",
]
NEW = [
    "Our business depends on suppliers located in Asia and disruption could harm production.",  # same
    "We face intense competition from larger, well-funded companies and new entrants using AI models.",  # reworded
    "Regulation of artificial intelligence could restrict our products and increase compliance costs.",  # new
]


def test_risk_diff_classifies_unchanged_modified_added_removed() -> None:
    diff = diff_risk_factors(OLD, NEW)
    assert diff.unchanged == 1
    assert len(diff.modified) == 1 and "AI models" in diff.modified[0].text
    assert diff.modified[0].counterpart == OLD[1]
    assert len(diff.added) == 1 and "artificial intelligence" in diff.added[0].text
    assert [c.text for c in diff.removed] == [OLD[2]]  # the tax-law risk disappeared
    assert diff.churn == pytest.approx(2 / 3)


def test_risk_diff_edge_cases_and_injected_embeddings() -> None:
    assert diff_risk_factors([], NEW).added and not diff_risk_factors([], NEW).removed
    assert diff_risk_factors(OLD, []).removed and diff_risk_factors(OLD, []).churn == 0.0
    with pytest.raises(ValueError, match="thresholds"):
        diff_risk_factors(OLD, NEW, unchanged=0.3, matched=0.5)
    embed = lambda texts: [[1.0, 0.0] if "Asia" in t else [0.0, 1.0] for t in texts]  # noqa: E731
    diff = diff_risk_factors(OLD[:1], NEW[:1], embed=embed)
    assert diff.unchanged == 1


# ------------------------------------------------------------------ tone
def test_tone_counts_per_thousand_words_and_net_tone() -> None:
    t = tone("Losses and adverse litigation could harm growth and further losses. Strong gains.")
    assert t.words == 12
    assert t.per_1000["negative"] > t.per_1000["positive"] > 0
    assert -1 < t.net_tone < 0
    assert tone("").words == 0 and tone("").net_tone == 0.0
    assert tone("revenue recognition policy").net_tone == 0.0
    assert set(SEED_LEXICON) == {"negative", "positive", "uncertainty", "litigious"}
