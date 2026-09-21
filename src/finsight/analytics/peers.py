"""Peer comparison: ranks, percentiles and distance from the group median."""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class PeerRow:
    ticker: str
    value: float
    rank: int  # 1 = highest
    percentile: float  # share of the *other* peers this one beats, 0-100
    vs_median: float  # value - group median


def percentile_rank(value: float, peers: list[float]) -> float:
    """Percent of other peers strictly below ``value`` (ties count half)."""
    others = list(peers)
    if value in others:
        others.remove(value)
    if not others:
        return 100.0
    below = sum(p < value for p in others) + 0.5 * sum(p == value for p in others)
    return 100.0 * below / len(others)


def peer_table(values: Mapping[str, float]) -> list[PeerRow]:
    """Rank a group best-first. Higher counts as better (invert cost-like metrics first)."""
    if not values:
        return []
    median = statistics.median(values.values())
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    all_values = list(values.values())
    return [
        PeerRow(ticker, v, rank, percentile_rank(v, all_values), v - median)
        for rank, (ticker, v) in enumerate(ordered, start=1)
    ]
