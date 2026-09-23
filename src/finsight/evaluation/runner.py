"""Evaluation runners.

* :func:`run_retrieval_eval` - retrieval only (no LLM): fast, free, deterministic, so it runs in
  CI as a regression gate and drives the ablations.
* :func:`run_generation_eval` - end to end through any ``question -> Answer`` callable, so the
  single-shot RAG pipeline and the agent (Phase 6) are scored by the same code. Examples run
  concurrently; a failure in one is recorded on that example, never fatal to the run.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from finsight.core.schemas import Answer
from finsight.evaluation.datasets import GoldExample
from finsight.evaluation.metrics.generation import (
    CitationHygiene,
    abstention_scores,
    citation_hygiene,
    rule_correct,
)
from finsight.evaluation.metrics.retrieval import DEFAULT_KS, score_ranking
from finsight.evaluation.stats import Interval, bootstrap_ci
from finsight.retrieval.retriever import Retriever


# --------------------------------------------------------------------------- retrieval
@dataclass(frozen=True)
class RetrievalRow:
    example_id: str
    qtype: str
    scores: dict[str, float]
    latency_ms: float
    n_retrieved: int


def run_retrieval_eval(
    retriever: Retriever,
    examples: Sequence[GoldExample],
    *,
    ks: Sequence[int] = DEFAULT_KS,
    auto_filters: bool = True,
    k: int | None = None,
) -> list[RetrievalRow]:
    """Score retrieval on every example that has gold sources."""
    rows: list[RetrievalRow] = []
    depth = k or max(ks)
    for ex in examples:
        if not ex.gold_sources:
            continue
        started = time.perf_counter()
        result = retriever.retrieve(ex.question, k=depth, auto_filters=auto_filters)
        latency = (time.perf_counter() - started) * 1000
        scores = score_ranking([r.chunk for r in result.chunks], ex.gold_sources, ks)
        rows.append(RetrievalRow(ex.id, ex.type.value, scores.flat(), latency, len(result.chunks)))
    return rows


def summarize_retrieval(rows: Sequence[RetrievalRow]) -> dict[str, Interval]:
    metrics = sorted({m for r in rows for m in r.scores})
    return {m: bootstrap_ci([r.scores[m] for r in rows]) for m in metrics}


def latency_percentiles(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    return (float(np.percentile(values, 50)), float(np.percentile(values, 95)))


# --------------------------------------------------------------------------- generation
@dataclass(frozen=True)
class GenerationRow:
    example: GoldExample
    answer: Answer | None
    error: str | None = None
    correct: bool | None = None
    hygiene: CitationHygiene | None = None


def _evaluate(example: GoldExample, answer_fn: Callable[[str], Answer]) -> GenerationRow:
    try:
        answer = answer_fn(example.question)
    except Exception as exc:  # one bad example must not abort a long run
        return GenerationRow(example, None, error=f"{type(exc).__name__}: {exc}")
    return GenerationRow(
        example, answer, correct=rule_correct(example.expected, answer),
        hygiene=None if answer.abstained else citation_hygiene(answer),
    )  # fmt: skip


def run_generation_eval(
    answer_fn: Callable[[str], Answer],
    examples: Sequence[GoldExample],
    *,
    workers: int = 4,
    on_progress: Callable[[int, int], None] = lambda _d, _t: None,
) -> list[GenerationRow]:
    rows: list[GenerationRow | None] = [None] * len(examples)
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_evaluate, ex, answer_fn): i for i, ex in enumerate(examples)}
        for future, index in futures.items():
            rows[index] = future.result()
            done += 1
            on_progress(done, len(examples))
    return [r for r in rows if r is not None]


@dataclass
class GenerationSummary:
    n: int
    errors: int
    accuracy: Interval  # over examples with a rule-based expectation
    accuracy_by_type: dict[str, Interval]
    abstention_precision: float
    abstention_recall: float
    abstention_f1: float
    citation_clean_rate: float | None  # among answered (non-abstained) examples
    p50_latency_ms: float
    p95_latency_ms: float
    total_cost_usd: float
    cost_per_query_usd: float
    per_example_correct: dict[str, float] = field(default_factory=dict)
    #: Diagnostic only - never part of `clean`. Among *clean* answers, how many cited at least one
    #: fact (agent/tools.py::_register_fact) vs. at least one retrieved passage; a hybrid answer
    #: counts in both. Distinguishes "citation hygiene rose because numeric answers can now cite
    #: at all" from "... because the system got better at citing passages" - see EVALUATION.md 3.2.
    clean_citation_kinds: dict[str, int] = field(default_factory=dict)


def summarize_generation(rows: Sequence[GenerationRow]) -> GenerationSummary:
    answered = [r for r in rows if r.answer is not None]
    scored = [r for r in answered if r.correct is not None]
    by_type: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        by_type[r.example.type.value].append(float(bool(r.correct)))
    pairs = [(r.example, r.answer) for r in answered if r.answer is not None]
    ab = abstention_scores(pairs)
    hygienic = [r.hygiene for r in answered if r.hygiene is not None]
    clean_kinds: dict[str, int] = defaultdict(int)
    for h in hygienic:
        if h.clean:
            for kind in h.citation_kinds:
                clean_kinds[kind] += 1
    p50, p95 = latency_percentiles([r.answer.latency_ms for r in answered if r.answer])
    cost = sum(r.answer.usage.cost_usd for r in answered if r.answer)
    return GenerationSummary(
        n=len(rows),
        errors=len(rows) - len(answered),
        accuracy=bootstrap_ci([float(bool(r.correct)) for r in scored]),
        accuracy_by_type={t: bootstrap_ci(v) for t, v in sorted(by_type.items())},
        abstention_precision=ab.precision, abstention_recall=ab.recall, abstention_f1=ab.f1,
        citation_clean_rate=(sum(h.clean for h in hygienic) / len(hygienic)) if hygienic else None,
        p50_latency_ms=p50, p95_latency_ms=p95, total_cost_usd=cost,
        cost_per_query_usd=cost / len(answered) if answered else 0.0,
        per_example_correct={r.example.id: float(bool(r.correct)) for r in scored},
        clean_citation_kinds=dict(clean_kinds),
    )  # fmt: skip
