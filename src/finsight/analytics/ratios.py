"""Financial ratios: pure, deterministic and self-documenting.

Every ratio is registered with its formula and required inputs, so a tool (or a reader) can show
*exactly* how a number was produced. Definitions are explicit about the balance-sheet basis:
year-end by default (matching how the question is phrased in the gold set), with ``*_avg``
variants that use the average of opening and closing balances, the more standard analyst basis.

Sign convention: flows are as reported; capex is a positive outflow, so free cash flow is
``operating_cash_flow - capex``. Division by zero raises :class:`RatioError` instead of returning
``inf``/``nan``, so a bad input can never surface as a plausible-looking number.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

DAYS = 365.0


class RatioError(ValueError):
    """The ratio cannot be computed from the given inputs."""


def safe_div(numerator: float, denominator: float, what: str = "ratio") -> float:
    if denominator == 0:
        raise RatioError(f"{what}: denominator is zero")
    return numerator / denominator


def average(current: float, prior: float) -> float:
    return (current + prior) / 2.0


def yoy(current: float, prior: float) -> float:
    """Year-over-year change as a fraction (0.05 = +5%). Undefined for a zero base."""
    return safe_div(current - prior, abs(prior), "year-over-year change")


def cagr(start: float, end: float, years: float) -> float:
    """Compound annual growth rate. Requires positive endpoints (a sign flip has no CAGR)."""
    if years <= 0:
        raise RatioError("cagr: years must be positive")
    if start <= 0 or end <= 0:
        raise RatioError("cagr: start and end values must both be positive")
    return float((end / start) ** (1.0 / years) - 1.0)


@dataclass(frozen=True)
class RatioSpec:
    name: str
    label: str
    formula: str
    inputs: tuple[str, ...]  # canonical metric names needed for the fiscal year
    prior_inputs: tuple[str, ...]  # canonical metrics needed for the *prior* year (averages)
    compute: Callable[[Mapping[str, float], Mapping[str, float]], float]
    kind: str = "ratio"  # "ratio" is shown as a percentage, "multiple" as x, "days" as days


def _r(
    name: str, label: str, formula: str, inputs: tuple[str, ...],
    fn: Callable[[Mapping[str, float], Mapping[str, float]], float],
    *, prior: tuple[str, ...] = (), kind: str = "ratio",
) -> RatioSpec:  # fmt: skip
    return RatioSpec(name, label, formula, inputs, prior, fn, kind)


RATIOS: dict[str, RatioSpec] = {
    s.name: s
    for s in (
        _r("gross_margin", "Gross margin", "gross_profit / revenue", ("gross_profit", "revenue"),
           lambda c, p: safe_div(c["gross_profit"], c["revenue"], "gross margin")),
        _r("operating_margin", "Operating margin", "operating_income / revenue",
           ("operating_income", "revenue"),
           lambda c, p: safe_div(c["operating_income"], c["revenue"], "operating margin")),
        _r("net_margin", "Net margin", "net_income / revenue", ("net_income", "revenue"),
           lambda c, p: safe_div(c["net_income"], c["revenue"], "net margin")),
        _r("roe", "Return on equity", "net_income / shareholders_equity (year-end)",
           ("net_income", "shareholders_equity"),
           lambda c, p: safe_div(c["net_income"], c["shareholders_equity"], "ROE")),
        _r("roe_avg", "Return on equity (average equity)",
           "net_income / average(shareholders_equity, prior year)",
           ("net_income", "shareholders_equity"),
           lambda c, p: safe_div(c["net_income"], average(c["shareholders_equity"], p["shareholders_equity"]), "ROE"),
           prior=("shareholders_equity",)),
        _r("roa", "Return on assets", "net_income / total_assets (year-end)",
           ("net_income", "total_assets"),
           lambda c, p: safe_div(c["net_income"], c["total_assets"], "ROA")),
        _r("roa_avg", "Return on assets (average assets)",
           "net_income / average(total_assets, prior year)", ("net_income", "total_assets"),
           lambda c, p: safe_div(c["net_income"], average(c["total_assets"], p["total_assets"]), "ROA"),
           prior=("total_assets",)),
        _r("current_ratio", "Current ratio", "current_assets / current_liabilities",
           ("current_assets", "current_liabilities"),
           lambda c, p: safe_div(c["current_assets"], c["current_liabilities"], "current ratio"),
           kind="multiple"),
        _r("quick_ratio", "Quick ratio", "(current_assets - inventory) / current_liabilities",
           ("current_assets", "inventory", "current_liabilities"),
           lambda c, p: safe_div(c["current_assets"] - c["inventory"], c["current_liabilities"], "quick ratio"),
           kind="multiple"),
        _r("debt_to_equity", "Debt to equity", "long_term_debt / shareholders_equity",
           ("long_term_debt", "shareholders_equity"),
           lambda c, p: safe_div(c["long_term_debt"], c["shareholders_equity"], "debt to equity"),
           kind="multiple"),
        _r("interest_coverage", "Interest coverage", "operating_income / interest_expense",
           ("operating_income", "interest_expense"),
           lambda c, p: safe_div(c["operating_income"], c["interest_expense"], "interest coverage"),
           kind="multiple"),
        _r("asset_turnover", "Asset turnover", "revenue / average(total_assets, prior year)",
           ("revenue", "total_assets"),
           lambda c, p: safe_div(c["revenue"], average(c["total_assets"], p["total_assets"]), "asset turnover"),
           prior=("total_assets",), kind="multiple"),
        _r("free_cash_flow", "Free cash flow", "operating_cash_flow - capex",
           ("operating_cash_flow", "capex"),
           lambda c, p: c["operating_cash_flow"] - c["capex"], kind="usd"),
        _r("fcf_margin", "Free cash flow margin", "(operating_cash_flow - capex) / revenue",
           ("operating_cash_flow", "capex", "revenue"),
           lambda c, p: safe_div(c["operating_cash_flow"] - c["capex"], c["revenue"], "FCF margin")),
        _r("effective_tax_rate", "Effective tax rate", "income_tax_expense / pretax_income",
           ("income_tax_expense", "pretax_income"),
           lambda c, p: safe_div(c["income_tax_expense"], c["pretax_income"], "effective tax rate")),
        _r("days_sales_outstanding", "Days sales outstanding", "accounts_receivable / revenue x 365",
           ("accounts_receivable", "revenue"),
           lambda c, p: safe_div(c["accounts_receivable"], c["revenue"], "DSO") * DAYS, kind="days"),
        _r("days_inventory", "Days inventory outstanding", "inventory / cost_of_revenue x 365",
           ("inventory", "cost_of_revenue"),
           lambda c, p: safe_div(c["inventory"], c["cost_of_revenue"], "DIO") * DAYS, kind="days"),
        _r("days_payable", "Days payables outstanding", "accounts_payable / cost_of_revenue x 365",
           ("accounts_payable", "cost_of_revenue"),
           lambda c, p: safe_div(c["accounts_payable"], c["cost_of_revenue"], "DPO") * DAYS, kind="days"),
    )
}  # fmt: skip


def compute_ratio(
    name: str, current: Mapping[str, float], prior: Mapping[str, float] | None = None
) -> float:
    spec = RATIOS.get(name)
    if spec is None:
        raise RatioError(f"unknown ratio {name!r}; known: {', '.join(sorted(RATIOS))}")
    missing = [m for m in spec.inputs if m not in current]
    missing += [f"{m} (prior year)" for m in spec.prior_inputs if m not in (prior or {})]
    if missing:
        raise RatioError(f"{name} needs {', '.join(missing)}")
    return spec.compute(current, prior or {})


def format_value(value: float, kind: str) -> str:
    if kind == "ratio":
        return f"{value * 100:.1f}%"
    if kind == "multiple":
        return f"{value:.2f}x"
    if kind == "days":
        return f"{value:.1f} days"
    return f"${value:,.0f}"
