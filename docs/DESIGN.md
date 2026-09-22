# FinSight — Design Document

> **Status:** Living document. All phases are implemented; where the build departed from this design
> (and why) is recorded in [ROADMAP](ROADMAP.md#outcome-and-deviations-from-the-plan) and the ADRs. Decisions with alternatives considered live in
> [`docs/adr/`](adr/). Companion docs: [ARCHITECTURE](ARCHITECTURE.md) ·
> [DATA](DATA.md) · [EVALUATION](EVALUATION.md) · [ROADMAP](ROADMAP.md) ·
> [LIMITATIONS](LIMITATIONS.md).

---

## 1. Problem

Answering a question like *"How did Apple's gross margin change over the last three years, and
what did management say drove it?"* takes an analyst 30–60 minutes: find three 10-Ks, locate the
income statement and MD&A, copy numbers into a spreadsheet, compute the ratio, then read prose
to find the explanation.

A general-purpose chatbot fails at this in predictable ways:

| Failure | Why it happens | Consequence in finance |
|---|---|---|
| Invented or stale numbers | Parametric memory, no source of truth | Wrong decisions; unusable in a professional setting |
| Bad arithmetic | LLMs approximate calculations | A margin that is off by a point is a different story |
| No provenance | Answer is not tied to a document | Nothing to audit; cannot be trusted or corrected |
| Wrong period | Fiscal years ≠ calendar years; restatements | Silently comparing FY2024 to a different window |
| Confident answers to unanswerable questions | No abstention behaviour | Advice-like output with no basis |

**FinSight's thesis:** split the problem by *what kind of truth each part needs*.

* **Numbers** are facts → come from **structured XBRL data** through **deterministic code**.
  The model never does the arithmetic and never "remembers" a figure.
* **Explanations** are prose → come from **retrieval over the filing text**, with every claim
  **cited** to a passage the user can open.
* **Everything is measured** → a reproducible evaluation harness decides every design choice.

## 2. Goals and non-goals

**Goals**

1. Answer factual, numeric, comparative and qualitative questions about US public companies
   from SEC filings, with **verifiable citations** and **numerically correct** figures.
2. **Abstain** when the evidence is insufficient or the request is out of scope (e.g. "should I
   buy this stock?") rather than guessing.
3. Be **evaluated rigorously**: retrieval quality, faithfulness, numeric accuracy, abstention,
   latency and cost — with confidence intervals and ablations.
4. Be **production-shaped**: typed, tested, configurable, observable, containerised, CI-gated.
5. Be **reproducible** on a laptop from public data in under an hour.

**Non-goals**

* Investment advice, price targets or trading signals.
* Real-time market data or intraday analysis.
* Non-US filers, non-SEC documents, or scanned PDFs (v1).
* Training or fine-tuning a foundation model (we *evaluate* embedding/reranker choices instead).

## 3. Users and questions

Primary user: an **analyst** (equity research, corporate finance, or a data analyst supporting
one) who needs fast, checkable answers. Secondary: a recruiter / hiring manager reading the repo,
who should find design rationale, evidence and tests within minutes.

### 3.1 Question taxonomy

The taxonomy is a first-class concept (`QueryType` in `core/schemas.py`): it drives routing and
lets evaluation report accuracy **per question type**, which exposes weaknesses that an average
would hide.

| Type | Example | Primary source | Mechanism |
|---|---|---|---|
| `fact_lookup` | "Who is Alphabet's auditor?" | 10-K text | Hybrid retrieval → cited answer |
| `numeric` | "NVIDIA's FY2024 revenue?" | XBRL facts | `get_financial_metric` tool |
| `computed_metric` | "JPMorgan's ROE in 2023?" | XBRL facts | Tool + deterministic calculator |
| `trend` | "How has Microsoft's operating margin moved since 2021?" | XBRL series (+ text for *why*) | Tool series + retrieval |
| `comparison` | "Compare AMZN and WMT gross margin" | XBRL (multi-company) | Parallel tool calls |
| `qualitative` | "What supply-chain risks does Tesla cite?" | Item 1A / 7 text | Retrieval + synthesis |
| `change_detection` | "What's new in Exxon's risk factors vs. last year?" | Item 1A (2 filings) | Risk-diff tool |
| `out_of_scope` | "Should I buy Apple?" | — | Guardrail: decline + redirect to facts |

## 4. Design principles

1. **Numbers come from tools, prose comes from retrieval.** No figure in an answer may exist
   unless it is (a) returned by a tool call or (b) quoted from a cited passage. A post-hoc
   *numeric consistency check* enforces this (§8.4).
2. **Every claim is traceable.** Citations are validated: each `[S#]` must resolve to a real
   retrieved chunk, and the UI shows the exact source passage.
3. **Abstain over guess.** Insufficient evidence is a *correct* output and is scored as such.
4. **Evaluation drives design.** If a component cannot be shown to help on the gold set, it does
   not ship as the default (this includes fashionable ones: contextual-LLM headers, agents).
5. **Deterministic by default.** Same inputs → same chunk ids, same index, same ranking.
   Randomness (LLM sampling) is isolated and logged.
6. **Swap-friendly seams.** Embedder, vector store, sparse index, reranker and LLM sit behind
   small protocols; tests use fakes, so CI needs no network, GPU, model download or API key.
7. **Boring infrastructure.** Embedded stores (DuckDB, Qdrant local, BM25 in-process) mean zero
   services to run, while the interfaces match what a production deployment would use.
8. **Secure and polite by construction.** SEC rate limit enforced in one place; retrieved text
   is treated as untrusted data; no secrets in settings, logs or run artefacts.

## 5. System overview

Two planes; full diagrams in [ARCHITECTURE](ARCHITECTURE.md).

**Offline (build) plane** — run by `finsight ingest` / `finsight index`
`EDGAR → raw HTML + manifest → parse → sections → chunks(+headers) → embeddings + BM25 index`
and, in parallel, `EDGAR companyfacts → normalised facts → DuckDB`.

**Online (query) plane** — run per question
`question → analysis (tickers, years, type) → route → { retrieval | tools | both } →
context assembly → Claude → citation + numeric validation → Answer`.

## 6. Data

Three sources, all public and free (details, schemas and gotchas in [DATA](DATA.md)):

| Source | Use | Notes |
|---|---|---|
| EDGAR filings (10-K first; 10-Q optional) | Text corpus for retrieval | Rate limit 10 req/s and mandatory `User-Agent` |
| XBRL `companyfacts` | Structured numbers | Source of truth for every figure |
| Daily prices (optional) | Context only (e.g. drawdown) | Never used to give advice |

**Universe** (`configs/universe.yaml`): 12 companies × FY2021–2025 10-Ks = 60 filings. Chosen
for sector diversity, four different fiscal year ends, and two banks whose statements lack
gross profit — each forces a real engineering problem rather than a toy demo.

## 7. Retrieval design

### 7.1 Chunking

* **Section-aware**: split into canonical Items first; a chunk never crosses an Item boundary.
  Item identity is metadata, enabling filters ("Item 1A only") and per-section evaluation.
* **Token-budgeted** (default 400 tokens, 15 % overlap), respecting paragraph edges.
* **Tables are separate chunks**, serialised to markdown with caption and units ("in millions"),
  because a table torn across chunk boundaries is meaningless.
* **Deterministic ids**: `sha256(accession|item|ordinal|text)[:20]` → idempotent re-indexing.

### 7.2 Contextual headers

Each chunk's *indexed* text is prefixed with `Apple Inc. (AAPL) | 10-K FY2024 | Item 7 — MD&A`.
Filings are highly repetitive across companies and years; without the header a chunk about
"gross margin decreased due to…" is nearly indistinguishable between issuers. An LLM-generated
"situating" sentence is a further optional variant that is **evaluated, not assumed**.

### 7.3 Hybrid retrieval

Dense embeddings capture paraphrase ("cost of goods" ≈ "cost of sales"); BM25 captures exact
tokens (`$391.0 billion`, `ASC 606`, `Item 1A`, product names) that dense models blur. We run
both and fuse with **Reciprocal Rank Fusion** (`score = Σ 1/(k + rank)`, k = 60): rank-based, so
no calibration between incomparable score scales (cosine vs. BM25). See ADR-0002.

### 7.4 Reranking and diversification

A cross-encoder rescoring of the fused top-N trades ~100–300 ms for precision. A per-filing cap
(default 4 chunks) prevents one long section crowding out other years/companies, which matters
for comparison questions. Both are switchable and ablated.

### 7.5 Metadata pre-filtering

Query analysis extracts tickers, fiscal years, form type and Item hints via an alias table and
regex first (fast, deterministic, free); an LLM structured-output fallback handles the rest.
Filters are applied **inside** the vector store and BM25 index (pre-filter), not after top-k.

## 8. Generation design

### 8.1 Grounding contract (system prompt, versioned)

The model is instructed that (1) it may use only the provided sources and tool results,
(2) each factual sentence needs a citation `[S#]`, (3) numbers must be copied from a source or
tool result, never computed mentally, (4) if the sources do not answer the question it must say
so, (5) it does not give personalised investment advice. Prompts carry a version id that is
stored on every `Answer`, so evaluation results are attributable.

### 8.2 Context assembly

Chunks are de-duplicated, ordered (by filing then position, so the model reads coherent
passages), labelled `[S1]…[Sn]`, and packed under a token budget. The stable system prompt and
tool definitions form a **cacheable prefix** (prompt caching); volatile content follows it.

### 8.3 Guardrails

* **Scope**: advice / prediction requests are declined with a pointer to relevant facts.
* **Prompt injection**: retrieved text is wrapped as data and the model is told never to follow
  instructions found inside sources. Filings are a semi-trusted corpus, but the pipeline is
  designed as if they were not.
* **Abstention**: no retrieved evidence above a relevance floor → answer "not found in the
  covered filings" without calling the generator at all (cheaper and safer).

### 8.4 Post-generation validation

1. **Citation check** — every `[S#]` maps to a real source; uncited factual sentences are flagged.
2. **Numeric consistency check** — every number in the answer must match (within rounding) a
   value in the tool results or a cited passage. Failures trigger one repair attempt, then a
   downgrade to "unverified" in the response metadata.
3. **Stop-reason handling** — `max_tokens` (truncation) and `refusal` are surfaced explicitly,
   never returned as if they were normal answers.

## 9. Agent design (Phase 6)

A bounded tool-use loop over Claude. Tools are deterministic and typed:

| Tool | Purpose |
|---|---|
| `search_filings(query, tickers?, years?, items?)` | Hybrid retrieval → cited passages |
| `get_financial_metric(ticker, metric, periods)` | XBRL series from DuckDB |
| `compute_ratio(ticker, ratio, periods)` | Ratio with inputs and formula returned |
| `compare_companies(tickers, metric, period)` | Peer table + percentile ranks |
| `get_risk_factor_changes(ticker, year_a, year_b)` | Added / removed / reworded risks |
| ~~`get_price_history`~~ | *Not implemented* (see ROADMAP: unverifiable offline; no price views by design) |

Design rules: steps and token **budgets** (default ≤ 8 steps); **parallel** tool calls where
independent; tool errors returned as `is_error` results so the model can recover; a full
**trace** is captured for the UI and for evaluation. The agent is compared against the
single-shot RAG baseline — it ships as default only if it wins on the gold set (ADR-0008).

## 10. Analytics layer

Pure functions, no I/O: margins, ROE/ROA/ROIC, liquidity and leverage ratios, working-capital
days, FCF, YoY/CAGR, DuPont (3- and 5-step) with driver attribution, z-score anomaly flags, peer
percentiles. Two "data-science" features go beyond retrieval: **risk-factor change detection**
(paragraph alignment + embedding similarity across years) and **tone analysis** of MD&A with the
Loughran–McDonald finance lexicon. Every function is unit-tested against hand-computed values,
and property-tested (e.g. DuPont factors must multiply back to ROE).

## 11. Evaluation strategy (summary — full method in [EVALUATION](EVALUATION.md))

* **Gold set** (~120 questions, versioned, human-verified) stratified by `QueryType`.
* **Retrieval**: Recall@k, MRR, nDCG@k at chunk and section level.
* **Generation**: faithfulness, answer correctness, citation precision/recall, abstention
  accuracy; **numeric accuracy** with relative tolerance.
* **Systems**: latency p50/p95, tokens, and cost per query.
* **Statistics**: bootstrap confidence intervals and paired tests; LLM-judge agreement with human
  labels is measured on a subset, not assumed.
* **Ablations**: dense vs BM25 vs hybrid vs hybrid+rerank · chunk size · embedding model ·
  headers on/off · single-shot RAG vs agent.
* **CI regression gate**: a small retrieval-only fixture eval runs on every PR.

## 12. Non-functional requirements

| Area | Target / approach |
|---|---|
| Latency (targets, to be measured) | Single-shot p50 < 6 s, p95 < 15 s; agent p50 < 20 s. Streaming for perceived latency. |
| Cost | Tracked per request (tokens × price, cache-aware); budget guard in the agent and eval runner. Model id per *role* is configurable so eval can trade cost for speed deliberately. |
| Reliability | Retries with backoff on 429/5xx (SEC and Anthropic); idempotent ingestion; index manifest prevents mixing incompatible embeddings. |
| Security | No secrets in settings/logs/artefacts; `gitleaks` pre-commit; retrieved text treated as untrusted; API input validated by Pydantic. |
| Observability | Structured logs with a `trace_id` spanning retrieval → generation → tools; per-stage timings; usage in every response. |
| Reproducibility | Pinned lower bounds, deterministic ids, run directories store config + git SHA + prompt version + index manifest. |
| Testability | Fakes for embedder / vector store / LLM; unit tests hermetic; `network` / `llm` markers isolate costly tests. |
| Compliance | Honours SEC fair-access policy; clear "not investment advice" disclaimers; provenance for all outputs. |

## 13. Technology choices

| Concern | Choice | Why (alternatives in ADRs) |
|---|---|---|
| Language / tooling | Python ≥ 3.11, Ruff, mypy `--strict`, pytest | Ecosystem fit; strict typing catches schema drift early |
| Domain models | Pydantic v2 (frozen, `extra=forbid`) | Validation at boundaries; same models for API, config and eval |
| LLM | Anthropic Claude via official SDK; default `claude-opus-5`. A second `LLMClient`, Ollama (free, local, no API key), runs the same agent for $0 (`--llm ollama`) | Strong tool use, long context; provider behind a thin wrapper (ADR-0006, extended by ADR-0011) |
| Embeddings / rerank | FastEmbed (ONNX): `bge-small-en-v1.5`, MiniLM cross-encoder | No PyTorch; runs on a laptop CPU; swappable (ADR-0005) |
| Vector store | Qdrant (embedded now, server in compose) | Payload pre-filtering; same API local and prod (ADR-0004) |
| Lexical index | `bm25s` | Fast, dependency-light BM25 with persistence |
| Fact store | DuckDB + Parquet | Analytical SQL (window functions) with zero ops (ADR-0007) |
| HTML parsing | `lxml` / BeautifulSoup | Robust to malformed SEC HTML |
| API / UI | FastAPI (+SSE) / Streamlit | Typed OpenAPI service; fastest route to an analyst-facing UI |
| CLI | Typer + Rich | Discoverable commands, good output |
| Logging | structlog | JSON in prod, pretty in dev, context-bound `trace_id` |
| CI | GitHub Actions matrix 3.11–3.13 | Lint, types, tests on every PR |

## 14. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Section splitting fails on unusual filings | High | Layered heading detection; TOC guard; fall back to whole-document chunks flagged `item="UNK"`; measure section-detection rate per filing in Phase 2 |
| XBRL tag inconsistency across issuers | High | Ordered fallback map per metric + per-company overrides; unit tests per company; report coverage matrix |
| Fiscal-period misalignment | High | Derive fiscal year from period end + company FYE, not from `fy`; explicit test cases for Jan/Jun/Sep FYE |
| LLM judge bias / drift | Medium | Separate judge model; human-labelled calibration subset; report agreement (Cohen's κ) |
| Gold-set leakage / overfitting to it | Medium | Frozen dev/test split; test set touched only for reported results |
| Cost blow-up in evaluation | Medium | Response cache keyed by (prompt, model, params); cost budget in runner; cheaper role models configurable |
| Prompt injection via filing text | Low–Med | Data-wrapping + instruction hierarchy; adversarial test cases in the gold set |
| SEC throttling / ban | Low | Central limiter at 8 req/s, mandatory UA, on-disk cache |

## 15. Open questions (to resolve by measurement)

1. Does an LLM-generated situating sentence beat the free deterministic header enough to justify
   its indexing cost? *(Phase 3 ablation)*
2. What chunk size maximises Recall@8 for tables vs. prose? *(Phase 3 ablation)*
3. Does the agent beat single-shot RAG on `qualitative` questions, or only on numeric ones?
   *(Phase 6 vs. Phase 4 baseline)*
4. Is a small cross-encoder enough, or does a larger reranker pay for its latency? *(Phase 5)*
