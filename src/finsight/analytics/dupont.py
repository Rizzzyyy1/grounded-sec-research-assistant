"""DuPont decomposition of ROE, with driver attribution of year-over-year change.

3-step:  ROE = net margin x asset turnover x equity multiplier
5-step:  ROE = tax burden x interest burden x EBIT margin x asset turnover x equity multiplier

Attribution answers the analyst's real question - *why* did ROE move? - by splitting the change
into per-factor contributions using log decomposition: because ROE is a product of factors,
``ln(ROE1/ROE0) = sum(ln(f1/f0))`` exactly, so contributions add up with no residual.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from finsight.analytics.ratios import RatioError, safe_div


@dataclass(frozen=True)
class DuPont:
    factors: dict[str, float]

    @property
    def roe(self) -> float:
        out = 1.0
        for v in self.factors.values():
            out *= v
        return out


def dupont_3(net_income: float, revenue: float, assets: float, equity: float) -> DuPont:
    return DuPont(
        {
            "net_margin": safe_div(net_income, revenue, "net margin"),
            "asset_turnover": safe_div(revenue, assets, "asset turnover"),
            "equity_multiplier": safe_div(assets, equity, "equity multiplier"),
        }
    )


def dupont_5(
    *,
    net_income: float,
    pretax_income: float,
    ebit: float,
    revenue: float,
    assets: float,
    equity: float,
) -> DuPont:
    return DuPont(
        {
            "tax_burden": safe_div(net_income, pretax_income, "tax burden"),
            "interest_burden": safe_div(pretax_income, ebit, "interest burden"),
            "ebit_margin": safe_div(ebit, revenue, "EBIT margin"),
            "asset_turnover": safe_div(revenue, assets, "asset turnover"),
            "equity_multiplier": safe_div(assets, equity, "equity multiplier"),
        }
    )


def attribute_change(prior: DuPont, current: DuPont) -> dict[str, float]:
    """Each factor's share of the ROE change, as log-contributions that sum to the total.

    Requires positive factors (a negative margin has no logarithm); callers should fall back to
    reporting the raw factor changes in that case.
    """
    if prior.factors.keys() != current.factors.keys():
        raise RatioError("cannot attribute between different decompositions")
    contributions: dict[str, float] = {}
    for name, before in prior.factors.items():
        after = current.factors[name]
        if before <= 0 or after <= 0:
            raise RatioError(f"log attribution needs positive factors ({name} is not)")
        contributions[name] = math.log(after / before)
    return contributions
