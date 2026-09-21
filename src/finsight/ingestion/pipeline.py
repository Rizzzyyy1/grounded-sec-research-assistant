"""Ingestion orchestration: EDGAR -> raw filings on disk + normalised facts in DuckDB.

Resumable and idempotent: filings already on disk with a matching hash are skipped, and a
company's facts are replaced atomically. A failure for one company (network blip, malformed
payload) is recorded in the report and does *not* abort the others.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from finsight.config.universe import Universe
from finsight.core.exceptions import FinSightError, IngestionError
from finsight.core.logging import get_logger
from finsight.ingestion.edgar.downloader import download_filing
from finsight.ingestion.edgar.filings import list_filings
from finsight.ingestion.xbrl.facts import parse_company_facts
from finsight.ingestion.xbrl.store import FactStore

log = get_logger(__name__)

#: Ratios such as averages and YoY growth need earlier years than the ones we report on.
HISTORY_YEARS = 2


class IngestClient(Protocol):
    """Everything ingestion needs from EDGAR; ``EdgarClient`` satisfies it, tests use a fake."""

    def ticker_to_cik(self, ticker: str) -> str: ...
    def get_submissions(self, cik: str) -> dict[str, Any]: ...
    def get_submissions_page(self, name: str) -> dict[str, Any]: ...
    def get_company_facts(self, cik: str) -> dict[str, Any]: ...
    def get_document(self, cik: str, accession: str, primary_doc: str) -> bytes: ...


@dataclass
class IngestionReport:
    filings_downloaded: int = 0
    filings_skipped: int = 0
    facts_loaded: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def run_ingestion(
    universe: Universe,
    *,
    client: IngestClient,
    store: FactStore,
    raw_dir: Path,
    tickers: Iterable[str] | None = None,
    download: bool = True,
    load_facts: bool = True,
    force: bool = False,
    on_progress: Callable[[str], None] = lambda _msg: None,
) -> IngestionReport:
    report = IngestionReport()
    wanted = {t.upper() for t in tickers} if tickers else None
    min_year = min(universe.fiscal_years) - HISTORY_YEARS

    for company in universe.companies:
        if wanted is not None and company.ticker not in wanted:
            continue
        on_progress(f"{company.ticker}: indexing filings")
        try:
            cik = company.cik or client.ticker_to_cik(company.ticker)
            store.upsert_company(
                company.ticker,
                cik=cik,
                name=company.name,
                sector=company.sector,
                fiscal_year_end=company.fiscal_year_end,
            )
            refs = list_filings(
                client,
                company.ticker,
                fiscal_year_end=company.fiscal_year_end,
                forms=universe.forms,
                fiscal_years=universe.fiscal_years,
                cik=cik,
            )
            if not refs:
                # Zero filings is never "fine" for a universe member. It usually means the CIK
                # is wrong - e.g. a holding-company reorganisation moved the ticker (XOM, 2026).
                raise IngestionError(
                    f"no {'/'.join(f.value for f in universe.forms)} filings found for CIK {cik}; "
                    "if the company reorganised, pin the predecessor CIK in the universe file"
                )
        except (FinSightError, ValueError) as exc:
            report.failures.append((company.ticker, f"filing index: {exc}"))
            log.error("ingest.company_failed", ticker=company.ticker, stage="index", error=str(exc))
            continue

        for ref in refs:
            try:
                if download:
                    result = download_filing(client, ref, raw_dir, force=force)
                    store.upsert_filing(
                        ref,
                        sha256=result.sha256,
                        size_bytes=result.size_bytes,
                        local_path=str(result.path),
                    )
                    if result.status == "downloaded":
                        report.filings_downloaded += 1
                        on_progress(f"{ref.label}: downloaded {result.size_bytes / 1e6:.1f} MB")
                    else:
                        report.filings_skipped += 1
                else:
                    store.upsert_filing(ref)
            except (FinSightError, OSError) as exc:
                report.failures.append((ref.label, f"download: {exc}"))
                log.error("ingest.filing_failed", filing=ref.label, error=str(exc))

        if load_facts:
            on_progress(f"{company.ticker}: loading XBRL facts")
            try:
                parsed = parse_company_facts(
                    client.get_company_facts(cik),
                    ticker=company.ticker,
                    fiscal_year_end=company.fiscal_year_end,
                    min_fiscal_year=min_year,
                )
                store.replace_company_facts(company.ticker, parsed)
                report.facts_loaded[company.ticker] = len(parsed.facts)
            except (FinSightError, ValueError) as exc:
                report.failures.append((company.ticker, f"facts: {exc}"))
                log.error(
                    "ingest.company_failed", ticker=company.ticker, stage="facts", error=str(exc)
                )
    return report
