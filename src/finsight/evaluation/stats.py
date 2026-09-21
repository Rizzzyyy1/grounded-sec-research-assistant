"""Statistics: bootstrap confidence intervals, paired comparisons, McNemar, Cohen's kappa.

With ~150 gold questions (fewer per type), a bare mean is not evidence of anything. Every headline
number is reported with a percentile-bootstrap 95% CI, and two systems are compared on the *same*
questions with a paired bootstrap of the difference - so a claim like "reranking helps" is made
only when the interval on the difference excludes zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Interval:
    mean: float
    lo: float
    hi: float

    def excludes_zero(self) -> bool:
        return self.lo > 0 or self.hi < 0

    def __str__(self) -> str:
        return f"{self.mean:.3f} [{self.lo:.3f}, {self.hi:.3f}]"


def bootstrap_ci(
    values: Sequence[float], *, n_boot: int = 10_000, alpha: float = 0.05, seed: int = 0
) -> Interval:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return Interval(float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(arr, size=(n_boot, arr.size), replace=True).mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return Interval(float(arr.mean()), float(lo), float(hi))


def paired_bootstrap_diff(
    a: Sequence[float],
    b: Sequence[float],
    *,
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Interval:
    """CI for mean(a - b) over the same questions (positive = a is better)."""
    if len(a) != len(b):
        raise ValueError("paired samples must have equal length")
    return bootstrap_ci(np.asarray(a, dtype=float) - np.asarray(b, dtype=float),
                        n_boot=n_boot, alpha=alpha, seed=seed)  # fmt: skip


def mcnemar_exact(only_a_correct: int, only_b_correct: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts."""
    n = only_a_correct + only_b_correct
    if n == 0:
        return 1.0
    k = min(only_a_correct, only_b_correct)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return float(min(1.0, 2 * tail))


def cohens_kappa(a: Sequence[object], b: Sequence[object]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty label sequences")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    labels = set(a) | set(b)
    expected = sum((list(a).count(lbl) / n) * (list(b).count(lbl) / n) for lbl in labels)
    return 1.0 if expected == 1 else float((observed - expected) / (1 - expected))
