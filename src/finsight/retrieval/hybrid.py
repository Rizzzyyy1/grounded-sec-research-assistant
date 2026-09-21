"""Rank fusion for hybrid retrieval.

Reciprocal Rank Fusion scores a document by ``sum(weight / (k + rank))`` over the rankings it
appears in. It is rank-based, so it needs no calibration between cosine similarities and BM25
scores (which live on incomparable scales), and ``k`` (default 60, from the original paper)
damps the influence of any single very-high rank.
"""

from __future__ import annotations

from collections.abc import Sequence


def rrf_fuse(
    rankings: Sequence[Sequence[str]],
    *,
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists (best first). Returns ``(id, score)`` sorted best first.

    Ties are broken by the order of first appearance across the input rankings, so the result is
    fully deterministic.
    """
    if weights is not None and len(weights) != len(rankings):
        raise ValueError("weights must match the number of rankings")
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for r, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else weights[r]
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
            first_seen.setdefault(doc_id, len(first_seen))
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
