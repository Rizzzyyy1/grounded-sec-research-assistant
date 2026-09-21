"""Company-facts parser: SEC XBRL JSON -> clean, fiscally-aligned :class:`FinancialFact` rows.

Pipeline (each step is a pure function, tested on its own)::

    extract_raw_facts   only 10-K/10-Q rows for tags/units we care about
        -> latest_versions   restatements: latest *filed* value wins per (tag, period)
        -> resolve_metrics   per period, the highest-priority tag wins (tag drift)
        -> normalize         fiscal year/period from dates, drop YTD, derive Q4 and gaps

Everything that SEC labels (``fy``, ``fp``, ``frame``) is ignored on purpose: the same annual
value is re-reported in later filings under a *different* ``fy`` and quarterly values are tagged
``fp=FY`` inside 10-Ks (see docs/DATA.md, trap #1). Periods are classified from their dates and
lengths, and fiscal labels come from ``core.fiscal``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from finsight.core.fiscal import fiscal_quarter_for, fiscal_year_for
from finsight.core.schemas import FinancialFact, FiscalPeriod, FormType
from finsight.ingestion.xbrl.concepts import METRICS, MetricSpec

_FORMS = {"10-K": FormType.TEN_K, "10-Q": FormType.TEN_Q}

# Period-length windows in days (52/53-week years and month-length variation need slack).
_ANNUAL = range(350, 381)
_QUARTER = range(80, 101)
_NINE_MONTH = range(260, 286)


@dataclass(frozen=True)
class RawFact:
    """One reported value exactly as filed; the unit of restatement history."""

    metric: str
    tag: str
    unit: str
    start: date | None
    end: date
    value: float
    form: str
    filed: date
    accession: str


@dataclass(frozen=True)
class ParsedFacts:
    facts: list[FinancialFact]  # normalised, one per (metric, unit, fiscal period)
    versions: list[RawFact]  # every reported version, kept for "as originally reported"


# --------------------------------------------------------------------------- step 1
def extract_raw_facts(
    payload: Mapping[str, Any], specs: Sequence[MetricSpec] = METRICS
) -> list[RawFact]:
    us_gaap: Mapping[str, Any] = payload.get("facts", {}).get("us-gaap", {})
    out: list[RawFact] = []
    for spec in specs:
        for tag in spec.tags:
            rows = us_gaap.get(tag, {}).get("units", {}).get(spec.unit, [])
            for row in rows:
                if row.get("form") not in _FORMS:
                    continue
                start = date.fromisoformat(row["start"]) if row.get("start") else None
                if (start is None) != (spec.period_type == "instant"):
                    continue  # wrong shape for the metric (duration on a balance item)
                out.append(
                    RawFact(
                        metric=spec.name,
                        tag=tag,
                        unit=spec.unit,
                        start=start,
                        end=date.fromisoformat(row["end"]),
                        value=float(row["val"]),
                        form=row["form"],
                        filed=date.fromisoformat(row["filed"]),
                        accession=row["accn"],
                    )
                )
    return out


# --------------------------------------------------------------------------- step 2
def latest_versions(raw: Iterable[RawFact]) -> list[RawFact]:
    """Restatements: for each (metric, tag, unit, period) keep the most recently filed value."""
    best: dict[tuple[str, str, str, date | None, date], RawFact] = {}
    for fact in raw:
        key = (fact.metric, fact.tag, fact.unit, fact.start, fact.end)
        current = best.get(key)
        if current is None or (fact.filed, fact.accession) > (current.filed, current.accession):
            best[key] = fact
    return list(best.values())


# --------------------------------------------------------------------------- step 3
def resolve_metrics(
    latest: Iterable[RawFact], specs: Sequence[MetricSpec] = METRICS
) -> list[RawFact]:
    """Per (metric, period) keep the value from the highest-priority tag that has one."""
    priority = {(s.name, tag): i for s in specs for i, tag in enumerate(s.tags)}
    best: dict[tuple[str, str, date | None, date], RawFact] = {}
    for fact in latest:
        key = (fact.metric, fact.unit, fact.start, fact.end)
        current = best.get(key)
        if (
            current is None
            or priority[(fact.metric, fact.tag)] < priority[(current.metric, current.tag)]
        ):
            best[key] = fact
    return list(best.values())


# --------------------------------------------------------------------------- step 4
def _classify(fact: RawFact, fye: str) -> tuple[int, FiscalPeriod] | None:
    """Fiscal (year, period) for a fact, or None when it is a YTD / odd-length duration."""
    year = fiscal_year_for(fact.end, fye)
    quarter = fiscal_quarter_for(fact.end, fye)
    if fact.start is None:  # instant: a balance at fiscal year end is the FY balance
        return year, FiscalPeriod.FY if quarter == 4 else FiscalPeriod(f"Q{quarter}")
    days = (fact.end - fact.start).days
    if days in _ANNUAL:
        return year, FiscalPeriod.FY
    if days in _QUARTER:
        return year, FiscalPeriod(f"Q{quarter}")
    return None


def _to_fact(
    raw: RawFact, *, ticker: str, cik: str, year: int, period: FiscalPeriod
) -> FinancialFact:
    return FinancialFact(
        ticker=ticker,
        cik=cik,
        metric=raw.metric,
        tag=raw.tag,
        value=raw.value,
        unit=raw.unit,
        period_type="instant" if raw.start is None else "duration",
        start=raw.start,
        end=raw.end,
        fiscal_year=year,
        fiscal_period=period,
        form=_FORMS[raw.form],
        filed=raw.filed,
        accession=raw.accession,
    )


def _derive_q4(
    resolved: Iterable[RawFact],
    facts: dict[tuple[str, str, int, FiscalPeriod], FinancialFact],
    specs: Mapping[str, MetricSpec],
    *,
    ticker: str,
    cik: str,
    fye: str,
) -> None:
    """Q4 is never reported as such: Q4 = FY - 9M YTD (additive USD flow metrics only)."""
    nine_month: dict[tuple[str, date | None, int], RawFact] = {}
    for raw in resolved:
        if raw.start is not None and (raw.end - raw.start).days in _NINE_MONTH:
            nine_month[(raw.metric, raw.start, fiscal_year_for(raw.end, fye))] = raw

    for (metric, unit, year, period), annual in list(facts.items()):
        spec = specs[metric]
        q4_key = (metric, unit, year, FiscalPeriod.Q4)
        if period is not FiscalPeriod.FY or not spec.additive or q4_key in facts:
            continue
        nine = nine_month.get((metric, annual.start, year))
        if nine is None or annual.start is None:
            continue
        facts[q4_key] = FinancialFact(
            ticker=ticker,
            cik=cik,
            metric=metric,
            tag=annual.tag,
            value=annual.value - nine.value,
            unit=unit,
            period_type="duration",
            start=nine.end + timedelta(days=1),
            end=annual.end,
            fiscal_year=year,
            fiscal_period=FiscalPeriod.Q4,
            form=annual.form,
            filed=annual.filed,
            accession=annual.accession,
            derived=True,
        )


def _derive_missing(facts: dict[tuple[str, str, int, FiscalPeriod], FinancialFact]) -> None:
    """Fill gross profit and total liabilities when the filer did not report them."""
    periods = {(f.fiscal_year, f.fiscal_period) for f in facts.values()}
    for year, period in sorted(periods, key=lambda p: (p[0], p[1].value)):

        def get(metric: str) -> FinancialFact | None:
            return facts.get((metric, "USD", year, period))  # noqa: B023

        revenue, cost, gross = get("revenue"), get("cost_of_revenue"), get("gross_profit")
        if (
            gross is None
            and revenue
            and cost
            and revenue.start == cost.start
            and revenue.end == cost.end
        ):
            facts[("gross_profit", "USD", year, period)] = revenue.model_copy(
                update={
                    "metric": "gross_profit",
                    "tag": "derived:revenue-cost_of_revenue",
                    "value": revenue.value - cost.value,
                    "derived": True,
                }
            )
        assets, equity, liabilities = (
            get("total_assets"),
            get("total_equity"),
            get("total_liabilities"),
        )
        if liabilities is None and assets and equity and assets.end == equity.end:
            mezzanine = get("mezzanine_equity")  # redeemable NCI: neither liability nor equity
            facts[("total_liabilities", "USD", year, period)] = assets.model_copy(
                update={
                    "metric": "total_liabilities",
                    "tag": "derived:assets-equity",
                    "value": assets.value - equity.value - (mezzanine.value if mezzanine else 0.0),
                    "derived": True,
                }
            )


def normalize(
    resolved: Sequence[RawFact],
    *,
    ticker: str,
    cik: str,
    fiscal_year_end: str,
    min_fiscal_year: int | None = None,
    specs: Sequence[MetricSpec] = METRICS,
) -> list[FinancialFact]:
    spec_by_name = {s.name: s for s in specs}
    facts: dict[tuple[str, str, int, FiscalPeriod], FinancialFact] = {}
    for raw in resolved:
        classified = _classify(raw, fiscal_year_end)
        if classified is None:
            continue
        year, period = classified
        if min_fiscal_year is not None and year < min_fiscal_year:
            continue
        key = (raw.metric, raw.unit, year, period)
        existing = facts.get(key)
        # Two facts can land on one fiscal period (e.g. an odd 53-week quirk): latest filing wins.
        if existing is None or (raw.filed, raw.end) > (existing.filed, existing.end):
            facts[key] = _to_fact(raw, ticker=ticker, cik=cik, year=year, period=period)

    _derive_q4(resolved, facts, spec_by_name, ticker=ticker, cik=cik, fye=fiscal_year_end)
    if min_fiscal_year is not None:
        facts = {k: v for k, v in facts.items() if v.fiscal_year >= min_fiscal_year}
    _derive_missing(facts)
    return sorted(
        facts.values(),
        key=lambda f: (f.metric, f.fiscal_year, f.fiscal_period.value, f.unit),
    )


# --------------------------------------------------------------------------- entry point
def parse_company_facts(
    payload: Mapping[str, Any],
    *,
    ticker: str,
    fiscal_year_end: str,
    min_fiscal_year: int | None = None,
    specs: Sequence[MetricSpec] = METRICS,
) -> ParsedFacts:
    cik = f"{int(payload['cik']):010d}"
    versions = extract_raw_facts(payload, specs)
    resolved = resolve_metrics(latest_versions(versions), specs)
    facts = normalize(
        resolved,
        ticker=ticker.upper(),
        cik=cik,
        fiscal_year_end=fiscal_year_end,
        min_fiscal_year=min_fiscal_year,
        specs=specs,
    )
    return ParsedFacts(facts=facts, versions=versions)


def group_by_metric(facts: Iterable[FinancialFact]) -> dict[str, list[FinancialFact]]:
    grouped: dict[str, list[FinancialFact]] = defaultdict(list)
    for fact in facts:
        grouped[fact.metric].append(fact)
    return dict(grouped)
