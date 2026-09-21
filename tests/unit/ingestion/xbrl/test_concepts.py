"""The metric taxonomy is data that other code depends on by *name*; guard those names."""

from __future__ import annotations

import pytest

from finsight.ingestion.xbrl.concepts import CANONICAL_METRICS, DERIVED_METRICS, METRICS

pytestmark = pytest.mark.unit


def test_metric_names_are_unique() -> None:
    names = [m.name for m in METRICS]
    assert len(names) == len(set(names))


def test_every_metric_has_tags_without_duplicates() -> None:
    for spec in METRICS:
        assert spec.tags, spec.name
        assert len(spec.tags) == len(set(spec.tags)), spec.name


def test_names_referenced_by_derivations_and_quality_checks_exist() -> None:
    """Regression: `total_equity` was referenced by SQL/derivations but never defined, so the
    accounting-identity check joined nothing and silently reported a clean pass."""
    referenced = {
        "revenue",
        "cost_of_revenue",
        "gross_profit",  # gross-profit derivation
        "total_assets",
        "total_liabilities",
        "total_equity",
        "mezzanine_equity",  # identity
        "shareholders_equity",
    }
    assert referenced <= set(CANONICAL_METRICS)
    assert set(DERIVED_METRICS) <= set(CANONICAL_METRICS)


def test_period_type_and_additivity_are_consistent() -> None:
    for spec in METRICS:
        if spec.period_type == "instant":
            assert not spec.additive, f"{spec.name}: balance-sheet values cannot be summed"
        if spec.unit != "USD":
            assert not spec.additive, f"{spec.name}: per-share values are not additive"


def test_parent_and_total_equity_use_different_priorities() -> None:
    parent, total = CANONICAL_METRICS["shareholders_equity"], CANONICAL_METRICS["total_equity"]
    assert parent.tags[0] == "StockholdersEquity"
    assert total.tags[0] == "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"


def test_banks_are_excluded_from_metrics_they_cannot_have() -> None:
    for name in ("cost_of_revenue", "gross_profit", "inventory", "capex", "operating_income"):
        assert "Financials" in CANONICAL_METRICS[name].na_sectors, name
