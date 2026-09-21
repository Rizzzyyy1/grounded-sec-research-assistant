"""Ablation A4 (BM25 half): does the contextual header help lexical retrieval?

BM25 can be rebuilt in seconds, unlike the dense index (~35 min to re-embed), so this half of A4
is cheap and reproducible:  .venv/bin/python scripts/ablation_header_bm25.py

It matters because the header literally contains "Item 1A - Risk Factors": on templated questions
that name the section, BM25 could look good for a reason that will not transfer to real queries.
"""

from __future__ import annotations

from pathlib import Path

from finsight.config.settings import RetrievalSettings
from finsight.config.universe import load_universe
from finsight.evaluation.ablation import AblationResult, render_ablation_markdown
from finsight.evaluation.datasets import load_gold
from finsight.evaluation.runner import run_retrieval_eval
from finsight.indexing.sparse_index import SparseIndex
from finsight.processing.pipeline import read_chunks
from finsight.retrieval.query_analysis import QueryAnalyzer
from finsight.retrieval.retriever import Retriever
from finsight.retrieval.sparse import SparseRetriever

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    chunks = read_chunks(ROOT / "data" / "processed" / "chunks.parquet")
    catalogue = {c.id: c for c in chunks}
    analyzer = QueryAnalyzer(load_universe(ROOT / "configs" / "universe.yaml"))
    gold = [
        g
        for g in load_gold(ROOT / "data" / "eval" / "gold_v1.jsonl", split="dev")
        if g.gold_sources
    ]
    settings = RetrievalSettings(mode="sparse", rerank=False)

    results: list[AblationResult] = []
    for name, corpus in (
        ("bm25_no_header", [c.model_copy(update={"embed_text": None}) for c in chunks]),
        ("bm25_with_header", chunks),
    ):
        index = SparseIndex()
        index.build(corpus)
        retriever = Retriever(catalogue, settings, sparse=SparseRetriever(index), analyzer=analyzer)
        results.append(AblationResult(name, settings, run_retrieval_eval(retriever, gold)))

    table = render_ablation_markdown(results, baseline="bm25_no_header")
    (ROOT / "reports" / "ablation_A4_bm25_header_dev.md").write_text(table + "\n", encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
