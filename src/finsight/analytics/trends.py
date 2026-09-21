"""Trend analysis over annual series: growth, rolling statistics, anomaly flags, slope."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from itertools import pairwise

from finsight.analytics.ratios import RatioError, cagr, yoy


def growth_series(values: Mapping[int, float]) -> dict[int, float]:
    """Year-over-year growth for every year that has a positive-or-negative, non-zero prior year."""
    years = sorted(values)
    return {
        b: yoy(values[b], values[a]) for a, b in pairwise(years) if b == a + 1 and values[a] != 0
    }


def series_cagr(values: Mapping[int, float]) -> float:
    years = sorted(values)
    if len(years) < 2:
        raise RatioError("need at least two years for a CAGR")
    return cagr(values[years[0]], values[years[-1]], years[-1] - years[0])


def rolling_mean(values: Sequence[float], window: int) -> list[float]:
    if window < 1:
        raise ValueError("window must be >= 1")
    return [sum(values[i - window + 1 : i + 1]) / window for i in range(window - 1, len(values))]


def zscores(values: Sequence[float]) -> list[float]:
    if len(values) < 2:
        return [0.0] * len(values)
    sd = statistics.pstdev(values)
    mu = statistics.fmean(values)
    return [0.0 if sd == 0 else (v - mu) / sd for v in values]


def flag_anomalies(values: Mapping[int, float], *, threshold: float = 2.0) -> dict[int, float]:
    """Years whose value is more than ``threshold`` standard deviations from the series mean."""
    years = sorted(values)
    return {
        y: z for y, z in zip(years, zscores([values[y] for y in years]), strict=True)
        if abs(z) > threshold
    }  # fmt: skip


def linear_slope(values: Mapping[int, float]) -> float:
    """Least-squares slope in units per year."""
    years = sorted(values)
    if len(years) < 2:
        raise RatioError("need at least two points for a slope")
    xs, ys = [float(y) for y in years], [values[y] for y in years]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / denom
    return slope if not math.isnan(slope) else 0.0
