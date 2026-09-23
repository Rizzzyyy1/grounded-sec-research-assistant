"""Filing index: form filtering, fiscal labelling, older pages, de-duplication."""

from __future__ import annotations

from typing import Any

import pytest

from finsight.config.universe import Universe
from finsight.core.schemas import FiscalPeriod, FormType
from finsight.ingestion.edgar.filings import (
    find_filings_by_accession,
    list_filings,
    list_universe_filings,
)

pytestmark = pytest.mark.unit

CIK = "0000320193"


def row(form: str, report: str, filed: str, n: int, doc: str | None = None) -> dict[str, str]:
    return {
        "accessionNumber": f"{CIK}-{filed[2:4]}-{n:06d}",
        "form": form,
        "reportDate": report,
        "filingDate": filed,
        "primaryDocument": doc or f"doc{n}.htm",
    }


def table(*rows: dict[str, str]) -> dict[str, list[str]]:
    """Column-oriented, exactly like SEC's `filings.recent`."""
    keys = {k for r in rows for k in r}
    return {k: [r.get(k, "") for r in rows] for k in sorted(keys)}


class FakeSource:
    def __init__(
        self,
        recent: dict[str, list[str]],
        pages: dict[str, dict[str, list[str]]] | None = None,
        page_meta: list[dict[str, str]] | None = None,
        name: str = "Apple Inc.",
    ) -> None:
        self._submissions: dict[str, Any] = {
            "name": name,
            "filings": {"recent": recent, "files": page_meta or []},
        }
        self._pages = pages or {}
        self.fetched_pages: list[str] = []

    def ticker_to_cik(self, ticker: str) -> str:
        return CIK

    def get_submissions(self, cik: str) -> dict[str, Any]:
        return self._submissions

    def get_submissions_page(self, name: str) -> dict[str, Any]:
        self.fetched_pages.append(name)
        return self._pages[name]


APPLE_10KS = table(
    row("10-K", "2024-09-28", "2024-11-01", 1),
    row("10-K", "2023-09-30", "2023-11-03", 2),
    row("10-K", "2022-09-24", "2022-10-28", 3),
)


def test_returns_only_requested_forms_and_excludes_amendments() -> None:
    recent = table(
        row("10-K", "2024-09-28", "2024-11-01", 1),
        row("10-K/A", "2024-09-28", "2024-12-01", 2),
        row("10-Q", "2024-06-29", "2024-08-02", 3),
        row("8-K", "2024-10-31", "2024-10-31", 4),
    )
    refs = list_filings(FakeSource(recent), "AAPL", fiscal_year_end="09-30")
    assert [r.accession for r in refs] == [f"{CIK}-24-000001"]


def test_fiscal_year_comes_from_period_end_not_filing_date() -> None:
    refs = list_filings(FakeSource(APPLE_10KS), "AAPL", fiscal_year_end="09-30")
    # Filed Nov 2024 / Nov 2023 / Oct 2022, but labelled by period end (Sep).
    assert [r.fiscal_year for r in refs] == [2024, 2023, 2022]
    assert all(r.fiscal_period is FiscalPeriod.FY for r in refs)


def test_january_year_end_is_labelled_by_the_year_it_ends() -> None:
    recent = table(
        row("10-K", "2024-01-28", "2024-02-21", 1),
        row("10-K", "2023-01-29", "2023-02-24", 2),
    )
    refs = list_filings(FakeSource(recent, name="NVIDIA"), "NVDA", fiscal_year_end="01-31")
    assert [r.fiscal_year for r in refs] == [2024, 2023]


def test_fiscal_year_filter() -> None:
    refs = list_filings(
        FakeSource(APPLE_10KS), "AAPL", fiscal_year_end="09-30", fiscal_years=[2023, 2024]
    )
    assert sorted(r.fiscal_year for r in refs) == [2023, 2024]


def test_quarterly_filings_get_fiscal_quarters() -> None:
    recent = table(
        row("10-Q", "2023-12-30", "2024-02-02", 1),
        row("10-Q", "2024-03-30", "2024-05-03", 2),
        row("10-Q", "2024-06-29", "2024-08-02", 3),
    )
    refs = list_filings(FakeSource(recent), "AAPL", fiscal_year_end="09-30", forms=[FormType.TEN_Q])
    by_period = {r.fiscal_period: r.fiscal_year for r in refs}
    assert by_period == {FiscalPeriod.Q1: 2024, FiscalPeriod.Q2: 2024, FiscalPeriod.Q3: 2024}


def test_older_pages_are_followed_to_reach_early_filings() -> None:
    page = table(row("10-K", "2021-09-25", "2021-10-29", 9))
    source = FakeSource(
        APPLE_10KS,
        pages={"CIK0000320193-submissions-001.json": page},
        page_meta=[{"name": "CIK0000320193-submissions-001.json", "filingTo": "2021-12-30"}],
    )
    refs = list_filings(
        source, "AAPL", fiscal_year_end="09-30", fiscal_years=[2021, 2022, 2023, 2024]
    )
    assert sorted(r.fiscal_year for r in refs) == [2021, 2022, 2023, 2024]
    assert source.fetched_pages == ["CIK0000320193-submissions-001.json"]


def test_pages_that_cannot_contain_needed_years_are_not_fetched() -> None:
    source = FakeSource(
        APPLE_10KS,
        pages={"old.json": table(row("10-K", "2015-09-26", "2015-10-28", 7))},
        page_meta=[{"name": "old.json", "filingTo": "2019-12-31"}],
    )
    list_filings(source, "AAPL", fiscal_year_end="09-30", fiscal_years=[2021, 2022])
    assert source.fetched_pages == []  # saves a request per company


def test_duplicate_period_keeps_latest_filing() -> None:
    recent = table(
        row("10-K", "2024-09-28", "2024-11-01", 1),
        row("10-K", "2024-09-28", "2024-11-15", 2),  # re-filed
    )
    refs = list_filings(FakeSource(recent), "AAPL", fiscal_year_end="09-30")
    assert [r.accession for r in refs] == [f"{CIK}-24-000002"]


def test_exact_fact_accession_keeps_comparative_filing_outside_universe_years() -> None:
    recent = table(
        row("10-K", "2024-09-28", "2024-11-01", 1),
        row("10-K", "2025-09-27", "2025-11-01", 2),
    )
    requested = f"{CIK}-25-000002"
    refs = find_filings_by_accession(
        FakeSource(recent), "AAPL", fiscal_year_end="09-30", accessions={requested}
    )
    assert [r.accession for r in refs] == [requested]
    assert refs[0].fiscal_year == 2025


def test_exact_fact_accession_does_not_invent_missing_filing() -> None:
    refs = find_filings_by_accession(
        FakeSource(APPLE_10KS),
        "AAPL",
        fiscal_year_end="09-30",
        accessions={f"{CIK}-26-999999"},
    )
    assert refs == []


def test_rows_without_dates_are_skipped_not_fatal() -> None:
    recent = table(row("10-K", "", "2024-11-01", 1), row("10-K", "2023-09-30", "2023-11-03", 2))
    refs = list_filings(FakeSource(recent), "AAPL", fiscal_year_end="09-30")
    assert [r.fiscal_year for r in refs] == [2023]


def test_filing_fields_are_populated() -> None:
    (ref,) = list_filings(
        FakeSource(table(row("10-K", "2024-09-28", "2024-11-01", 1, doc="aapl-20240928.htm"))),
        "aapl",
        fiscal_year_end="09-30",
    )
    assert ref.ticker == "AAPL"
    assert ref.company == "Apple Inc."
    assert ref.cik == CIK
    assert ref.label == "AAPL 10-K FY2024"
    assert ref.url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000001/aapl-20240928.htm"
    )


def test_results_are_newest_period_first() -> None:
    refs = list_filings(FakeSource(APPLE_10KS), "AAPL", fiscal_year_end="09-30")
    assert [r.period_of_report for r in refs] == sorted(
        (r.period_of_report for r in refs), reverse=True
    )


def test_8k_is_not_supported_yet() -> None:
    with pytest.raises(ValueError, match="unsupported forms"):
        list_filings(
            FakeSource(APPLE_10KS), "AAPL", fiscal_year_end="09-30", forms=[FormType.EIGHT_K]
        )


def test_universe_listing_is_keyed_by_ticker() -> None:
    universe = Universe.model_validate(
        {
            "name": "mini",
            "fiscal_years": [2023, 2024],
            "companies": [
                {"ticker": "AAPL", "name": "Apple", "sector": "IT", "fiscal_year_end": "09-30"},
                {"ticker": "MSFT", "name": "Microsoft", "sector": "IT", "fiscal_year_end": "06-30"},
            ],
        }
    )
    result = list_universe_filings(FakeSource(APPLE_10KS), universe)
    assert set(result) == {"AAPL", "MSFT"}
    assert sorted(r.fiscal_year for r in result["AAPL"]) == [2023, 2024]
