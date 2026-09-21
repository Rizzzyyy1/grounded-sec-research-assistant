"""Filing index: which 10-K / 10-Q filings exist for a company, and for which fiscal period.

Two details make this harder than it looks:

1. SEC's ``filings.recent`` table only holds roughly the latest 1,000 filings. For large filers,
   Form 4s and 8-Ks crowd older 10-Ks out of it, so older filings live in extra JSON *pages*
   named in ``filings.files``. We follow those pages (skipping any that cannot contain a year we
   need).
2. SEC does not say which *fiscal* year a filing belongs to. We derive it from the period end
   date and the company's fiscal year end (``core.fiscal``) instead of trusting any SEC field.

Amendments (``10-K/A``) are excluded on purpose: they are partial and would shadow the original.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date
from typing import Any, Protocol

from finsight.config.universe import Universe
from finsight.core.fiscal import fiscal_quarter_for, fiscal_year_for
from finsight.core.logging import get_logger
from finsight.core.schemas import FilingRef, FiscalPeriod, FormType
from finsight.ingestion.edgar.client import document_url, pad_cik

log = get_logger(__name__)

SUPPORTED_FORMS = (FormType.TEN_K, FormType.TEN_Q)


class EdgarSource(Protocol):
    """What the filing index needs from EDGAR. ``EdgarClient`` satisfies it; tests use a fake."""

    def ticker_to_cik(self, ticker: str) -> str: ...

    def get_submissions(self, cik: str) -> dict[str, Any]: ...

    def get_submissions_page(self, name: str) -> dict[str, Any]: ...


def _rows(columns: dict[str, list[Any]]) -> Iterator[dict[str, Any]]:
    """SEC returns a column-oriented table; turn it into one dict per filing."""
    count = len(columns.get("accessionNumber", []))
    for i in range(count):
        yield {key: values[i] for key, values in columns.items() if i < len(values)}


def _to_ref(
    row: dict[str, Any],
    *,
    cik: str,
    ticker: str,
    company: str,
    fiscal_year_end: str,
    wanted: frozenset[str],
) -> FilingRef | None:
    form = row.get("form")
    if form not in wanted:
        return None
    report, filed = row.get("reportDate"), row.get("filingDate")
    if not report or not filed:
        log.warning("edgar.filing_missing_dates", accession=row.get("accessionNumber"))
        return None

    period_end = date.fromisoformat(report)
    is_annual = form == FormType.TEN_K.value
    # A 10-Q can never be Q4: cap at 3 so 52/53-week drift near year end cannot mislabel it.
    period = (
        FiscalPeriod.FY
        if is_annual
        else FiscalPeriod(f"Q{min(3, fiscal_quarter_for(period_end, fiscal_year_end))}")
    )
    return FilingRef(
        cik=cik,
        ticker=ticker.upper(),
        company=company,
        form=FormType(form),
        accession=row["accessionNumber"],
        filed=date.fromisoformat(filed),
        period_of_report=period_end,
        fiscal_year=fiscal_year_for(period_end, fiscal_year_end),
        fiscal_period=period,
        primary_doc=row["primaryDocument"],
        url=document_url(cik, row["accessionNumber"], row["primaryDocument"]),
    )


def _older_pages(submissions: dict[str, Any], min_year: int | None) -> Iterator[str]:
    """Names of extra filing pages that could contain filings from ``min_year`` onward."""
    for page in submissions.get("filings", {}).get("files", []):
        filing_to = page.get("filingTo")
        # A filing is always made in or after its fiscal-year label, so a page whose newest
        # filing predates ``min_year`` cannot hold anything we need.
        if min_year is not None and filing_to and int(filing_to[:4]) < min_year:
            continue
        yield page["name"]


def list_filings(
    source: EdgarSource,
    ticker: str,
    *,
    fiscal_year_end: str,
    forms: Iterable[FormType] = (FormType.TEN_K,),
    fiscal_years: Iterable[int] | None = None,
    cik: str | None = None,
) -> list[FilingRef]:
    """All matching filings for ``ticker``, newest period first.

    ``fiscal_year_end`` is the company's nominal ``MM-DD`` year end (``configs/universe.yaml``).
    Pass ``cik`` to skip the ticker lookup (successor registrants, see ``CompanySpec.cik``).
    """
    form_set = frozenset(FormType(f) for f in forms)
    unsupported = form_set - set(SUPPORTED_FORMS)
    if unsupported:
        raise ValueError(f"unsupported forms: {sorted(f.value for f in unsupported)}")
    wanted = frozenset(f.value for f in form_set)
    years = frozenset(fiscal_years) if fiscal_years is not None else None

    cik = pad_cik(cik or source.ticker_to_cik(ticker))
    submissions = source.get_submissions(cik)
    company = str(submissions.get("name", ticker))

    tables = [submissions.get("filings", {}).get("recent", {})]
    tables += [
        source.get_submissions_page(name)
        for name in _older_pages(submissions, min(years) if years else None)
    ]

    latest: dict[tuple[str, int, str], FilingRef] = {}
    for table in tables:
        for row in _rows(table):
            ref = _to_ref(
                row,
                cik=cik,
                ticker=ticker,
                company=company,
                fiscal_year_end=fiscal_year_end,
                wanted=wanted,
            )
            if ref is None or (years is not None and ref.fiscal_year not in years):
                continue
            key = (ref.form.value, ref.fiscal_year, ref.fiscal_period.value)
            if key not in latest or ref.filed > latest[key].filed:
                latest[key] = ref
    return sorted(latest.values(), key=lambda r: r.period_of_report, reverse=True)


def list_universe_filings(source: EdgarSource, universe: Universe) -> dict[str, list[FilingRef]]:
    """Filing index for every company in the universe, keyed by ticker."""
    return {
        company.ticker: list_filings(
            source,
            company.ticker,
            fiscal_year_end=company.fiscal_year_end,
            forms=universe.forms,
            fiscal_years=universe.fiscal_years,
            cik=company.cik,
        )
        for company in universe.companies
    }
