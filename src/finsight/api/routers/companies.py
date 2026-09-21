"""Universe, financial statements, ratio series and passage lookup (for citations)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from finsight.analytics.ratios import RATIOS, RatioError, compute_ratio, format_value
from finsight.api.deps import Services, get_services
from finsight.api.schemas import (
    CompanyOut,
    FinancialsResponse,
    MetricPoint,
    PassageOut,
    RatioPoint,
    RatioSeries,
    RatiosResponse,
)
from finsight.core.schemas import FiscalPeriod
from finsight.ingestion.xbrl.concepts import CANONICAL_METRICS

router = APIRouter(prefix="/v1", tags=["companies"])
ServicesDep = Annotated[Services, Depends(get_services)]


def _company(services: Services, ticker: str) -> str:
    try:
        return services.ctx.universe.company(ticker).ticker
    except KeyError:
        raise HTTPException(404, f"{ticker.upper()} is not in the covered universe") from None


def _years(spec: str | None, default: tuple[int, ...]) -> list[int]:
    if not spec:
        return list(default)
    try:
        if "-" in spec:
            lo, hi = (int(x) for x in spec.split("-", 1))
            return list(range(lo, hi + 1))
        return [int(x) for x in spec.split(",")]
    except ValueError:
        raise HTTPException(422, "years must look like '2021-2025' or '2022,2024'") from None


@router.get("/companies", response_model=list[CompanyOut], summary="Covered companies")
def companies(services: ServicesDep) -> list[CompanyOut]:
    return [
        CompanyOut(ticker=c.ticker, name=c.name, sector=c.sector, fiscal_year_end=c.fiscal_year_end,
                   known_gaps=c.known_gaps)
        for c in services.ctx.universe.companies
    ]  # fmt: skip


@router.get("/companies/{ticker}/financials", response_model=FinancialsResponse,
            summary="Reported XBRL series for one or more metrics")  # fmt: skip
def financials(
    ticker: str,
    services: ServicesDep,
    metrics: Annotated[
        str, Query(description="Comma-separated canonical metrics")
    ] = "revenue,net_income",
    years: Annotated[str | None, Query(description="'2021-2025' or '2022,2024'")] = None,
    period: FiscalPeriod = FiscalPeriod.FY,
) -> FinancialsResponse:
    t = _company(services, ticker)
    wanted = _years(years, services.ctx.universe.fiscal_years)
    out: dict[str, list[MetricPoint]] = {}
    for metric in (m.strip() for m in metrics.split(",") if m.strip()):
        if metric not in CANONICAL_METRICS:
            raise HTTPException(422, f"unknown metric {metric!r}")
        with services.ctx.db_lock:
            df = services.ctx.facts.get_metric(t, metric, period=period, years=wanted)
        out[metric] = [
            MetricPoint(fiscal_year=int(r.fiscal_year), fiscal_period=r.fiscal_period, value=float(r.value),
                        unit=r.unit, period_end=str(r.end_date)[:10], xbrl_tag=r.tag, derived=bool(r.derived))
            for r in df.itertuples()
        ]  # fmt: skip
    return FinancialsResponse(ticker=t, metrics=out)


@router.get("/companies/{ticker}/ratios", response_model=RatiosResponse,
            summary="Computed ratio series with formulas")  # fmt: skip
def ratios(
    ticker: str,
    services: ServicesDep,
    names: Annotated[
        str, Query(description="Comma-separated ratio names")
    ] = "gross_margin,operating_margin,net_margin,roe",
    years: Annotated[str | None, Query()] = None,
) -> RatiosResponse:
    t = _company(services, ticker)
    wanted = _years(years, services.ctx.universe.fiscal_years)
    out: dict[str, RatioSeries] = {}
    for name in (n.strip() for n in names.split(",") if n.strip()):
        spec = RATIOS.get(name)
        if spec is None:
            raise HTTPException(422, f"unknown ratio {name!r}")
        points: list[RatioPoint] = []
        skipped: dict[int, str] = {}
        for year in wanted:
            try:
                with services.ctx.db_lock:
                    cur = {m: services.ctx.facts.get_fact(t, m, year) for m in spec.inputs}
                    prior = {
                        m: services.ctx.facts.get_fact(t, m, year - 1) for m in spec.prior_inputs
                    }
                absent = [
                    m
                    for m, f in {
                        **cur,
                        **{f"{k} (prior year)": v for k, v in prior.items()},
                    }.items()
                    if f is None
                ]
                if absent:
                    raise RatioError(f"missing {', '.join(absent)}")
                value = compute_ratio(name, {m: f.value for m, f in cur.items() if f},
                                      {m: f.value for m, f in prior.items() if f})  # fmt: skip
                points.append(
                    RatioPoint(
                        fiscal_year=year, value=value, formatted=format_value(value, spec.kind)
                    )
                )
            except RatioError as exc:
                skipped[year] = str(exc)
        out[name] = RatioSeries(
            label=spec.label, formula=spec.formula, points=points, skipped=skipped
        )
    return RatiosResponse(ticker=t, ratios=out)


@router.get(
    "/passages/{chunk_id}",
    response_model=PassageOut,
    summary="The source passage behind a citation",
)
def passage(chunk_id: str, services: ServicesDep) -> PassageOut:
    chunk = services.ctx.retriever.catalogue.get(chunk_id)
    if chunk is None:
        raise HTTPException(404, "unknown passage id")
    m = chunk.metadata
    return PassageOut(chunk_id=chunk.id, ticker=m.ticker, form=m.form.value, fiscal_year=m.fiscal_year,
                      item=m.item, item_title=m.item_title, source_url=m.source_url, text=chunk.text)  # fmt: skip
