"""Ablation A7: how does retrieval depth (final_k) trade recall against context size?

    .venv/bin/python scripts/ablation_recall_curve.py

One retrieval at depth 16 per question gives recall@k for every smaller k for free (the top-k of a
ranking is the ranking's prefix), so this is exact, not an approximation. Default config: hybrid,
rerank off, query-derived filters on, dev split.
"""

from __future__ import annotations

from pathlib import Path

from finsight.config.settings import get_settings
from finsight.evaluation.datasets import load_gold
from finsight.evaluation.runner import run_retrieval_eval, summarize_retrieval
from finsight.stack import load_stack

ROOT = Path(__file__).resolve().parents[1]
KS = (1, 3, 5, 8, 12, 16)


def main() -> None:
    settings = get_settings()
    gold = [
        g for g in load_gold(settings.eval_dir / "gold_v1.jsonl", split="dev") if g.gold_sources
    ]
    with load_stack(settings) as stack:
        rows = run_retrieval_eval(stack.retriever(), gold, ks=KS, k=max(KS))
    summary = summarize_retrieval(rows)
    lines = [
        f"Hybrid, rerank off, filters on, dev split (n={len(rows)}). Approx. context cost assumes ~300 tokens/chunk.",
        "",
        "| k | recall@k [95% CI] | hit@k | approx. context tokens |",
        "|---|---|---|---|",
        *[
            f"| {k} | {summary[f'recall@{k}']} | {summary[f'hit@{k}'].mean:.3f} | ~{k * 300:,} |"
            for k in KS
        ],
    ]
    out = "\n".join(lines) + "\n"
    (ROOT / "reports" / "ablation_A7_recall_curve_dev.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
