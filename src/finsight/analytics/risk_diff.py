"""Risk-factor change detection between two years of Item 1A.

Paragraphs of the newer filing are aligned to the most similar paragraph of the older one
(cosine similarity of term-frequency vectors, or of caller-supplied embeddings). By similarity:

* >= ``unchanged``  -> the same risk, carried forward;
* >= ``matched``    -> the same risk, materially reworded;
* otherwise         -> **added** (a new risk).
Old paragraphs that nothing in the new filing resembles are **removed**.

Pure Python by design: it works on plain strings so it is trivially testable and has no model
dependency; a semantic embedder can be injected for better handling of paraphrase.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

_WORD = re.compile(r"[a-z][a-z0-9'-]+")
_STOP = frozenset(
    {"the", "and", "our", "for", "that", "with", "this", "are", "from", "any", "may", "could", "would",
     "have", "has", "not", "which", "their", "such", "also", "other", "than", "into", "these", "those"}
)  # fmt: skip
Embed = Callable[[Sequence[str]], Sequence[Sequence[float]]]


def _terms(text: str) -> Counter[str]:
    return Counter(w for w in _WORD.findall(text.lower()) if w not in _STOP)


def _cosine_sparse(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(a[k] * b[k] for k in a.keys() & b.keys())
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _cosine_dense(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass(frozen=True)
class Change:
    text: str
    similarity: float  # to the best counterpart (0 for pure additions / removals)
    counterpart: str | None = None


@dataclass
class RiskDiff:
    added: list[Change] = field(default_factory=list)
    removed: list[Change] = field(default_factory=list)
    modified: list[Change] = field(default_factory=list)
    unchanged: int = 0

    @property
    def churn(self) -> float:
        """Share of the new filing's paragraphs that are added or materially reworded."""
        total = len(self.added) + len(self.modified) + self.unchanged
        return (len(self.added) + len(self.modified)) / total if total else 0.0


def diff_risk_factors(
    old: Sequence[str],
    new: Sequence[str],
    *,
    unchanged: float = 0.85,
    matched: float = 0.5,
    embed: Embed | None = None,
) -> RiskDiff:
    if not 0 < matched <= unchanged <= 1:
        raise ValueError("thresholds must satisfy 0 < matched <= unchanged <= 1")
    similarity: Callable[[int, int], float]  # (index in new, index in old) -> cosine
    if embed is None:
        terms_old, terms_new = [_terms(p) for p in old], [_terms(p) for p in new]

        def similarity(j: int, i: int) -> float:
            return _cosine_sparse(terms_new[j], terms_old[i])

    else:
        dense_old, dense_new = list(embed(old)), list(embed(new))

        def similarity(j: int, i: int) -> float:
            return _cosine_dense(dense_new[j], dense_old[i])

    result = RiskDiff()
    matched_old: set[int] = set()
    for j, paragraph in enumerate(new):
        sims = [similarity(j, i) for i in range(len(old))]
        best = max(range(len(old)), key=sims.__getitem__) if old else None
        score = sims[best] if best is not None else 0.0
        if best is not None and score >= matched:
            matched_old.add(best)
        if score >= unchanged:
            result.unchanged += 1
        elif score >= matched and best is not None:
            result.modified.append(Change(paragraph, score, old[best]))
        else:
            result.added.append(Change(paragraph, score))
    result.removed = [Change(p, 0.0) for i, p in enumerate(old) if i not in matched_old]
    return result
