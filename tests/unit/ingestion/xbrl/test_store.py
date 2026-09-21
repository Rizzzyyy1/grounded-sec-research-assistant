"""DuckDB fact store: idempotence, atomicity, constraints and typed reads."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from finsight.core.schemas import FilingRef, FinancialFact, FiscalPeriod, FormType
from finsight.ingestion.xbrl.facts import ParsedFacts, RawFact
from finsight.ingestion.xbrl.store import FactStore

pytestmark = pytest.mark.unit


def fact(
    ticker: str = "AAPL",
    metric: str = "revenue",
    year: int = 2024,
    period: FiscalPeriod = FiscalPeriod.FY,
    value: float = 100.0,
    derived: bool = False,
) -> FinancialFact:
    return FinancialFact(
        ticker=ticker,
        cik="0000320193",
        metric=metric,
        tag="Revenues",
        value=value,
        unit="USD",
        period_type="duration",
        start=date(year - 1, 10, 1),
        end=date(year, 9, 28),
        fiscal_year=year,
        fiscal_period=period,
        form=FormType.TEN_K,
        filed=date(year, 11, 1),
        accession="0000320193-24-000123",
        derived=derived,
    )


def parsed(*facts: FinancialFact, versions: list[RawFact] | None = None) -> ParsedFacts:
    return ParsedFacts(facts=list(facts), versions=versions or [])


def test_replace_is_idempotent() -> None:
    with FactStore() as store:
        for _ in range(3):
            store.replace_company_facts("AAPL", parsed(fact(year=2023), fact(year=2024)))
        assert store.count("facts") == 2


def test_replace_only_touches_the_given_ticker() -> None:
    with FactStore() as store:
        store.replace_company_facts("AAPL", parsed(fact("AAPL")))
        store.replace_company_facts("MSFT", parsed(fact("MSFT")))
        store.replace_company_facts("AAPL", parsed())  # AAPL now has nothing
        assert store.tickers() == ["MSFT"]


def test_duplicate_primary_key_is_rejected_and_rolled_back() -> None:
    with FactStore() as store:
        store.replace_company_facts("AAPL", parsed(fact(value=1.0)))
        with pytest.raises(duckdb.ConstraintException):
            store.replace_company_facts("AAPL", parsed(fact(value=2.0), fact(value=3.0)))
        # the failed replacement must not have destroyed the previous good data
        assert store.get_fact("AAPL", "revenue", 2024).value == 1.0  # type: ignore[union-attr]


def test_get_metric_is_ordered_and_filterable() -> None:
    with FactStore() as store:
        store.replace_company_facts(
            "AAPL", parsed(*(fact(year=y, value=float(y)) for y in (2023, 2021, 2022, 2024)))
        )
        assert list(store.get_metric("aapl", "revenue")["fiscal_year"]) == [2021, 2022, 2023, 2024]
        assert list(store.get_metric("AAPL", "revenue", years=[2022, 2024])["fiscal_year"]) == [
            2022,
            2024,
        ]
        assert store.get_metric("AAPL", "net_income").empty


def test_quarters_and_years_are_kept_apart() -> None:
    with FactStore() as store:
        store.replace_company_facts(
            "AAPL", parsed(fact(value=4.0), fact(period=FiscalPeriod.Q4, value=1.0, derived=True))
        )
        assert store.get_fact("AAPL", "revenue", 2024).value == 4.0  # type: ignore[union-attr]
        q4 = store.get_fact("AAPL", "revenue", 2024, FiscalPeriod.Q4)
        assert q4 is not None
        assert (q4.value, q4.derived) == (1.0, True)


def test_get_fact_roundtrips_a_full_model() -> None:
    original = fact(value=391_035_000_000.0)
    with FactStore() as store:
        store.replace_company_facts("AAPL", parsed(original))
        assert store.get_fact("AAPL", "revenue", 2024) == original
        assert store.get_fact("AAPL", "revenue", 1999) is None


def test_instant_facts_roundtrip_with_null_start() -> None:
    instant = FinancialFact(
        ticker="AAPL",
        cik="0000320193",
        metric="total_assets",
        tag="Assets",
        value=5.0,
        unit="USD",
        period_type="instant",
        start=None,
        end=date(2024, 9, 28),
        fiscal_year=2024,
        fiscal_period=FiscalPeriod.FY,
        form=FormType.TEN_K,
        filed=date(2024, 11, 1),
        accession="0000320193-24-000123",
    )
    with FactStore() as store:
        store.replace_company_facts("AAPL", parsed(instant))
        assert store.get_fact("AAPL", "total_assets", 2024) == instant


def test_version_history_is_kept_oldest_first() -> None:
    def version(value: float, filed: date) -> RawFact:
        return RawFact(
            "revenue",
            "Revenues",
            "USD",
            date(2022, 10, 1),
            date(2023, 9, 30),
            value,
            "10-K",
            filed,
            "0000320193-23-000106",
        )

    with FactStore() as store:
        store.replace_company_facts(
            "AAPL",
            parsed(
                fact(year=2023),
                versions=[version(380, date(2024, 11, 1)), version(383, date(2023, 11, 3))],
            ),
        )
        history = store.versions("AAPL", "revenue", date(2023, 9, 30))
        assert list(history["value"]) == [383, 380]


def test_file_backed_store_persists(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "facts.duckdb"
    with FactStore(path) as store:
        store.replace_company_facts("AAPL", parsed(fact()))
    with FactStore(path) as reopened:
        assert reopened.count("facts") == 1


def test_count_rejects_unknown_tables() -> None:
    with FactStore() as store, pytest.raises(ValueError, match="unknown table"):
        store.count("facts; DROP TABLE facts")


def test_sql_supports_parameters_and_window_functions() -> None:
    with FactStore() as store:
        store.replace_company_facts(
            "AAPL",
            parsed(*(fact(year=y, value=100.0 * (1.1 ** (y - 2022))) for y in (2022, 2023, 2024))),
        )
        df = store.sql(
            "SELECT fiscal_year, value / lag(value) OVER (ORDER BY fiscal_year) - 1 AS yoy "
            "FROM facts WHERE ticker = ? ORDER BY 1",
            ["AAPL"],
        )
        assert df["yoy"].iloc[1] == pytest.approx(0.10)


def test_reregistering_a_filing_keeps_its_download_metadata() -> None:
    """Regression: a facts-only ingestion re-upserted filings without a path/hash and wiped the
    catalogue (INSERT OR REPLACE), so `finsight process` found zero filings."""
    ref = FilingRef(
        cik="0000320193",
        ticker="AAPL",
        company="Apple Inc.",
        form=FormType.TEN_K,
        accession="0000320193-24-000123",
        filed=date(2024, 11, 1),
        period_of_report=date(2024, 9, 28),
        fiscal_year=2024,
        fiscal_period=FiscalPeriod.FY,
        primary_doc="a.htm",
        url="https://example.com/a.htm",
    )
    with FactStore() as store:
        store.upsert_filing(ref, sha256="abc", size_bytes=10, local_path="/data/a.htm")
        store.upsert_filing(ref)  # e.g. `ingest --facts-only`
        row = store.filings().iloc[0]
        assert (row.sha256, row.size_bytes, row.local_path) == ("abc", 10, "/data/a.htm")
        store.upsert_filing(ref, sha256="def", size_bytes=11, local_path="/data/b.htm")
        assert store.filings().iloc[0].sha256 == "def"  # a real update still wins
        assert store.count("filings") == 1
