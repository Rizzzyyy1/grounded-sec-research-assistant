# ADR-0002: Hybrid retrieval fused with RRF, then cross-encoder rerank
* **Status:** Accepted; **amended 2026-09-21 after ablation A1: reranking is opt-in** · **Date:** 2026-09-21

## Context
Financial questions mix semantic intent ("why did margins compress?") with exact tokens
(`$391.0 billion`, `ASC 842`, product and segment names, `Item 1A`). Dense embeddings blur exact
tokens; BM25 misses paraphrase ("cost of goods" vs "cost of sales"). Their scores are on
incomparable scales (cosine similarity vs. unbounded BM25).

## Decision
Retrieve top-k from both a dense index and a BM25 index, fuse by **Reciprocal Rank Fusion**
(`Σ 1/(60 + rank)`), rerank the fused top-N with a cross-encoder, then apply a per-filing cap.
Each stage is switchable via `RetrievalSettings` and independently evaluated.

## Alternatives considered
* *Dense only* — simplest; predicted weak on exact figures and defined terms.
* *Weighted score fusion* — needs score normalisation and tuning per corpus; kept as an option.
* *Learned sparse (SPLADE)* — promising, but adds a model and ops cost; revisit if BM25 is the bottleneck.
* *LLM reranking* — high quality but slow and costly per query; candidate stretch ablation.

## Consequences
+ Robust to both query styles; rank-based fusion needs no calibration. + Every stage ablatable.
− Two indexes to keep consistent (mitigated by a single builder + manifest). − Reranking adds
~100–300 ms; justified only if ablation A1/A6 show a gain — otherwise it is turned off by default.

## Amendment: what the ablation showed (dev split, n = 29; see `reports/ablation_A1_dev.md`)

| | recall@8 | nDCG@8 | p50 |
|---|---|---|---|
| dense only | 0.707 | 0.275 | 157 ms |
| BM25 only | 0.897 | 0.302 | 1 ms |
| **hybrid (default)** | 0.897 | 0.346 | 159 ms |
| hybrid + rerank | 0.897 | 0.434 | 2,484 ms |

Hybrid beats dense-only significantly (paired CI [+0.034, +0.362]). The cross-encoder improves *ordering*
but not recall@8, at ~16x the latency, and all eight passages reach the model whatever their order - so
`rerank` defaults to **off** and remains the `hybrid_rerank` preset. BM25 alone matches hybrid's recall on
these templated questions (partly because the contextual header contains the Item name: without it BM25
falls to 0.586), so hybrid is kept for robustness to *paraphrase*, which this dev set under-tests.
