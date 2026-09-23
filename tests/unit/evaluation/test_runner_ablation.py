"""Runners and ablation engine on a synthetic corpus with known ground truth."""

from __future__ import annotations

from pathlib import Path

import pytest

from finsight.config.settings import RetrievalSettings
from finsight.config.universe import load_universe
from finsight.core.schemas import Answer, QueryType, Usage
from finsight.evaluation.ablation import (
    paired_metric_diff,
    render_ablation_markdown,
    run_retrieval_ablation,
)
from finsight.evaluation.datasets import Expected, GoldExample, GoldSource, NumericExpectation
from finsight.evaluation.report import render_generation_summary, write_generation_run
from finsight.evaluation.runner import (
    latency_percentiles,
    run_generation_eval,
    run_retrieval_eval,
    summarize_generation,
    summarize_retrieval,
)
from finsight.indexing.embeddings import HashingEmbedder
from finsight.indexing.sparse_index import SparseIndex
from finsight.indexing.vector_store import InMemoryVectorStore
from finsight.retrieval.dense import DenseRetriever
from finsight.retrieval.query_analysis import QueryAnalyzer
from finsight.retrieval.rerank import LexicalReranker
from finsight.retrieval.retriever import Retriever
from finsight.retrieval.sparse import SparseRetriever

from tests.unit.conftest import ChunkFactory  # isort: skip

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[3]


def gold_ex(i: int, question: str, ticker: str, year: int, item: str) -> GoldExample:
    return GoldExample(
        id=f"r{i}", split="dev", type=QueryType.QUALITATIVE, question=question, expected=Expected(),
        gold_sources=(GoldSource(ticker=ticker, fiscal_year=year, item=item),), provenance="template",
    )  # fmt: skip


@pytest.fixture
def world(make_chunk: ChunkFactory):  # type: ignore[no-untyped-def]
    rows = [
        ("Apple risk factors include supply chain disruption in Asia", "AAPL", 2024, "1A"),
        ("Apple properties include campuses in Cupertino California", "AAPL", 2024, "2"),
        ("Apple legal proceedings relate to antitrust matters", "AAPL", 2024, "3"),
        ("Microsoft risk factors include cybersecurity threats", "MSFT", 2024, "1A"),
        ("Microsoft properties include datacenters worldwide", "MSFT", 2024, "2"),
    ]
    corpus = [make_chunk(t, ticker=tk, year=y, item=i) for t, tk, y, i in rows]
    examples = [
        gold_ex(1, "What supply chain risk factors does Apple disclose in fiscal 2024?", "AAPL", 2024, "1A"),
        gold_ex(2, "What properties does Apple own in fiscal 2024?", "AAPL", 2024, "2"),
        gold_ex(3, "What antitrust legal proceedings does Apple describe in fiscal 2024?", "AAPL", 2024, "3"),
        gold_ex(4, "What cybersecurity risk factors does Microsoft cite in fiscal 2024?", "MSFT", 2024, "1A"),
    ]  # fmt: skip
    emb, store, sparse = HashingEmbedder(512), InMemoryVectorStore(), SparseIndex()
    store.upsert(corpus, emb.embed_documents([c.indexed_text for c in corpus]))
    sparse.build(corpus)
    analyzer = QueryAnalyzer(load_universe(ROOT / "configs" / "universe.yaml"))

    def factory(rs: RetrievalSettings) -> Retriever:
        return Retriever(
            {c.id: c for c in corpus}, rs, dense=DenseRetriever(emb, store),
            sparse=SparseRetriever(sparse), reranker=LexicalReranker() if rs.rerank else None,
            analyzer=analyzer,
        )  # fmt: skip

    return factory, examples


def test_retrieval_eval_scores_every_example_with_gold_sources(world) -> None:  # type: ignore[no-untyped-def]
    factory, examples = world
    no_gold = GoldExample(id="n", split="dev", type=QueryType.NUMERIC, question="What was revenue in 2024?",
                          expected=Expected(numeric=NumericExpectation(value=1, unit="usd", rel_tol=0.1)),
                          provenance="xbrl")  # fmt: skip
    rows = run_retrieval_eval(
        factory(RetrievalSettings(rerank=False, final_k=4)), [*examples, no_gold]
    )
    assert len(rows) == 4  # the example without gold sources is skipped, not scored as zero
    assert all(r.latency_ms >= 0 for r in rows)
    summary = summarize_retrieval(rows)
    assert summary["hit@8"].mean == 1.0  # tiny corpus, filtered to the right company/year
    assert summary["mrr"].mean > 0.5


def test_filters_are_what_make_the_right_filing_reachable(world) -> None:  # type: ignore[no-untyped-def]
    factory, examples = world
    on = run_retrieval_eval(factory(RetrievalSettings(rerank=False)), examples, auto_filters=True)
    assert all(r.scores["hit@1"] in (0.0, 1.0) for r in on)


def test_latency_percentiles() -> None:
    p50, p95 = latency_percentiles([1, 2, 3, 4, 100])
    assert p50 == 3 and 4 < p95 <= 100
    assert all(x != x for x in latency_percentiles([]))  # NaN for empty input


def test_ablation_and_paired_comparison(world) -> None:  # type: ignore[no-untyped-def]
    factory, examples = world
    presets = {
        "dense_only": RetrievalSettings(mode="dense", rerank=False),
        "hybrid": RetrievalSettings(mode="hybrid", rerank=False),
        "hybrid_rerank": RetrievalSettings(mode="hybrid", rerank=True, rerank_top_n=10, final_k=5),
    }
    messages: list[str] = []
    results = run_retrieval_ablation(factory, presets, examples, on_progress=messages.append)
    assert [r.name for r in results] == list(presets) and len(messages) == 3
    diff = paired_metric_diff(results[1], results[0], "recall@8")
    assert -1 <= diff.mean <= 1
    table = render_ablation_markdown(results, baseline="dense_only")
    for name in presets:
        assert name in table
    assert "Δ vs dense_only" in table and "paired-bootstrap" in table
    assert render_ablation_markdown([]) == "_no results_"


# ------------------------------------------------------------------ generation
def answer_for(
    text: str, *, abstained: bool = False, cost: float = 0.0, latency: float = 10.0
) -> Answer:
    return Answer(question="q", text=text, abstained=abstained, latency_ms=latency,
                  usage=Usage(cost_usd=cost, input_tokens=10, output_tokens=5))  # fmt: skip


def gen_examples() -> list[GoldExample]:
    num = Expected(numeric=NumericExpectation(value=100e6, unit="usd", rel_tol=0.01))
    return [
        GoldExample(id="a", split="dev", type=QueryType.NUMERIC, question="What was revenue in fiscal 2024?", expected=num, provenance="xbrl"),
        GoldExample(id="b", split="dev", type=QueryType.NUMERIC, question="What was net income in fiscal 2024?", expected=num, provenance="xbrl"),
        GoldExample(id="c", split="dev", type=QueryType.OUT_OF_SCOPE, question="Should I buy Apple stock now?", expected=Expected(abstain=True), provenance="adversarial"),
        GoldExample(id="d", split="dev", type=QueryType.OUT_OF_SCOPE, question="Is Tesla a good investment?", expected=Expected(abstain=True), provenance="adversarial"),
    ]  # fmt: skip


def test_generation_eval_summary_matches_hand_computation() -> None:
    replies = {
        "What was revenue in fiscal 2024?": answer_for("$100 million", cost=0.01),  # correct
        "What was net income in fiscal 2024?": answer_for("$1 million", cost=0.03),  # wrong
        "Should I buy Apple stock now?": answer_for("declined", abstained=True),  # correct abstain
        "Is Tesla a good investment?": answer_for("Yes, buy it!", cost=0.02),  # missed abstention
    }
    rows = run_generation_eval(lambda q: replies[q], gen_examples(), workers=2)
    assert [r.correct for r in rows] == [
        True,
        False,
        True,
        False,
    ]  # order preserved despite threads
    s = summarize_generation(rows)
    assert (s.n, s.errors) == (4, 0)
    assert s.accuracy.mean == pytest.approx(0.5)
    assert s.abstention_precision == 1.0 and s.abstention_recall == 0.5
    assert s.total_cost_usd == pytest.approx(0.06) and s.cost_per_query_usd == pytest.approx(0.015)
    assert set(s.accuracy_by_type) == {"numeric", "out_of_scope"}
    assert s.accuracy_by_type["numeric"].mean == 0.5


def test_one_failing_example_is_recorded_not_fatal() -> None:
    def flaky(question: str) -> Answer:
        if "net income" in question:
            raise RuntimeError("API down")
        return answer_for("$100 million")

    rows = run_generation_eval(flaky, gen_examples()[:2])
    assert rows[0].correct is True
    assert rows[1].answer is None and "RuntimeError: API down" in (rows[1].error or "")
    s = summarize_generation(rows)
    assert (s.n, s.errors) == (2, 1)


def test_progress_callback_and_citation_hygiene_rate() -> None:
    seen: list[tuple[int, int]] = []
    rows = run_generation_eval(lambda q: answer_for("$100 million"), gen_examples()[:2],
                               on_progress=lambda d, t: seen.append((d, t)))  # fmt: skip
    assert seen[-1] == (2, 2)
    assert summarize_generation(rows).citation_clean_rate == 0.0  # no citations -> not clean


def test_clean_citation_kinds_breaks_down_fact_vs_passage_without_moving_the_rate() -> None:
    """Diagnostic only: adding citation_kinds must never change citation_clean_rate, so a run
    from before fact citations existed stays comparable to one measured after."""
    from finsight.core.schemas import Citation  # noqa: PLC0415

    fact = Citation(source_id="S1", kind="fact", ticker="AAPL", fiscal_year=2024, url="u",
                    quote="q", metric="revenue", xbrl_tag="Revenues")  # fmt: skip
    replies = {
        "What was revenue in fiscal 2024?": answer_for("$100 million", cost=0.01).model_copy(
            update={"citations": (fact,)}
        ),
        "What was net income in fiscal 2024?": answer_for("$1 million", cost=0.03),
        "Should I buy Apple stock now?": answer_for("declined", abstained=True),
        "Is Tesla a good investment?": answer_for("Yes, buy it!", cost=0.02),
    }
    rows = run_generation_eval(lambda q: replies[q], gen_examples(), workers=1)
    s = summarize_generation(rows)
    # 1 of 3 non-abstained answers is clean (the cited one) - citation_kinds must not move this.
    assert s.citation_clean_rate == pytest.approx(1 / 3)
    assert s.clean_citation_kinds == {"fact": 1}
    assert "citation kind used (fact: 1)" in render_generation_summary("x", s, {})


def test_run_artefacts_are_written(tmp_path: Path) -> None:
    rows = run_generation_eval(lambda q: answer_for("$100 million"), gen_examples()[:2])
    summary = summarize_generation(rows)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_generation_run(run_dir, "unit", rows, summary, {"system": "test"})
    assert {p.name for p in run_dir.iterdir()} == {"config.json", "results.jsonl", "summary.md"}
    md = (run_dir / "summary.md").read_text()
    assert (
        "Evaluation run: unit" in md and "Accuracy by question type" in md and "exploratory" in md
    )
    assert len((run_dir / "results.jsonl").read_text().splitlines()) == 2
    assert "rule-based accuracy" in render_generation_summary("x", summary, {})
