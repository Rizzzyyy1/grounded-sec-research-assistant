"""Ingestion pipeline against a fake EDGAR: idempotence, isolation and the zero-filings guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from finsight.config.universe import Universe
from finsight.core.exceptions import IngestionError
from finsight.ingestion.pipeline import run_ingestion
from finsight.ingestion.xbrl.store import FactStore

pytestmark = pytest.mark.unit


def _submissions(cik: str, name: str, n_years: int = 2) -> dict[str, Any]:
    rows = [(f"{2024 - i}-09-28", f"{2024 - i}-11-01") for i in range(n_years)]
    return {
        "name": name,
        "filings": {
            "recent": {
                "accessionNumber": [f"{cik}-{r[1][2:4]}-000001" for r in rows],
                "form": ["10-K"] * n_years,
                "reportDate": [r[0] for r in rows],
                "filingDate": [r[1] for r in rows],
                "primaryDocument": [f"doc{i}.htm" for i in range(n_years)],
            },
            "files": [],
        },
    }


def _facts(cik: int) -> dict[str, Any]:
    rows = [
        {
            "start": "2023-10-01",
            "end": "2024-09-28",
            "val": 1000,
            "accn": "A-24",
            "fy": 2024,
            "fp": "FY",
            "form": "10-K",
            "filed": "2024-11-01",
        },
    ]
    return {
        "cik": cik,
        "entityName": "X",
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": rows}}}},
    }


class FakeClient:
    def __init__(
        self, *, empty: set[str] | None = None, broken_facts: set[str] | None = None
    ) -> None:
        self.ciks = {"AAA": "0000000001", "BBB": "0000000002"}
        self.empty, self.broken_facts = empty or set(), broken_facts or set()
        self.doc_calls = 0
        self.lookups: list[str] = []

    def ticker_to_cik(self, ticker: str) -> str:
        self.lookups.append(ticker)
        return self.ciks[ticker]

    def get_submissions(self, cik: str) -> dict[str, Any]:
        return _submissions(cik, f"Co {cik}", 0 if cik in self.empty else 2)

    def get_submissions_page(self, name: str) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("no older pages expected")

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        if cik in self.broken_facts:
            raise IngestionError("boom")
        return _facts(int(cik))

    def get_document(self, cik: str, accession: str, primary_doc: str) -> bytes:
        self.doc_calls += 1
        return f"<html>{accession}</html>".encode()


def universe(extra: dict[str, object] | None = None) -> Universe:
    def co(ticker: str, **kw: object) -> dict[str, object]:
        return {"ticker": ticker, "name": ticker, "sector": "IT", "fiscal_year_end": "09-30", **kw}

    return Universe.model_validate(
        {
            "name": "t",
            "fiscal_years": [2023, 2024],
            "companies": [co("AAA"), co("BBB", **(extra or {}))],
        }
    )


def run(client: FakeClient, tmp_path: Path, store: FactStore, **kw: Any):  # type: ignore[no-untyped-def]
    return run_ingestion(
        kw.pop("universe", universe()), client=client, store=store, raw_dir=tmp_path / "raw", **kw
    )


def test_happy_path_downloads_filings_and_loads_facts(tmp_path: Path) -> None:
    client = FakeClient()
    with FactStore() as store:
        report = run(client, tmp_path, store)
        assert report.ok
        assert report.filings_downloaded == 4
        assert set(report.facts_loaded) == {"AAA", "BBB"}
        assert store.count("filings") == 4
        assert store.count("companies") == 2
        assert store.get_fact("AAA", "revenue", 2024) is not None
        rows = store.sql("SELECT sha256, size_bytes, local_path FROM filings")
        assert rows["sha256"].notna().all()
        assert all(Path(p).is_file() for p in rows["local_path"])


def test_second_run_downloads_nothing(tmp_path: Path) -> None:
    client = FakeClient()
    with FactStore() as store:
        run(client, tmp_path, store)
        again = run(client, tmp_path, store)
    assert again.filings_downloaded == 0
    assert again.filings_skipped == 4
    assert client.doc_calls == 4


def test_one_failing_company_does_not_abort_the_others(tmp_path: Path) -> None:
    client = FakeClient(broken_facts={"0000000001"})
    with FactStore() as store:
        report = run(client, tmp_path, store)
        assert not report.ok
        assert [who for who, _ in report.failures] == ["AAA"]
        assert "boom" in report.failures[0][1]
        assert "BBB" in report.facts_loaded  # the healthy company still completed
        assert store.get_fact("BBB", "revenue", 2024) is not None


def test_zero_filings_is_a_failure_not_silent_success(tmp_path: Path) -> None:
    """Regression (XOM, 2026): the ticker moved to a new holding-company CIK with no 10-Ks."""
    client = FakeClient(empty={"0000000002"})
    with FactStore() as store:
        report = run(client, tmp_path, store)
    assert not report.ok
    ((who, message),) = report.failures
    assert who == "BBB"
    assert "no 10-K filings found" in message
    assert "predecessor CIK" in message  # tells the operator how to fix it


def test_pinned_cik_bypasses_the_ticker_lookup(tmp_path: Path) -> None:
    client = FakeClient()
    client.ciks["BBB"] = "0000000099"  # what the ticker map wrongly says now
    pinned = universe({"cik": "0000000002"})
    with FactStore() as store:
        report = run(client, tmp_path, store, universe=pinned)
    assert report.ok
    assert client.lookups == ["AAA"]  # BBB was never looked up by ticker


def test_ticker_filter_limits_scope(tmp_path: Path) -> None:
    with FactStore() as store:
        report = run(FakeClient(), tmp_path, store, tickers=["bbb"])
        assert set(report.facts_loaded) == {"BBB"}
        assert store.count("companies") == 1


def test_facts_only_and_filings_only_modes(tmp_path: Path) -> None:
    client = FakeClient()
    with FactStore() as store:
        run(client, tmp_path, store, download=False)
        assert client.doc_calls == 0
        assert store.count("facts") > 0
    with FactStore() as store2:
        run(client, tmp_path, store2, load_facts=False)
        assert store2.count("facts") == 0
        assert store2.count("filings") == 4


def test_progress_callback_receives_messages(tmp_path: Path) -> None:
    seen: list[str] = []
    with FactStore() as store:
        run(FakeClient(), tmp_path, store, on_progress=seen.append)
    assert any("AAA" in m for m in seen)
    assert any("downloaded" in m for m in seen)


def test_facts_only_catalogues_exact_later_source_accession(tmp_path: Path) -> None:
    class ComparativeClient(FakeClient):
        def get_company_facts(self, cik: str) -> dict[str, Any]:
            facts = _facts(int(cik))
            facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]["accn"] = f"{cik}-25-000001"
            return facts

        def get_submissions(self, cik: str) -> dict[str, Any]:
            submissions = _submissions(cik, f"Co {cik}", 2)
            recent = submissions["filings"]["recent"]
            recent["accessionNumber"].append(f"{cik}-25-000001")
            recent["form"].append("10-K")
            recent["reportDate"].append("2025-09-27")
            recent["filingDate"].append("2025-11-01")
            recent["primaryDocument"].append("comparative.htm")
            return submissions

    with FactStore() as store:
        report = run(ComparativeClient(), tmp_path, store, download=False)
        assert report.ok
        assert report.fact_source_filings_catalogued == 2
        assert store.filing_url("0000000001-25-000001") == (
            "https://www.sec.gov/Archives/edgar/data/1/000000000125000001/comparative.htm"
        )
