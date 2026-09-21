"""Coverage matrix and accounting-identity checks."""

from __future__ import annotations

from datetime import date

import pytest

from finsight.config.universe import Universe
from finsight.core.schemas import FinancialFact, FiscalPeriod, FormType
from finsight.ingestion.xbrl.facts import ParsedFacts
from finsight.ingestion.xbrl.quality import (
    check_accounting_identity,
    coverage_matrix,
    coverage_rate,
    identity_periods_checked,
    raw_availability,
    render_coverage_markdown,
    stale_known_gaps,
    unexplained_gaps,
)
from finsight.ingestion.xbrl.store import FactStore

pytestmark = pytest.mark.unit


def make(
    ticker: str, metric: str, value: float, year: int = 2024, derived: bool = False
) -> FinancialFact:
    instant = metric.startswith(("total_", "mezz"))
    return FinancialFact(
        ticker=ticker,
        cik="0000000001",
        metric=metric,
        tag="T",
        value=value,
        unit="USD",
        period_type="instant" if instant else "duration",
        start=None if instant else date(year - 1, 10, 1),
        end=date(year, 9, 28),
        fiscal_year=year,
        fiscal_period=FiscalPeriod.FY,
        form=FormType.TEN_K,
        filed=date(year, 11, 1),
        accession="0000000001-24-000001",
        derived=derived,
    )


def universe(**gaps: dict[str, str]) -> Universe:
    def company(ticker: str, sector: str, known: dict[str, str]) -> dict[str, object]:
        return {
            "ticker": ticker,
            "name": ticker,
            "sector": sector,
            "fiscal_year_end": "09-30",
            "known_gaps": known,
        }

    return Universe.model_validate(
        {
            "name": "t",
            "fiscal_years": [2024],
            "companies": [
                company("TECH", "Information Technology", gaps.get("TECH", {})),
                company("BANK", "Financials", gaps.get("BANK", {})),
            ],
        }
    )


def store_with(*facts: FinancialFact) -> FactStore:
    store = FactStore()
    for ticker in {f.ticker for f in facts}:
        store.replace_company_facts(
            ticker, ParsedFacts([f for f in facts if f.ticker == ticker], [])
        )
    return store


def status(matrix, ticker: str, metric: str) -> str:  # type: ignore[no-untyped-def]
    row = matrix[(matrix.ticker == ticker) & (matrix.metric == metric)]
    return str(row["status"].iloc[0])


def test_statuses_reported_derived_not_applicable_and_missing() -> None:
    with store_with(
        make("TECH", "revenue", 1), make("TECH", "gross_profit", 1, derived=True)
    ) as st:
        m = coverage_matrix(st, universe())
    assert status(m, "TECH", "revenue") == "reported"
    assert status(m, "TECH", "gross_profit") == "derived"
    assert status(m, "BANK", "gross_profit") == "n/a"  # a bank has no gross profit
    assert status(m, "TECH", "net_income") == "missing"


def test_optional_metrics_never_appear_as_gaps() -> None:
    with store_with(make("TECH", "revenue", 1)) as st:
        m = coverage_matrix(st, universe())
    assert "mezzanine_equity" not in set(m["metric"])


def test_known_gap_turns_missing_into_explained_and_lifts_the_rate() -> None:
    with store_with(make("TECH", "revenue", 1)) as st:
        bare = coverage_matrix(st, universe())
        explained = coverage_matrix(st, universe(TECH={"net_income": "not disclosed"}))
    assert status(bare, "TECH", "net_income") == "missing"
    assert status(explained, "TECH", "net_income") == "not reported"
    assert explained[explained.metric == "net_income"]["note"].iloc[0] == "not disclosed"
    assert coverage_rate(explained) > coverage_rate(bare)
    assert len(unexplained_gaps(explained)) == len(unexplained_gaps(bare)) - 1


def test_raw_availability_counts_explained_gaps_against_the_company() -> None:
    with store_with(make("TECH", "revenue", 1)) as st:
        m = coverage_matrix(st, universe(TECH={"net_income": "not disclosed"}))
    assert raw_availability(m) < coverage_rate(m)  # honesty check: explained != available


def test_declared_gap_with_data_in_every_year_is_stale() -> None:
    with store_with(make("TECH", "net_income", 5)) as st:
        m = coverage_matrix(st, universe(TECH={"net_income": "old explanation"}))
    stale = stale_known_gaps(m)
    assert list(zip(stale.ticker, stale.metric, strict=True)) == [("TECH", "net_income")]


def test_partial_gap_declaration_is_not_stale() -> None:
    u = Universe.model_validate(
        {
            "name": "t",
            "fiscal_years": [2023, 2024],
            "companies": [
                {
                    "ticker": "TECH",
                    "name": "T",
                    "sector": "IT",
                    "fiscal_year_end": "09-30",
                    "known_gaps": {"share_repurchases": "none in FY2024"},
                }
            ],
        }
    )
    with store_with(make("TECH", "share_repurchases", 5, year=2023)) as st:
        m = coverage_matrix(st, u)
    assert stale_known_gaps(m).empty


def test_identity_flags_real_deviation_and_counts_periods_checked() -> None:
    with store_with(
        make("TECH", "total_assets", 100),
        make("TECH", "total_liabilities", 60),
        make("TECH", "total_equity", 30),  # 10% unexplained
    ) as st:
        assert identity_periods_checked(st) == 1
        bad = check_accounting_identity(st)
    assert len(bad) == 1
    assert bad["deviation"].iloc[0] == pytest.approx(0.10)


def test_identity_accepts_mezzanine_equity_as_the_explanation() -> None:
    facts = [
        make("TECH", "total_assets", 100),
        make("TECH", "total_liabilities", 60),
        make("TECH", "total_equity", 30),
        make("TECH", "mezzanine_equity", 10),
    ]
    with store_with(*facts) as st:
        assert check_accounting_identity(st).empty


def test_identity_ignores_derived_liabilities_and_reports_zero_checked() -> None:
    """A derived value satisfies the identity by construction, so it must not count as checked -
    otherwise an empty comparison would look like a clean pass."""
    with store_with(
        make("TECH", "total_assets", 100),
        make("TECH", "total_liabilities", 60, derived=True),
        make("TECH", "total_equity", 30),
    ) as st:
        assert identity_periods_checked(st) == 0
        assert check_accounting_identity(st).empty


def test_markdown_report_has_all_sections() -> None:
    with store_with(make("TECH", "revenue", 1)) as st:
        m = coverage_matrix(st, universe(TECH={"net_income": "not disclosed"}))
        report = render_coverage_markdown(
            m, check_accounting_identity(st), identity_periods_checked(st)
        )
    for heading in (
        "# Data coverage report",
        "## Coverage by company",
        "## Explained gaps",
        "## Unexplained gaps",
        "## Accounting identity",
    ):
        assert heading in report
    assert "not disclosed" in report
    assert "Raw availability" in report
