"""The parsed fact store must agree with figures published in the companies' own 10-Ks.

Integration test: it needs the real store built by `finsight ingest`, so it is skipped on a
fresh clone / in CI. Expected values are USD millions copied from the filed financial statements.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from finsight.core.schemas import FiscalPeriod
from finsight.ingestion.xbrl.store import FactStore

pytestmark = pytest.mark.integration

PUBLISHED = [
    ("AAPL", "revenue", 2024, 391_035),
    ("AAPL", "net_income", 2024, 93_736),
    ("AAPL", "gross_profit", 2024, 180_683),
    ("AAPL", "total_assets", 2024, 364_980),
    ("MSFT", "revenue", 2024, 245_122),
    ("MSFT", "net_income", 2024, 88_136),
    ("NVDA", "revenue", 2024, 60_922),
    ("NVDA", "net_income", 2024, 29_760),
    ("JPM", "net_income", 2023, 49_552),
    ("AMZN", "revenue", 2023, 574_785),
    ("WMT", "revenue", 2024, 648_125),
    ("GOOGL", "revenue", 2023, 307_394),
    ("TSLA", "revenue", 2023, 96_773),
    ("JNJ", "revenue", 2023, 85_159),
]


DB = Path(__file__).resolve().parents[2] / "data" / "processed" / "facts.duckdb"


@pytest.fixture(scope="module")
def store() -> Iterator[FactStore]:
    if not DB.is_file():
        pytest.skip("fact store not built; run `finsight ingest` first")
    with FactStore(DB) as opened:
        yield opened


@pytest.mark.parametrize(("ticker", "metric", "year", "millions"), PUBLISHED)
def test_matches_published_figure(
    store: FactStore, ticker: str, metric: str, year: int, millions: int
) -> None:
    fact = store.get_fact(ticker, metric, year)
    assert fact is not None, f"{ticker} {metric} FY{year} missing"
    assert fact.value / 1e6 == pytest.approx(millions, abs=1)


def test_derived_q4_matches_the_published_quarter(store: FactStore) -> None:
    q4 = store.get_fact("AAPL", "revenue", 2024, FiscalPeriod.Q4)
    assert q4 is not None
    assert q4.derived
    assert q4.value / 1e6 == pytest.approx(94_930, abs=1)


def test_xom_history_survives_the_holding_company_reorganisation(store: FactStore) -> None:
    assert store.get_fact("XOM", "revenue", 2023) is not None
    assert store.sql("SELECT count(*) n FROM filings WHERE ticker='XOM'")["n"].iloc[0] == 5
