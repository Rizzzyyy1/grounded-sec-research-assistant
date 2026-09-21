# FinSight — Evaluation Methodology

> **Principle:** every design choice is justified by a number from this harness, and the protocol
> is fixed **before** results exist so it cannot be tuned to flatter them.
>
> **Status (implemented):** the harness, metrics, statistics and ablation engine below are built and
> tested. The gold set that ships (**`gold_v1`**) is an *automatically derived* set - see
> [§2.1](#21-what-gold_v1-actually-is) for exactly what that does and does not prove. The
> human-verified, LLM-assisted question set described in §2 is the target for `gold_v2`.

## 1. What is being evaluated

Two systems share one interface (`question → Answer`) and are scored by the same harness:

* **Baseline** — single-shot RAG (`generation/pipeline.py`, Phase 4)
* **Agent** — tool-using research agent (`agent/orchestrator.py`, Phase 6)

Components inside them (retriever, chunker, embedder, reranker) are additionally evaluated in
isolation with **retrieval-only** metrics, which need no LLM and therefore run in CI.

## 2. Gold dataset (`data/eval/gold_v1.jsonl`)

**Size & shape:** ~120 questions, stratified across the eight `QueryType`s (≥ 10 each; heavier
on numeric, trend and qualitative), spread across all 12 companies and multiple fiscal years.

**Record schema**

```json
{
  "id": "num-0042",
  "split": "dev | test",
  "type": "computed_metric",
  "question": "What was JPMorgan's return on equity in FY2023?",
  "expected": {
    "answer_text": "About 17%.",
    "numeric": {"value": 0.17, "unit": "ratio", "rel_tol": 0.02},
    "abstain": false
  },
  "gold_sources": [
    {"accession": "0000019617-24-000xxx", "item": "8", "chunk_ids": ["…"]}
  ],
  "required_tools": ["compute_ratio"],
  "notes": "ROE = net income / average equity"
}
```

**Construction protocol**

1. *Generate candidates* with an LLM from sampled passages and fact-table rows (cheap breadth).
2. *Human verification*: every question is answered independently from the filing; wrong or
   ambiguous items are dropped or rewritten. **Nothing enters the set unverified.**
3. *Gold source labels* (accession + item + chunk ids) are recorded so retrieval can be scored
   without an LLM.
4. *Adversarial slice* (~15 %): out-of-scope advice requests, unanswerable-from-corpus questions
   (should abstain), questions containing a false premise, and prompt-injection strings placed in
   the question.
5. **Split**: 70 % `dev` (used for tuning), 30 % `test` (**frozen**; touched only to produce the
   numbers reported in the README). Split is by question *template family*, not random, to avoid
   near-duplicate leakage.
6. Versioned (`gold_v1`, `gold_v2`…); any change bumps the version and results are re-run.

### 2.1 What `gold_v1` actually is

`gold_v1` (147 questions, `data/eval/gold_v1.jsonl`, built by `finsight eval gold`) is derived
programmatically, not written or verified by a human:

| Family | Count | How the answer is known | Verification |
|---|---|---|---|
| Numeric, ratio, trend, comparison | 85 | Computed from the XBRL fact store | Correct **by construction**; the fact store itself is checked against 14 figures published in the 10-Ks (`tests/integration/test_published_figures.py`) |
| Retrieval (qualitative, fact, change) | 44 | Gold *section* = the (company, year, Item) the question names | Template-derived; the gold Item is guaranteed to exist in the corpus |
| Adversarial (advice, unanswerable, injection) | 25 | Correct behaviour is known (abstain / ignore the injection) | By construction |

**What it proves:** the numeric and abstention behaviour is scored against ground truth with no
judge; retrieval is scored at section level with no judge.
**What it does not prove:** how the system handles *naturally phrased* or multi-hop questions -
templated questions are cleaner than real ones, so scores here are an upper bound on real-world
performance (measured: see §2.2). Splits hold out four whole companies (`test` = MSFT, JPM, WMT, JNJ), so a system
cannot be tuned on a company and then "validated" on the same company. Provenance is recorded on
every record.

### 2.2 `gold_v2_draft`: a natural-phrasing probe (not a gold set)

`data/eval/gold_v2_draft.jsonl` (built by `scripts/make_gold_v2_draft.py`, 38 questions, provenance
`draft`) rewrites question families in natural wording. Numeric expectations still come from the
XBRL store; the gold *sections* for text questions are unverified drafting judgement, and the file
stays a `draft` until a human has checked every label. Two results are recorded in
[RESULTS](../reports/RESULTS.md): the tool router falls from 0.94 (templated test) to 0.69 on natural
phrasing, and the advice guardrail had a hole (all 4 naturally phrased advice requests slipped through) that
templated questions could not reveal. The post-fix re-run is **in-sample** because the fix was written
from these questions; only the pre-fix column is a clean measurement.

## 3. Metrics

### 3.1 Retrieval (no LLM; runs in CI on a fixture corpus)

| Metric | Definition | Level |
|---|---|---|
| Recall@k (k = 1, 3, 5, 8, 20) | fraction of gold sources found in top-k | chunk & section |
| Hit-rate@k | ≥ 1 gold source in top-k | chunk & section |
| MRR | mean reciprocal rank of first gold source | chunk & section |
| nDCG@k | graded relevance (gold = 1, same-section = 0.5) | chunk |
| Filter accuracy | extracted `(ticker, year, form, item)` equals gold | query analysis |

Section-level scores are the primary retrieval headline: chunk boundaries are an implementation
detail, and comparing chunkers by chunk-id recall would be circular.

### 3.2 Generation

| Metric | How | Notes |
|---|---|---|
| **Numeric accuracy** | Extract numbers + units from the answer; match gold within `rel_tol` | The metric that matters most in finance; no LLM involved |
| **Faithfulness** | Claim decomposition → each claim judged *supported / unsupported / contradicted* against the cited context | LLM judge, calibrated (§4) |
| **Answer correctness** | Judge compares to `expected.answer_text` for qualitative items | Rubric-scored 0–2 |
| **Citation precision** | cited sources that actually support their sentence / all cited | |
| **Citation recall** | factual sentences with a valid citation / all factual sentences | |
| **Abstention accuracy** | F1 of abstain decision on answerable vs. unanswerable | Over-abstaining is penalised too |
| **Tool correctness** (agent) | required tools called, arguments match gold | |

### 3.3 System

Latency p50 / p95 (end-to-end and per stage), input/output/cached tokens, **cost per query**, and
cost per *correct* answer (the number that decides whether the agent is worth it).

## 4. LLM-as-judge: controls

* Judge model is **configured separately** from the generator (`llm.judge_model`) to reduce
  self-preference bias; judge prompts are versioned and stored with results.
* **Calibration:** a random 25 % of judged items are also scored by a human; agreement is
  reported (Cohen's κ / Spearman). If κ is low the judge metric is reported as *indicative only*.
* Judge sees the **cited context**, not the whole corpus, so "faithfulness" means faithful to what
  was retrieved, and retrieval misses are attributed to retrieval, not generation.
* Deterministic settings where the API permits; every judge call is cached by
  `(prompt_version, model, input_hash)` so re-runs are free and reproducible.

## 5. Statistics

* **Bootstrap 95 % CIs** (10 000 resamples over questions) on every headline metric.
* **Paired comparisons** between systems on the same questions: paired bootstrap for the
  difference; McNemar's test for binary correctness. Report effect size, not just p-values.
* With ~36 test questions overall and fewer per type, per-type results are labelled
  **exploratory** and shown with wide intervals — no claim is made from a difference the
  intervals do not support.
* Multiple-comparison caution: ablation grids are described as hypothesis-generating; the
  chosen default is confirmed once on the frozen test split.

## 6. Ablation plan

| # | Factor | Levels | Question answered |
|---|---|---|---|
| A1 | Retrieval mode | dense · BM25 · hybrid · hybrid + rerank | Does hybrid/rerank earn its complexity? |
| A2 | Chunk size | 200 · 400 · 800 tokens | Best size for prose vs tables |
| A3 | Overlap | 0 % · 15 % · 30 % | Is overlap worth the index bloat? |
| A4 | Contextual header | off · deterministic header · LLM situating sentence | Value of context vs. its cost |
| A5 | Embedding model | bge-small · bge-base · (one more) | Size/quality trade-off |
| A6 | Reranker | none · MiniLM cross-encoder · larger | Latency vs. precision |
| A7 | `final_k` | 4 · 8 · 12 | Recall vs. context noise and cost |
| A8 | System | single-shot RAG · agent | Does tool use improve numeric/trend types? |
| A9 | Generator model | configured Claude tiers | Cost/quality frontier |

**Status.** A1, A4 (BM25 half), A2/A3 (BM25 half) and A7 (a recall-vs-k curve, retrieval only) are run
and reported in [RESULTS](../reports/RESULTS.md). A5, A6 (beyond MiniLM), A8 with an LLM and A9 need a
model download or an API key and are **not run**. A2/A3 found no chunk size or overlap reliably better
than the default, and *no overlap* scored numerically higher than 15% (difference +0.017, CI includes
zero): the 15% overlap is unproven, and only 30% is reliably worse. The dense half of A2-A4 costs
about 35 minutes per re-embedding and was not measured, so those are BM25-only statements.

Each ablation changes **one factor**, others fixed at the current default. Output: one table per
ablation (metric ± CI, latency, cost) in `reports/`, and a summary in the README.

## 7. Run artefacts and reproducibility

Every run writes `reports/runs/<run_id>/`:

```
config.json      resolved settings, preset name, git SHA, dirty flag
manifest.json    index manifest (embedding model, chunker, corpus hash), gold version, universe
prompts.json     prompt versions actually used
results.jsonl    per-question: retrieved ids, answer, citations, tool trace, scores, usage, latency
summary.md       headline table with CIs + per-type breakdown + failure list
```

`finsight eval report <run_id>` renders the summary; `finsight eval compare A B` produces the
paired comparison. Results in the README are copied from `summary.md`, never typed by hand.

## 8. Error analysis (the part that produces improvements)

Every run auto-buckets failures: *retrieval miss* (gold section not in top-k), *retrieval hit but
generation wrong*, *numeric mismatch*, *wrong period*, *wrong company*, *unsupported claim*,
*over-abstention*, *should-have-abstained*. The top buckets, with concrete examples, are written
up in `docs/` after each phase — this is what turns a benchmark into an engineering feedback loop.

## 9. CI regression gate

A **retrieval-only** evaluation on a small committed fixture corpus (a few real filing excerpts,
~25 questions) runs on every PR with a deterministic embedder and BM25. The PR fails if section
Recall@8 drops more than a configured margin versus the committed baseline. It catches chunking or
fusion regressions without network, GPU or API cost.
