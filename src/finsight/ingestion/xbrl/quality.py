"""Data-quality checks and the coverage matrix.

The coverage matrix answers, for every (company x metric x fiscal year): is there a value, was
it reported or derived by us, or is it legitimately not applicable (a bank has no gross profit)?
Every gap is therefore either *explained* (n/a) or *visible* (missing) - never silent.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from finsight.config.universe import Universe
from finsight.core.schemas import FiscalPeriod
from finsight.ingestion.xbrl.concepts import METRICS, MetricSpec
from finsight.ingestion.xbrl.store import FactStore

REPORTED, DERIVED, NOT_APPLICABLE, NOT_REPORTED, MISSING = (
    "reported",
    "derived",
    "n/a",
    "not reported",
    "missing",
)
#: Statuses that carry no value by design and are therefore excluded from the coverage rate.
_EXPLAINED = (NOT_APPLICABLE, NOT_REPORTED)


def coverage_matrix(
    store: FactStore,
    universe: Universe,
    *,
    specs: Sequence[MetricSpec] = METRICS,
    period: FiscalPeriod = FiscalPeriod.FY,
) -> pd.DataFrame:
    """Long-format table: ticker, sector, metric, fiscal_year, status, tag, note, stale_gap.

    ``n/a``          the sector never has this metric (a bank has no gross profit)
    ``not reported`` this company does not disclose it; the reason is in ``known_gaps``
    ``missing``      applicable, not explained, not found: a bug or an undocumented gap
    ``stale_gap``    a declared known gap that actually has data (the explanation has rotted)
    """
    have = store.sql(
        "SELECT ticker, metric, fiscal_year, tag, derived FROM facts "
        "WHERE fiscal_period = ? AND unit IN ('USD', 'USD/shares')",
        [period.value],
    )
    index = {(r.ticker, r.metric, int(r.fiscal_year)): r for r in have.itertuples()}
    rows: list[dict[str, object]] = []
    for company in universe.companies:
        for spec in (s for s in specs if not s.optional):
            for year in universe.fiscal_years:
                hit = index.get((company.ticker, spec.name, year))
                note, stale = "", False
                if hit is not None:
                    status, tag = (DERIVED if hit.derived else REPORTED), hit.tag
                    stale = spec.name in company.known_gaps
                elif company.sector in spec.na_sectors:
                    status, tag = NOT_APPLICABLE, ""
                elif spec.name in company.known_gaps:
                    status, tag, note = NOT_REPORTED, "", company.known_gaps[spec.name]
                else:
                    status, tag = MISSING, ""
                rows.append(
                    {
                        "ticker": company.ticker,
                        "sector": company.sector,
                        "metric": spec.name,
                        "fiscal_year": year,
                        "status": status,
                        "tag": tag,
                        "note": note,
                        "stale_gap": stale,
                    }
                )
    return pd.DataFrame(rows)


def coverage_rate(matrix: pd.DataFrame) -> float:
    """Share of applicable, not-explained-absent cells that hold a value."""
    applicable = matrix[~matrix["status"].isin(_EXPLAINED)]
    if applicable.empty:
        return 1.0
    return float((applicable["status"] != MISSING).mean())


def raw_availability(matrix: pd.DataFrame) -> float:
    """Share of all non-``n/a`` cells that hold a value - explained gaps count *against* it."""
    considered = matrix[matrix["status"] != NOT_APPLICABLE]
    if considered.empty:
        return 1.0
    return float((~considered["status"].isin([MISSING, NOT_REPORTED])).mean())


def coverage_by_ticker(matrix: pd.DataFrame) -> pd.DataFrame:
    applicable = matrix[~matrix["status"].isin(_EXPLAINED)]
    return (
        applicable.assign(ok=applicable["status"] != MISSING)
        .groupby("ticker")["ok"]
        .mean()
        .rename("coverage")
        .reset_index()
    )


def unexplained_gaps(matrix: pd.DataFrame) -> pd.DataFrame:
    return matrix[matrix["status"] == MISSING]


def stale_known_gaps(matrix: pd.DataFrame) -> pd.DataFrame:
    """Declared gaps that now have data: the explanation should be deleted or corrected."""
    declared = matrix[matrix["stale_gap"]].drop_duplicates(["ticker", "metric"])
    # A declaration is only stale when the metric has no gap in *any* year; partial gaps
    # (e.g. "no buyback in FY2025") are legitimately declared while other years have data.
    has_gap = matrix[matrix["status"] == NOT_REPORTED].drop_duplicates(["ticker", "metric"])
    keys = set(zip(has_gap["ticker"], has_gap["metric"], strict=True))
    return declared[
        [(t, m) not in keys for t, m in zip(declared["ticker"], declared["metric"], strict=True)]
    ]


def identity_periods_checked(store: FactStore) -> int:
    """How many periods have reported assets, liabilities *and* total equity to compare.

    Reported next to the violation count so that an empty join (e.g. a metric that silently
    stopped being extracted) can never be mistaken for a clean pass.
    """
    df = store.sql(
        """
        SELECT count(*) AS n FROM facts a
        JOIN facts l ON l.ticker = a.ticker AND l.fiscal_year = a.fiscal_year
                     AND l.fiscal_period = a.fiscal_period AND l.metric = 'total_liabilities'
        JOIN facts e ON e.ticker = a.ticker AND e.fiscal_year = a.fiscal_year
                     AND e.fiscal_period = a.fiscal_period AND e.metric = 'total_equity'
        WHERE a.metric = 'total_assets' AND NOT l.derived
        """
    )
    return int(df["n"].iloc[0])


def check_accounting_identity(store: FactStore, *, tolerance: float = 0.01) -> pd.DataFrame:
    """Rows where Assets deviates from Liabilities + Equity + Mezzanine by more than ``tolerance``.

    Uses reported (non-derived) liabilities only - a derived value satisfies the identity by
    construction. Equity is ``total_equity`` (including non-controlling interests) and redeemable
    NCI ("mezzanine equity", sitting between liabilities and equity) is added when reported.
    """
    return store.sql(
        """
        SELECT a.ticker, a.fiscal_year, a.fiscal_period,
               a.value AS assets, l.value AS liabilities, e.value AS equity,
               COALESCE(m.value, 0) AS mezzanine,
               abs(a.value - (l.value + e.value + COALESCE(m.value, 0))) / a.value AS deviation
        FROM facts a
        JOIN facts l ON l.ticker = a.ticker AND l.fiscal_year = a.fiscal_year
                     AND l.fiscal_period = a.fiscal_period AND l.metric = 'total_liabilities'
        JOIN facts e ON e.ticker = a.ticker AND e.fiscal_year = a.fiscal_year
                     AND e.fiscal_period = a.fiscal_period AND e.metric = 'total_equity'
        LEFT JOIN facts m ON m.ticker = a.ticker AND m.fiscal_year = a.fiscal_year
                          AND m.fiscal_period = a.fiscal_period AND m.metric = 'mezzanine_equity'
        WHERE a.metric = 'total_assets' AND NOT l.derived
          AND abs(a.value - (l.value + e.value + COALESCE(m.value, 0))) / a.value > ?
        ORDER BY deviation DESC
        """,
        [tolerance],
    )


def render_coverage_markdown(
    matrix: pd.DataFrame, identity_violations: pd.DataFrame, identity_checked: int
) -> str:
    """Human-readable report committed to docs/coverage.md as evidence of validation."""
    by_ticker = coverage_by_ticker(matrix)
    lines = [
        "# Data coverage report",
        "",
        "Generated by `finsight coverage --write docs/coverage.md` from the DuckDB fact store.",
        "Cells are (company x metric x fiscal year) for annual (FY) periods.",
        "`n/a` = the metric does not exist for that sector (e.g. banks have no gross profit).",
        "`not reported` = the company does not disclose it (reason in `configs/universe.yaml`).",
        "`missing` = applicable, unexplained and not found (this should always be empty).",
        "",
        f"* **Raw availability** (value present / every cell except sector-`n/a`): "
        f"{raw_availability(matrix):.1%}",
        f"* **Unexplained gaps**: {len(unexplained_gaps(matrix))} cells",
        f"* Coverage excluding explained non-disclosures: {coverage_rate(matrix):.1%}",
        "",
        "## Coverage by company",
        "",
        "| Ticker | Coverage |",
        "|---|---|",
        *[f"| {r.ticker} | {r.coverage:.1%} |" for r in by_ticker.itertuples()],
        "",
        "## Status counts",
        "",
        "| Status | Cells |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in matrix["status"].value_counts().items()],
        "",
    ]
    stale = stale_known_gaps(matrix)
    if not stale.empty:
        lines += ["## Stale known-gap declarations (data exists; remove the explanation)", ""]
        lines += [f"* {r.ticker} / {r.metric}" for r in stale.itertuples()]
        lines.append("")
    explained = matrix[matrix["status"] == NOT_REPORTED].drop_duplicates(["ticker", "metric"])
    if not explained.empty:
        lines += [
            "## Explained gaps (declared in configs/universe.yaml)",
            "",
            "| Ticker | Metric | Reason |",
            "|---|---|---|",
        ]
        lines += [f"| {r.ticker} | {r.metric} | {r.note} |" for r in explained.itertuples()]
        lines.append("")
    missing = matrix[matrix["status"] == MISSING]
    lines += ["## Unexplained gaps (applicable, not declared, not found)", ""]
    if missing.empty:
        lines += ["None.", ""]
    else:
        grouped = missing.groupby(["ticker", "metric"])["fiscal_year"].apply(
            lambda s: ", ".join(str(int(y)) for y in sorted(s))
        )
        lines += ["| Ticker | Metric | Fiscal years |", "|---|---|---|"]
        lines += [f"| {t} | {m} | {ys} |" for (t, m), ys in grouped.items()]
        lines.append("")
    lines += ["## Accounting identity (Assets = Liabilities + Equity, tolerance 1%)", ""]
    lines += [
        f"Periods checked (reported liabilities and total equity present): {identity_checked}",
        "",
    ]
    if identity_violations.empty:
        lines += ["No violations among reported liabilities.", ""]
    else:
        lines += [
            "| Ticker | FY | Period | Assets | Liabilities | Equity | Mezzanine | Deviation |",
            "|---|---|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {r.ticker} | {r.fiscal_year} | {r.fiscal_period} | {r.assets:,.0f} | "
            f"{r.liabilities:,.0f} | {r.equity:,.0f} | {r.mezzanine:,.0f} | {r.deviation:.2%} |"
            for r in identity_violations.itertuples()
        ]
        lines += [
            "",
            "Listed for human review, not silently ignored.",
            "",
        ]
    return "\n".join(lines)
