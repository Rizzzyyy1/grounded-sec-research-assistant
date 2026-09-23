# FinSight — Roadmap

Each phase ends with a **demonstrable, tested increment** and an **exit criterion** that is
checkable, so progress is never "mostly done". Effort is a rough guide for one person.

| Phase | Theme | Status |
|---|---|---|
| 0 | Foundations | ✅ Done |
| 1 | Data layer | ✅ Done |
| 2 | Parsing & chunking | ✅ Done |
| 3 | Indexing & retrieval | ✅ Done |
| 4 | Grounded generation (RAG baseline) | ✅ Done |
| 5 | Evaluation harness + baseline results | ✅ Done |
| 6 | Agent & analytics | ✅ Done |
| 7 | API & UI | ✅ Done |
| 8 | Hardening, ablations, packaging | ✅ Done |


## Outcome and deviations from the plan

All eight phases are built, tested and exercised on real data. The plan below is kept as written; what
changed, and why:

| Plan | What happened |
|---|---|
| Gold set: ~120 human-verified, LLM-assisted questions | **147 automatically derived questions** (numeric answers correct by construction). Human verification and LLM-assisted phrasing remain `gold_v2` work — documented in [EVALUATION §2.1](EVALUATION.md#21-what-gold_v1-actually-is) |
| Agent evaluated against the RAG baseline | **Run live** (ADR-0011) against a free local model (`--llm ollama`, no API key): dev, test and the natural-phrasing probe, paired against the router and the extractive baseline (ERROR_ANALYSIS 3c). `--llm claude` specifically remains unrun (no API key) |
| `get_price_history` tool | **Not implemented**: a live market-data dependency that cannot be verified offline, and the assistant gives no price views |
| Reranker as the default | **Reranker is opt-in** after ablation A1 (ADR-0002 amendment) |
| `finsight data coverage` | Shipped as `finsight coverage`; plus `known_gaps`, raw-availability and identity-coverage reporting |
| Load test, import-linter | **Done**: [`reports/load_test.md`](../reports/load_test.md) (one worker, no LLM) and six import-linter contracts run by `make arch` and CI, each shown to fail on an injected violation. The first draft found a real `indexing` <-> `retrieval` cycle (`RetrievalFilters` moved to `core`) |
| Ablations | A1, A2/A3 (BM25 half), A4 (BM25 half), A7 run; A5, A6-beyond-MiniLM, A8-with-LLM, A9 not run (need downloads or an API key) |
| Docker | Files written and statically tested; **not built or run** (no Docker on the build machine) |
| 10-Q / 8-K, FY2026 successor-CIK union, human gold set, running Claude specifically | Future work (below) |

---

## Phase 0 — Foundations ✅

**Delivered:** repo structure (90 typed, documented modules), `pyproject.toml` with dependency groups,
typed settings (env-driven, validated), domain schemas (frozen Pydantic), exception hierarchy,
structured logging, universe/preset loaders, Typer CLI (`version`, `config show`, `doctor`),
126 unit tests, Ruff + strict mypy clean, Makefile, pre-commit (incl. gitleaks), GitHub Actions
matrix CI, VS Code config, and the design / architecture / data / evaluation documents.

**Exit criterion:** `make check` passes on a fresh clone. ✅

## Phase 1 — Data layer (≈ 1 week) ✅

**Progress**

- [x] Fiscal-calendar logic (`core/fiscal.py`) — year/quarter from period end + company FYE; property-tested
- [x] Token-bucket rate limiter — verified in virtual time; SEC cap invariant property-tested
- [x] Response cache — ETag revalidation, immutable filings, atomic writes
- [x] `EdgarClient` — mandatory User-Agent, retries with `Retry-After`, limiter on every attempt
- [x] Filing index — follows older submission pages, derives fiscal periods, dedupes, `finsight filings`
- [ ] Filing downloader with manifests (sha256) — **next**
- [ ] `companyfacts` parser: fiscal alignment, restatement dedupe, Q4 derivation
- [ ] Canonical metric resolver with tag fallbacks
- [ ] DuckDB fact store + coverage matrix + data-quality checks
- [ ] Ingestion pipeline + `finsight ingest`

**Build:** `EdgarClient` (UA, token-bucket limiter, retries, ETag cache) · filing index ·
idempotent downloader with manifests · `companyfacts` parser (fiscal-year derivation, restatement
dedupe, Q4 derivation) · metric resolver with tag fallbacks · DuckDB fact store · ingestion
pipeline + `finsight ingest`.

**Tests:** recorded-response fixtures (`respx`) so tests are offline; property tests for
fiscal-period alignment; per-company tag-resolution tests (incl. JPM without gross profit).

**Exit:** `finsight ingest` fetches the 12-company universe; coverage matrix ≥ 95 % for non-bank
metrics with every gap explained; accounting-identity check passes; re-run is a no-op.

**Portfolio artefact:** `docs/coverage.md` + notebook `01_data_quality.ipynb` (data-analyst skills).

## Phase 2 — Parsing & chunking (≈ 1 week)

**Build:** HTML/iXBRL cleaner · Item splitter with TOC guard · table extraction & markdown
serialisation · section-aware chunker with deterministic ids · `chunks.parquet`.

**Tests:** golden-file tests on real excerpts; section-detection rate per filing as a regression
metric; property tests (chunks never exceed budget, never cross Items, reassemble to source).

**Exit:** ≥ 95 % of filings yield the expected core Items (1, 1A, 7, 7A, 8); every failure listed.

**Artefact:** notebook `02_corpus_eda.ipynb` — token distributions, section sizes, table share.

## Phase 3 — Indexing & retrieval (≈ 1–1.5 weeks)

**Build:** `Embedder` protocol (FastEmbed + hashing fake) · Qdrant store with payload indexes ·
`bm25s` index with finance-aware tokeniser · index builder + manifest · query analysis · dense,
sparse, RRF fusion, cross-encoder rerank, per-filing diversification · `Retriever` facade.

**Tests:** fakes for hermetic unit tests; RRF unit tests against hand-computed rankings; filter
correctness tests; one integration test with a real small model.

**Exit:** retrieval-only smoke eval on a 25-question fixture; ablation A1 runnable.

## Phase 4 — Grounded generation / RAG baseline (≈ 1 week)

**Build:** `LLMClient` (retries, streaming, caching, usage/cost, stop-reason handling) ·
versioned prompts · context assembly · citation parser/validator · guardrails · numeric
consistency check · `RagPipeline` · `finsight ask`.

**Tests:** fake LLM for deterministic pipeline tests; adversarial prompt-injection cases;
citation-validator property tests; a small set of `@llm` live tests.

**Exit:** `finsight ask "…"` returns a cited, validated answer with usage and latency.
*(Requires `ANTHROPIC_API_KEY` or `ant auth login`.)*

## Phase 5 — Evaluation harness + baseline (≈ 1.5 weeks)

**Build:** gold set v1 (§ EVALUATION) · retrieval / numeric / generation metrics · calibrated LLM
judge · runner with caching and cost tracking · bootstrap statistics · report generator · CI
retrieval regression gate · `finsight eval run|report|compare|ablate`.

**Exit:** baseline numbers with confidence intervals; ablations A1–A3, A7 complete; first error
analysis written up.

**Artefact:** README results table generated from `summary.md`; notebook `03_error_analysis.ipynb`.

## Phase 6 — Agent & analytics (≈ 1.5–2 weeks)

**Build:** ratio library, DuPont, trend/anomaly, peer comparison · risk-factor diff · tone analysis
(Loughran–McDonald) · tool schemas and handlers · bounded agent loop with trace · price context
tool.

**Tests:** analytics against hand-computed values; property tests (DuPont factors multiply to
ROE); agent tests with a scripted fake LLM; tool-error recovery cases.

**Exit:** ablation A8 (agent vs baseline) with CIs; the agent becomes default **only if it wins**.

**Artefact:** notebooks `04_peer_analysis.ipynb` (financial-analyst view) and
`05_risk_factor_drift.ipynb` (NLP view).

## Phase 7 — API & UI (≈ 1 week)

**Build:** FastAPI (`/v1/query`, SSE streaming, `/v1/companies/…`, health) · middleware (request
id, timing, rate limit) · Streamlit app: Ask (streaming + expandable citations + agent trace),
Company Explorer, Compare, Evaluation dashboard.

**Exit:** end-to-end demo from browser; OpenAPI schema published; 60-second demo GIF for the README.

## Phase 8 — Hardening, ablations, packaging (≈ 1 week)

**Build:** Dockerfiles + `docker compose` (ui, api, qdrant) · import-linter architecture check ·
load test (latency under concurrency) · remaining ablations A4–A6, A9 · cost/latency analysis ·
security review (input limits, injection tests) · final README, write-up, model-card style
limitations.

**Exit:** `docker compose up` gives a working system from a clean clone in < 15 minutes of work;
all headline numbers reproducible via one documented command.

---

## Definition of done (every phase)

1. `make check` green (lint, strict types, tests).
2. New behaviour has tests; new decisions have an ADR or a DESIGN update.
3. If retrieval/generation changed: evaluation re-run and results recorded.
4. README status table updated; nothing claimed that is not measured.

## Next steps (highest value first)

1. **Run `--llm claude`** on the gold set (`finsight eval run --system agent --llm claude`) and record the
   result *alongside* the free `--llm ollama` numbers already measured (ADR-0011) - do not replace them.
2. **`gold_v2`**: human-verified, naturally phrased, multi-source labels, fresh holdout. The naturally
   phrased draft already exists (`data/eval/gold_v2_draft.jsonl`, 38 questions, `provenance=draft`);
   what remains is the human verification pass — procedure and worksheet:
   [GOLD_V2_REVIEW_CHECKLIST.md](GOLD_V2_REVIEW_CHECKLIST.md) / `reports/gold_v2_review_worksheet.md`
   — prepared, not completed; no label has been approved.
3. Section-mapping override for filings like JPMorgan's; retrieval-score relevance floor for abstention.
4. Union the old and new XOM CIKs once FY2026 10-Ks exist.
5. ~~Wire a provider choice through `finsight serve`/`finsight ui`~~ **Done**: `finsight serve --llm
   {auto,claude,ollama}` (or `FINSIGHT_LLM_PROVIDER`), explicit and non-fallback by construction
   (ADR-0012). Verified live: `finsight serve --llm ollama`, `/readyz`, `/v1/query`, and the
   Streamlit Ask page all confirmed end to end with a real local model, zero cost.
6. ~~Give XBRL-tool answers a real citation, not just passages~~ **Done**: `agent/tools.py::_register_fact`
   + `Citation.kind` (ERROR_ANALYSIS.md 3d). Natural-probe citation hygiene 7.7% → 22.2%. What's left:
   (a) re-measure `gold_v1` dev/test citation hygiene against this fix - not yet done, see
   `scripts/collect_results.py`'s "Citation hygiene" caveat; (b) extend the filing catalogue so a
   "latest restated value" fact can be cited even when its own accession was never downloaded (13.3%
   of revenue facts checked cannot be cited today, concentrated in 4 companies' most recent years) -
   needs a live EDGAR fetch per missing accession, not done here.

## Stretch ideas

Earnings-call transcripts · 10-Q/8-K support · Text-to-SQL over the fact store as an extra tool ·
fine-tuned embedding model with hard negatives from the gold set · multi-turn memory ·
Anthropic Batch API for cheaper evaluation runs.
