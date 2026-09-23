"""DuckDB store: normalised facts, restatement history and the filing catalogue.

DuckDB gives us analytical SQL (window functions for YoY / CAGR) with zero infrastructure.

Tables
------
``facts``          one row per (ticker, metric, unit, fiscal_year, fiscal_period) - enforced by the
                   primary key, so a normalisation bug that produced duplicates fails loudly.
``fact_versions``  every reported version of every value ("as originally reported").
``filings``        catalogue of downloaded 10-K/10-Q documents (path, sha256, size).
``companies``      universe metadata (sector, fiscal year end) for coverage and peer queries.

The store is a single-writer file: it is built by a batch job and read by everything else.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from finsight.core.schemas import FilingRef, FinancialFact, FiscalPeriod
from finsight.ingestion.xbrl.facts import ParsedFacts, RawFact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    ticker VARCHAR NOT NULL, cik VARCHAR NOT NULL, metric VARCHAR NOT NULL,
    tag VARCHAR NOT NULL, value DOUBLE NOT NULL, unit VARCHAR NOT NULL,
    period_type VARCHAR NOT NULL, start_date DATE, end_date DATE NOT NULL,
    fiscal_year INTEGER NOT NULL, fiscal_period VARCHAR NOT NULL,
    form VARCHAR NOT NULL, filed DATE NOT NULL, accession VARCHAR NOT NULL,
    derived BOOLEAN NOT NULL,
    PRIMARY KEY (ticker, metric, unit, fiscal_year, fiscal_period)
);
CREATE TABLE IF NOT EXISTS fact_versions (
    ticker VARCHAR NOT NULL, metric VARCHAR NOT NULL, tag VARCHAR NOT NULL,
    unit VARCHAR NOT NULL, start_date DATE, end_date DATE NOT NULL, value DOUBLE NOT NULL,
    form VARCHAR NOT NULL, filed DATE NOT NULL, accession VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS filings (
    ticker VARCHAR NOT NULL, cik VARCHAR NOT NULL, company VARCHAR NOT NULL,
    form VARCHAR NOT NULL, accession VARCHAR PRIMARY KEY, filed DATE NOT NULL,
    period_of_report DATE NOT NULL, fiscal_year INTEGER NOT NULL,
    fiscal_period VARCHAR NOT NULL, primary_doc VARCHAR NOT NULL, url VARCHAR NOT NULL,
    sha256 VARCHAR, size_bytes BIGINT, local_path VARCHAR
);
CREATE TABLE IF NOT EXISTS companies (
    ticker VARCHAR PRIMARY KEY, cik VARCHAR, name VARCHAR, sector VARCHAR,
    fiscal_year_end VARCHAR
);
"""

_FACT_COLS = (
    "ticker, cik, metric, tag, value, unit, period_type, start_date, end_date, fiscal_year, "
    "fiscal_period, form, filed, accession, derived"
)


class FactStore:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(_SCHEMA)

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> FactStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ writes
    def replace_company_facts(self, ticker: str, parsed: ParsedFacts) -> None:
        """Idempotent: everything for ``ticker`` is replaced atomically in one transaction."""
        ticker = ticker.upper()
        facts = pd.DataFrame(
            [_fact_row(f) for f in parsed.facts],
            columns=_FACT_COLUMNS,
        )
        versions = pd.DataFrame(
            [_version_row(ticker, v) for v in parsed.versions], columns=_VERSION_COLUMNS
        )
        self._con.execute("BEGIN")
        try:
            self._con.execute("DELETE FROM facts WHERE ticker = ?", [ticker])
            self._con.execute("DELETE FROM fact_versions WHERE ticker = ?", [ticker])
            if not facts.empty:
                self._con.register("_facts_df", facts)
                self._con.execute(_INSERT_FACTS)
                self._con.unregister("_facts_df")
            if not versions.empty:
                self._con.register("_versions_df", versions)
                self._con.execute("INSERT INTO fact_versions SELECT * FROM _versions_df")
                self._con.unregister("_versions_df")
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise

    def upsert_company(
        self, ticker: str, *, cik: str, name: str, sector: str, fiscal_year_end: str
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?, ?)",
            [ticker.upper(), cik, name, sector, fiscal_year_end],
        )

    def upsert_filing(
        self,
        ref: FilingRef,
        *,
        sha256: str | None = None,
        size_bytes: int | None = None,
        local_path: str | None = None,
    ) -> None:
        """Insert or update a catalogue row.

        Download metadata is *merged*: re-registering a filing without it (e.g. a facts-only
        ingestion run) must not erase the path and hash of a filing that is already on disk.
        """
        self._con.execute(
            """
            INSERT INTO filings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (accession) DO UPDATE SET
                ticker = excluded.ticker, cik = excluded.cik, company = excluded.company,
                form = excluded.form, filed = excluded.filed,
                period_of_report = excluded.period_of_report,
                fiscal_year = excluded.fiscal_year, fiscal_period = excluded.fiscal_period,
                primary_doc = excluded.primary_doc, url = excluded.url,
                sha256 = COALESCE(excluded.sha256, filings.sha256),
                size_bytes = COALESCE(excluded.size_bytes, filings.size_bytes),
                local_path = COALESCE(excluded.local_path, filings.local_path)
            """,
            [
                ref.ticker, ref.cik, ref.company, ref.form.value, ref.accession, ref.filed,
                ref.period_of_report, ref.fiscal_year, ref.fiscal_period.value, ref.primary_doc,
                ref.url, sha256, size_bytes, local_path,
            ],
        )  # fmt: skip

    # ------------------------------------------------------------------ reads
    def sql(self, query: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
        """Ad-hoc read-only analysis (notebooks, tools). Returns a pandas DataFrame."""
        return self._con.execute(query, list(params or [])).df()

    def get_metric(
        self,
        ticker: str,
        metric: str,
        *,
        period: FiscalPeriod = FiscalPeriod.FY,
        years: Iterable[int] | None = None,
    ) -> pd.DataFrame:
        """A metric's time series, oldest first."""
        query = "SELECT * FROM facts WHERE ticker = ? AND metric = ? AND fiscal_period = ?"
        params: list[Any] = [ticker.upper(), metric, period.value]
        if years is not None:
            ys = sorted(set(years))
            query += f" AND fiscal_year IN ({','.join('?' * len(ys))})"
            params += ys
        return self.sql(query + " ORDER BY fiscal_year", params)

    def get_fact(
        self, ticker: str, metric: str, fiscal_year: int, period: FiscalPeriod = FiscalPeriod.FY
    ) -> FinancialFact | None:
        df = self.get_metric(ticker, metric, period=period, years=[fiscal_year])
        if df.empty:
            return None
        r = df.iloc[0]
        return FinancialFact(
            ticker=r.ticker,
            cik=r.cik,
            metric=r.metric,
            tag=r.tag,
            value=float(r.value),
            unit=r.unit,
            period_type=r.period_type,
            start=None if pd.isna(r.start_date) else pd.Timestamp(r.start_date).date(),
            end=pd.Timestamp(r.end_date).date(),
            fiscal_year=int(r.fiscal_year),
            fiscal_period=FiscalPeriod(r.fiscal_period),
            form=r.form,
            filed=pd.Timestamp(r.filed).date(),
            accession=r.accession,
            derived=bool(r.derived),
        )

    def versions(self, ticker: str, metric: str, end: date) -> pd.DataFrame:
        """Every reported version of one period's value, oldest filing first."""
        return self.sql(
            "SELECT * FROM fact_versions WHERE ticker = ? AND metric = ? AND end_date = ? "
            "ORDER BY filed",
            [ticker.upper(), metric, end],
        )

    def filings(self) -> pd.DataFrame:
        """The filing catalogue, oldest first per company."""
        return self.sql("SELECT * FROM filings ORDER BY ticker, period_of_report")

    def filing_url(self, accession: str) -> str | None:
        """The primary document URL for one filing, or ``None`` if it was never catalogued."""
        row = self._con.execute(
            "SELECT url FROM filings WHERE accession = ?", [accession]
        ).fetchone()
        return row[0] if row else None

    def uncatalogued_fact_accessions(self, ticker: str) -> set[str]:
        """Selected facts whose exact source filing has no catalogue row."""
        rows = self._con.execute(
            "SELECT DISTINCT f.accession FROM facts f "
            "LEFT JOIN filings d ON f.accession = d.accession "
            "WHERE f.ticker = ? AND d.accession IS NULL",
            [ticker.upper()],
        ).fetchall()
        return {str(row[0]) for row in rows}

    def tickers(self) -> list[str]:
        return list(self.sql("SELECT DISTINCT ticker FROM facts ORDER BY 1")["ticker"])

    def count(self, table: str) -> int:
        if table not in {"facts", "fact_versions", "filings", "companies"}:
            raise ValueError(f"unknown table {table!r}")
        row = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608 - whitelisted
        return int(row[0]) if row else 0


_INSERT_FACTS = f"INSERT INTO facts SELECT {_FACT_COLS} FROM _facts_df"  # noqa: S608 - constants only

_FACT_COLUMNS = [
    "ticker",
    "cik",
    "metric",
    "tag",
    "value",
    "unit",
    "period_type",
    "start_date",
    "end_date",
    "fiscal_year",
    "fiscal_period",
    "form",
    "filed",
    "accession",
    "derived",
]
_VERSION_COLUMNS = [
    "ticker",
    "metric",
    "tag",
    "unit",
    "start_date",
    "end_date",
    "value",
    "form",
    "filed",
    "accession",
]


def _fact_row(f: FinancialFact) -> list[Any]:
    return [
        f.ticker,
        f.cik,
        f.metric,
        f.tag,
        f.value,
        f.unit,
        f.period_type,
        f.start,
        f.end,
        f.fiscal_year,
        f.fiscal_period.value,
        f.form.value,
        f.filed,
        f.accession,
        f.derived,
    ]


def _version_row(ticker: str, v: RawFact) -> list[Any]:
    return [ticker, v.metric, v.tag, v.unit, v.start, v.end, v.value, v.form, v.filed, v.accession]
