"""Retriever facade on a synthetic corpus with fakes (no models, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from finsight.config.settings import RetrievalSettings
from finsight.config.universe import load_universe
from finsight.core.exceptions import RetrievalError
from finsight.core.filters import RetrievalFilters
from finsight.indexing.embeddings import HashingEmbedder
from finsight.indexing.sparse_index import SparseIndex
from finsight.indexing.vector_store import InMemoryVectorStore
from finsight.retrieval.dense import DenseRetriever
from finsight.retrieval.query_analysis import QueryAnalyzer
from finsight.retrieval.rerank import LexicalReranker
from finsight.retrieval.retriever import Retriever, diversify
from finsight.retrieval.sparse import SparseRetriever

from tests.unit.conftest import ChunkFactory  # isort: skip

pytestmark = pytest.mark.unit


@pytest.fixture
def corpus(make_chunk: ChunkFactory) -> list:  # type: ignore[type-arg]
    rows = [
        ("Apple depends on outsourcing partners in China mainland for manufacturing", "AAPL", 2024),
        ("Apple supply chain disruption could harm results of operations", "AAPL", 2024),
        ("Apple net sales were $391,035 million", "AAPL", 2024),
        ("Apple supply chain risks were similar in the prior year", "AAPL", 2023),
        ("Microsoft cloud supply chain capacity constraints", "MSFT", 2024),
        ("Microsoft revenue grew due to Azure demand", "MSFT", 2024),
        ("Tesla battery supply chain and lithium costs", "TSLA", 2024),
        ("Walmart eCommerce growth and membership income", "WMT", 2024),
    ]
    return [make_chunk(text, ticker=t, year=y) for text, t, y in rows]


def build(corpus, mode: str = "hybrid", **kw):  # type: ignore[no-untyped-def]
    emb = HashingEmbedder(512)
    store = InMemoryVectorStore()
    store.upsert(corpus, emb.embed_documents([c.indexed_text for c in corpus]))
    sparse = SparseIndex()
    sparse.build(corpus)
    root = Path(__file__).resolve().parents[3]
    settings = RetrievalSettings(mode=mode, rerank=kw.pop("rerank", False), **kw)
    return Retriever(
        {c.id: c for c in corpus},
        settings,
        dense=DenseRetriever(emb, store),
        sparse=SparseRetriever(sparse),
        reranker=LexicalReranker() if settings.rerank else None,
        analyzer=QueryAnalyzer(load_universe(root / "configs" / "universe.yaml")),
    )


def test_hybrid_returns_ranked_chunks_with_stage_evidence(corpus) -> None:  # type: ignore[no-untyped-def]
    result = build(corpus).retrieve("supply chain disruption", k=4, auto_filters=False)
    assert 0 < len(result.chunks) <= 4
    top = result.chunks[0]
    assert "supply chain" in top.chunk.text.lower()
    assert top.dense_rank is not None or top.sparse_rank is not None
    scores = [c.score for c in result.chunks]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("mode", ["dense", "sparse", "hybrid"])
def test_each_mode_populates_only_its_own_stage(corpus, mode: str) -> None:  # type: ignore[no-untyped-def]
    chunks = build(corpus, mode).retrieve("Apple supply chain", auto_filters=False).chunks
    assert chunks
    for c in chunks:
        if mode == "dense":
            assert c.sparse_rank is None and c.dense_rank is not None
        elif mode == "sparse":
            assert c.dense_rank is None and c.sparse_rank is not None


def test_exact_figure_is_found_lexically(corpus) -> None:  # type: ignore[no-untyped-def]
    result = build(corpus, "sparse").retrieve("$391,035", auto_filters=False)
    assert "391,035" in result.chunks[0].chunk.text


def test_auto_filters_restrict_to_the_ticker_and_year_in_the_question(corpus) -> None:  # type: ignore[no-untyped-def]
    result = build(corpus).retrieve("Apple supply chain risks in fiscal 2024")
    assert result.analysis and result.analysis.tickers == ("AAPL",)
    assert result.filters.tickers == ("AAPL",) and result.filters.fiscal_years == (2024,)
    assert {(c.chunk.metadata.ticker, c.chunk.metadata.fiscal_year) for c in result.chunks} == {
        ("AAPL", 2024)
    }


def test_explicit_filters_override_auto_filters(corpus) -> None:  # type: ignore[no-untyped-def]
    result = build(corpus).retrieve(
        "Apple supply chain", filters=RetrievalFilters(tickers=("MSFT",))
    )
    assert {c.chunk.metadata.ticker for c in result.chunks} == {"MSFT"}


def test_filters_matching_nothing_yield_an_honest_empty_result(corpus) -> None:  # type: ignore[no-untyped-def]
    result = build(corpus).retrieve("Apple supply chain in 2015")
    assert result.chunks == []  # no fallback to unfiltered results: better to abstain than mislead


def test_reranker_reorders_by_its_own_score(corpus) -> None:  # type: ignore[no-untyped-def]
    plain = build(corpus, "dense").retrieve("China mainland outsourcing", auto_filters=False, k=5)
    reranked = build(corpus, "dense", rerank=True).retrieve(
        "China mainland outsourcing", auto_filters=False, k=5
    )
    top = reranked.chunks[0]
    assert top.rerank_score is not None and top.rerank_score == max(
        c.rerank_score or 0 for c in reranked.chunks
    )
    assert "China mainland" in top.chunk.text
    assert all(c.rerank_score is None for c in plain.chunks)


def test_k_and_timings(corpus) -> None:  # type: ignore[no-untyped-def]
    result = build(corpus).retrieve("supply chain", k=2, auto_filters=False)
    assert len(result.chunks) == 2
    assert {"analysis", "dense", "sparse", "fusion", "rerank", "diversify"} <= set(
        result.timings_ms
    )
    assert all(v >= 0 for v in result.timings_ms.values())


def test_diversify_caps_per_filing_but_refills_from_spillover(make_chunk: ChunkFactory) -> None:
    a = [make_chunk(f"a{i}", accession="0000000001-24-000001") for i in range(5)]
    b = [make_chunk(f"b{i}", accession="0000000002-24-000001") for i in range(2)]
    catalogue = {c.id: c for c in a + b}
    order = [c.id for c in a + b]
    capped = diversify(order, catalogue, per_filing=2, k=4)
    accessions = [catalogue[i].metadata.accession for i in capped]
    assert accessions.count("0000000001-24-000001") == 2
    assert accessions.count("0000000002-24-000001") == 2
    # not enough other evidence -> the cap yields rather than returning too little
    assert len(diversify([c.id for c in a], catalogue, per_filing=2, k=4)) == 4


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (dict(mode="dense"), "dense retriever"),
        (dict(mode="sparse"), "sparse retriever"),
        (dict(mode="hybrid"), "dense retriever"),
    ],
)
def test_missing_components_are_rejected_at_construction(kwargs: dict, message: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(RetrievalError, match=message):
        Retriever({}, RetrievalSettings(rerank=False, **kwargs))


def test_rerank_without_a_reranker_is_rejected() -> None:
    with pytest.raises(RetrievalError, match="reranker"):
        Retriever(
            {}, RetrievalSettings(mode="sparse", rerank=True), sparse=SparseRetriever(SparseIndex())
        )
