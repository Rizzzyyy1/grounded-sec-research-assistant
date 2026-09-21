"""Ablation studies over retrieval configurations.

Each preset (``configs/retrieval.yaml``) changes one factor with everything else fixed. Results
are compared on the *same* questions with a paired bootstrap of the difference against a
baseline, so "hybrid beats dense" is only claimed when the interval excludes zero.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from finsight.config.settings import RetrievalSettings
from finsight.evaluation.datasets import GoldExample
from finsight.evaluation.runner import (
    RetrievalRow,
    latency_percentiles,
    run_retrieval_eval,
    summarize_retrieval,
)
from finsight.evaluation.stats import Interval, paired_bootstrap_diff
from finsight.retrieval.retriever import Retriever


@dataclass(frozen=True)
class AblationResult:
    name: str
    settings: RetrievalSettings
    rows: list[RetrievalRow]


def run_retrieval_ablation(
    factory: Callable[[RetrievalSettings], Retriever],
    presets: Mapping[str, RetrievalSettings],
    examples: Sequence[GoldExample],
    *,
    auto_filters: bool = True,
    on_progress: Callable[[str], None] = lambda _m: None,
) -> list[AblationResult]:
    results: list[AblationResult] = []
    for name, settings in presets.items():
        on_progress(f"evaluating preset {name!r}")
        retriever = factory(settings)
        rows = run_retrieval_eval(retriever, examples, auto_filters=auto_filters)
        results.append(AblationResult(name, settings, rows))
    return results


def paired_metric_diff(a: AblationResult, b: AblationResult, metric: str) -> Interval:
    """CI of metric(a) - metric(b) over the questions both were scored on."""
    ra, rb = {r.example_id: r for r in a.rows}, {r.example_id: r for r in b.rows}
    shared = sorted(set(ra) & set(rb))
    return paired_bootstrap_diff(
        [ra[i].scores[metric] for i in shared], [rb[i].scores[metric] for i in shared]
    )


def render_ablation_markdown(
    results: Sequence[AblationResult], *, metric: str = "recall@8", baseline: str | None = None,
) -> str:  # fmt: skip
    if not results:
        return "_no results_"
    base = next((r for r in results if r.name == baseline), results[0])
    header = (
        f"| Preset | {metric} [95% CI] | MRR | nDCG@8 | p50 ms | p95 ms "
        f"| Δ vs {base.name} [95% CI] |"
    )
    lines = [header, "|---|---|---|---|---|---|---|"]
    for r in results:
        summary = summarize_retrieval(r.rows)
        p50, p95 = latency_percentiles([row.latency_ms for row in r.rows])
        if r is base:
            delta = "-"
        else:
            diff = paired_metric_diff(r, base, metric)
            delta = f"{diff}" + (" *" if diff.excludes_zero() else "")
        lines.append(
            f"| {r.name} | {summary[metric]} | {summary['mrr'].mean:.3f} | "
            f"{summary['ndcg@8'].mean:.3f} | {p50:.0f} | {p95:.0f} | {delta} |"
        )
    lines += [
        "",
        f"`*` = paired-bootstrap 95% CI of the difference excludes zero "
        f"(n={len(base.rows)} questions).",
    ]
    return "\n".join(lines)
