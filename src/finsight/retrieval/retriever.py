"""Retriever facade: analyse -> filter -> dense + sparse -> fuse -> rerank -> diversify.

Every returned :class:`RetrievedChunk` carries the rank and score it earned in each stage, so a
retrieval failure can be diagnosed ("BM25 found it at rank 2, dense missed it, the reranker
demoted it") instead of guessed at. Stage timings are returned alongside for telemetry.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from finsight.config.settings import RetrievalSettings
from finsight.core.exceptions import RetrievalError
from finsight.core.filters import RetrievalFilters
from finsight.core.schemas import Chunk, QueryAnalysis, RetrievedChunk
from finsight.indexing.vector_store import Hit
from finsight.retrieval.dense import DenseRetriever
from finsight.retrieval.hybrid import rrf_fuse
from finsight.retrieval.query_analysis import QueryAnalyzer, to_filters
from finsight.retrieval.rerank import Reranker
from finsight.retrieval.sparse import SparseRetriever


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    analysis: QueryAnalysis | None
    filters: RetrievalFilters
    timings_ms: dict[str, float] = field(default_factory=dict)


def diversify(
    ordered_ids: Sequence[str], catalogue: Mapping[str, Chunk], *, per_filing: int, k: int
) -> list[str]:
    """Cap chunks per filing so one long section cannot crowd out other years/companies.

    Spillover (chunks skipped by the cap) refills the list if the cap left it short of ``k``,
    so the cap never *reduces* the amount of evidence when little else is available.
    """
    taken: list[str] = []
    skipped: list[str] = []
    counts: dict[str, int] = {}
    for chunk_id in ordered_ids:
        accession = catalogue[chunk_id].metadata.accession
        if counts.get(accession, 0) < per_filing:
            counts[accession] = counts.get(accession, 0) + 1
            taken.append(chunk_id)
        else:
            skipped.append(chunk_id)
        if len(taken) == k:
            return taken
    return (taken + skipped)[:k]


class Retriever:
    def __init__(
        self,
        catalogue: Mapping[str, Chunk],
        settings: RetrievalSettings,
        *,
        dense: DenseRetriever | None = None,
        sparse: SparseRetriever | None = None,
        reranker: Reranker | None = None,
        analyzer: QueryAnalyzer | None = None,
    ) -> None:
        if settings.mode in {"dense", "hybrid"} and dense is None:
            raise RetrievalError(f"mode {settings.mode!r} needs a dense retriever")
        if settings.mode in {"sparse", "hybrid"} and sparse is None:
            raise RetrievalError(f"mode {settings.mode!r} needs a sparse retriever")
        if settings.rerank and reranker is None:
            raise RetrievalError("settings.rerank is on but no reranker was provided")
        self._catalogue = catalogue
        self._s = settings
        self._dense, self._sparse = dense, sparse
        self._reranker = reranker if settings.rerank else None
        self._analyzer = analyzer

    @property
    def catalogue(self) -> Mapping[str, Chunk]:
        """The chunk catalogue this retriever serves (read-only view)."""
        return self._catalogue

    @property
    def analyzer(self) -> QueryAnalyzer | None:
        return self._analyzer

    def retrieve(
        self,
        query: str,
        *,
        k: int | None = None,
        filters: RetrievalFilters | None = None,
        auto_filters: bool = True,
    ) -> RetrievalResult:
        s = self._s
        k = k or s.final_k
        timings: dict[str, float] = {}

        def tick(stage: str, since: float) -> float:
            now = time.perf_counter()
            timings[stage] = (now - since) * 1000
            return now

        t = time.perf_counter()
        analysis = self._analyzer.analyze(query) if self._analyzer else None
        if filters is None:
            filters = to_filters(analysis) if (analysis and auto_filters) else RetrievalFilters()
        t = tick("analysis", t)

        dense_hits: list[Hit] = (
            self._dense.search(query, s.dense_k, filters)
            if self._dense and s.mode != "sparse"
            else []
        )
        t = tick("dense", t)
        sparse_hits: list[Hit] = (
            self._sparse.search(query, s.sparse_k, filters)
            if self._sparse and s.mode != "dense"
            else []
        )
        t = tick("sparse", t)

        dense_rank = {h.chunk_id: (i, h.score) for i, h in enumerate(dense_hits, start=1)}
        sparse_rank = {h.chunk_id: (i, h.score) for i, h in enumerate(sparse_hits, start=1)}

        if s.mode == "dense":
            fused = [(h.chunk_id, h.score) for h in dense_hits]
        elif s.mode == "sparse":
            fused = [(h.chunk_id, h.score) for h in sparse_hits]
        else:
            fused = rrf_fuse(
                [[h.chunk_id for h in dense_hits], [h.chunk_id for h in sparse_hits]],
                k=s.rrf_k,
                weights=[s.dense_weight, s.sparse_weight],
            )
        t = tick("fusion", t)

        scores = dict(fused)
        rerank_scores: dict[str, float] = {}
        ordered = [cid for cid, _ in fused]
        if self._reranker and ordered:
            candidates = ordered[: s.rerank_top_n]
            chunk_list = [self._catalogue[c] for c in candidates]
            for cid, sc in zip(candidates, self._reranker.score(query, chunk_list), strict=True):
                rerank_scores[cid] = sc
            ordered = sorted(candidates, key=lambda c: -rerank_scores[c])
            scores = {**scores, **rerank_scores}
        t = tick("rerank", t)

        final_ids = diversify(ordered, self._catalogue, per_filing=s.max_chunks_per_filing, k=k)
        tick("diversify", t)

        results = [
            RetrievedChunk(
                chunk=self._catalogue[cid],
                score=scores[cid],
                dense_rank=dense_rank.get(cid, (None, None))[0],
                sparse_rank=sparse_rank.get(cid, (None, None))[0],
                dense_score=dense_rank.get(cid, (None, None))[1],
                sparse_score=sparse_rank.get(cid, (None, None))[1],
                rerank_score=rerank_scores.get(cid),
            )
            for cid in final_ids
        ]
        return RetrievalResult(results, analysis, filters, timings)
