"""Retrieval metrics at *section* level (no LLM needed).

A retrieved chunk is relevant when it belongs to a gold section: same ticker, fiscal year and Item
(a gold source with ``item=None`` accepts any section of that filing). Section level is the
headline rather than chunk id, because chunk boundaries are an implementation detail - scoring
chunkers by chunk-id recall would be circular.

Metrics: hit@k, recall@k (share of gold *sources* found), MRR, nDCG@k with binary gains.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from finsight.core.schemas import Chunk
from finsight.evaluation.datasets import GoldSource

DEFAULT_KS = (1, 3, 5, 8)


def matches_source(chunk: Chunk, source: GoldSource) -> bool:
    m = chunk.metadata
    return (
        m.ticker == source.ticker
        and m.fiscal_year == source.fiscal_year
        and (source.item is None or m.item == source.item)
    )


def is_relevant(chunk: Chunk, sources: Sequence[GoldSource]) -> bool:
    return any(matches_source(chunk, s) for s in sources)


@dataclass(frozen=True)
class RetrievalScores:
    hit: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
    ndcg: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0

    def flat(self) -> dict[str, float]:
        out: dict[str, float] = {"mrr": self.mrr}
        for name, values in (("hit", self.hit), ("recall", self.recall), ("ndcg", self.ndcg)):
            out.update({f"{name}@{k}": v for k, v in values.items()})
        return out


def score_ranking(
    ranked: Sequence[Chunk], sources: Sequence[GoldSource], ks: Sequence[int] = DEFAULT_KS
) -> RetrievalScores:
    if not sources:
        raise ValueError("a retrieval example needs at least one gold source")
    relevance = [1.0 if is_relevant(c, sources) else 0.0 for c in ranked]
    first = next((i for i, r in enumerate(relevance, start=1) if r), None)
    hit: dict[int, float] = {}
    recall: dict[int, float] = {}
    ndcg: dict[int, float] = {}
    for k in ks:
        top = ranked[:k]
        hit[k] = float(any(relevance[:k]))
        found = sum(any(matches_source(c, s) for c in top) for s in sources)
        recall[k] = found / len(sources)
        dcg = sum(r / math.log2(i + 1) for i, r in enumerate(relevance[:k], start=1))
        ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, max(len(sources), 1) * k) + 1))
        ndcg[k] = dcg / ideal if ideal else 0.0
    return RetrievalScores(hit, recall, ndcg, 1.0 / first if first else 0.0)
