"""CI regression gate: retrieval quality on real 10-K text must not drop below the baseline.

Hermetic by design - deterministic hashing embedder + BM25 over a committed fixture of 182 real
passages - so it needs no network, model download, GPU or API key and runs on every PR. If it
fails, chunking / tokenisation / fusion / filtering changed retrieval quality; either fix the
regression or, if the change is intentional, re-run ``scripts/make_regression_fixture.py`` and
update ``tests/fixtures/retrieval_baseline.json`` in the same PR with the reasoning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finsight.config.settings import RetrievalSettings
from finsight.config.universe import load_universe
from finsight.evaluation.datasets import load_gold
from finsight.evaluation.runner import run_retrieval_eval, summarize_retrieval
from finsight.indexing.embeddings import HashingEmbedder
from finsight.indexing.sparse_index import SparseIndex
from finsight.indexing.vector_store import InMemoryVectorStore
from finsight.processing.pipeline import read_chunks
from finsight.retrieval.dense import DenseRetriever
from finsight.retrieval.query_analysis import QueryAnalyzer
from finsight.retrieval.retriever import Retriever
from finsight.retrieval.sparse import SparseRetriever

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def retriever_factory():  # type: ignore[no-untyped-def]
    chunks = read_chunks(FIXTURES / "mini_corpus.parquet")
    emb, store, sparse = HashingEmbedder(512), InMemoryVectorStore(), SparseIndex()
    store.upsert(chunks, emb.embed_documents([c.indexed_text for c in chunks]))
    sparse.build(chunks)
    analyzer = QueryAnalyzer(load_universe(ROOT / "configs" / "universe.yaml"))
    catalogue = {c.id: c for c in chunks}

    def make(mode: str = "hybrid") -> Retriever:
        return Retriever(
            catalogue, RetrievalSettings(mode=mode, rerank=False),  # type: ignore[arg-type]
            dense=DenseRetriever(emb, store), sparse=SparseRetriever(sparse), analyzer=analyzer,
        )  # fmt: skip

    return make


def test_hybrid_retrieval_does_not_regress_below_the_committed_baseline(retriever_factory) -> None:  # type: ignore[no-untyped-def]
    baseline = json.loads((FIXTURES / "retrieval_baseline.json").read_text())
    gold = load_gold(FIXTURES / "mini_gold.jsonl")
    assert len(gold) == baseline["n_questions"]
    scores = summarize_retrieval(run_retrieval_eval(retriever_factory("hybrid"), gold))
    for metric, floor in baseline["metrics"].items():
        assert scores[metric].mean >= floor - baseline["margin"], (
            f"{metric} regressed: {scores[metric].mean:.3f} < baseline {floor:.3f} - {baseline['margin']}"
        )


def test_metadata_prefiltering_is_worth_it(retriever_factory) -> None:  # type: ignore[no-untyped-def]
    """The design claim behind pre-filters: naming the company/year in the question must help."""
    gold = load_gold(FIXTURES / "mini_gold.jsonl")
    on = summarize_retrieval(run_retrieval_eval(retriever_factory(), gold, auto_filters=True))
    off = summarize_retrieval(run_retrieval_eval(retriever_factory(), gold, auto_filters=False))
    assert on["recall@8"].mean > off["recall@8"].mean + 0.10


def test_fixture_is_real_filing_text_across_the_expected_filings() -> None:
    chunks = read_chunks(FIXTURES / "mini_corpus.parquet")
    assert {(c.metadata.ticker, c.metadata.fiscal_year) for c in chunks} == {
        ("AAPL", 2024), ("MSFT", 2024), ("XOM", 2023), ("WMT", 2024)
    }  # fmt: skip
    assert sum(c.metadata.item == "1A" for c in chunks) >= 40
    assert all(c.metadata.source_url.startswith("https://www.sec.gov/") for c in chunks)
