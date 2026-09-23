# FinSight — Portfolio Guide

How to present this project: what you can claim, what you must not, and how to defend it in an
interview. Every number below is traceable to a file in the repo (the source is in brackets). If a
number changes, change it here too.

> **The rule that makes this project credible:** claim only what is measured, say what is not, and be
> able to explain every line you show. A reviewer who finds one overstated number stops trusting the rest.

---

## 1. The 30-second pitch

> FinSight is a financial-research assistant over SEC 10-K filings. Its core idea is that different parts
> of an answer need different kinds of truth: **numbers come from SEC XBRL structured data through tested
> tools** (the model never does the arithmetic), **explanations come from hybrid retrieval** (dense +
> BM25, fused with RRF) with validated citations, and everything is judged by an **evaluation harness with
> confidence intervals**. It abstains when evidence is missing and declines investment advice. I built the
> data pipeline, the retrieval stack, the tool layer, the API and UI, and the evaluation — and I can tell
> you where it is weak, with numbers.

---

## 2. Resume bullets (measured numbers only)

Pick 3-5 for the role; do not use all of them.

**Data engineering / analysis**
* Built an idempotent SEC EDGAR ingestion pipeline (rate-limited, cached, checksummed) covering 12
  companies × 5 fiscal years (60 10-Ks, 9,146 XBRL facts) with fiscal-calendar logic for non-calendar and
  52/53-week fiscal years. [`docs/DATA.md`, README]
* Validated the fact store with automated data-quality checks: the balance-sheet identity held with
  **0 violations across 278 checked periods**, and **94.4% raw availability with 0 unexplained gaps**
  (every absent value is n/a or has a written reason); spot-checked against 14 figures published in the
  10-Ks. [`docs/coverage.md`, `tests/integration/test_published_figures.py`]
* Found and fixed a silent data bug (a query joining zero rows reported "0 violations") and added a
  count of periods checked to every check so an empty join cannot pass. [`docs/ERROR_ANALYSIS.md` §3 row 5, `CLAUDE.md`]

**ML / retrieval engineering**
* Implemented section-aware chunking (23,221 chunks), dense (bge-small, ONNX) + BM25 retrieval fused with
  Reciprocal Rank Fusion, with metadata pre-filters that add **+0.20 recall@8** on held-out companies
  (0.367 → 0.567). [`reports/RESULTS.md`, `docs/ERROR_ANALYSIS.md` §1]
* Ran ablations with paired-bootstrap confidence intervals: a contextual chunk header lifts BM25 recall@8
  from 0.586 to 0.897 (CI of the difference [+0.155, +0.483]); a cross-encoder reranker improved ordering
  but not recall at ~16× the latency, so it is opt-in. [`reports/ablation_A4_bm25_header_dev.md`, `reports/ablation_A1_dev.md`]
* Diagnosed a generalisation gap (recall@8 0.90 dev vs 0.57 held-out test) down to its cause — all 9
  misses retrieved the right filing but the wrong section (bank filing layout, MD&A ambiguity) — and
  deliberately did **not** tune against the test failures. [notebook `04`, `docs/ERROR_ANALYSIS.md` §1]

**Evaluation / LLM systems**
* Built an evaluation-first harness (retrieval metrics, rule-based numeric accuracy, abstention F1,
  bootstrap CIs, paired tests, frozen company-level dev/test split). Routing numeric questions to XBRL
  tools scored **0.94 vs 0.21** for passage retrieval on held-out companies (paired difference +0.73,
  95% CI [0.59, 0.88]). [`reports/RESULTS.md`, README "What the evidence says"]
* Stress-tested my own result with a natural-phrasing probe: the tool router fell to **0.69** on 38
  differently worded questions and exposed a hole in the advice guardrail (all 4 naturally phrased advice
  requests slipped through); fixed it and reported the re-run as in-sample. [`reports/RESULTS.md`, `docs/ERROR_ANALYSIS.md` §3b]
* Added a second `LLMClient` (ADR-0011) so the tool-using agent could be run live for free against a
  local model (Ollama), no API key: found and fixed two general tool-robustness bugs from real failures,
  set `temperature=0`/`seed=0` after the same question returned a different tool call across runs, and
  reported the honest paired result - **not a clean win**: significantly *below* the router on the
  templated test split (0.735 vs 0.941, driven by ratio/comparison questions), statistically tied on
  natural phrasing, and a clear win over the extractive baseline on both. Also measured citation hygiene
  (valid citation, no flagged claim) at 0-8% for the free agent vs 37-100% for the two no-LLM baselines -
  the accuracy numbers alone overstate how often it actually grounds its answer.
  [`docs/adr/0011-local-free-llm-provider.md`, `docs/ERROR_ANALYSIS.md` §3c, `reports/RESULTS.md`]

**Software engineering**
* ~9.8k lines of typed Python (mypy strict, ruff clean) with ~6.9k lines of tests (~785 hermetic tests
  as of this writing - these three numbers move with every commit, so treat them as illustrative and
  run `make test` for the exact current count rather than trusting a hardcoded figure here again; a
  Hypothesis property test found a real chunker bug), CI, Docker files, and import-linter
  architecture contracts each verified to fail on an injected violation. [`make check`, `pyproject.toml`]
* FastAPI service with SSE streaming, rate limiting and a Streamlit UI; load-tested on one worker: ~80
  req/s for database-backed endpoints, ~6-7 req/s for hybrid-retrieval questions, 0 errors. [`reports/load_test.md`]

---

## 3. What you must NOT claim

| Do not say | Why | Say instead |
|---|---|---|
| "Claude-powered agent achieves X" | `--llm claude` specifically has never been run (no API key) - there is no Claude accuracy, cost or latency number | "The agent architecture is measured live against a free local model (ADR-0011); Claude itself is unrun" |
| "The LLM agent beats the router" (bare) | On the natural-phrasing probe the free local agent is statistically indistinguishable from the router (paired diff -0.038, CI crosses zero) - it *does* beat the extractive baseline decisively (+0.385, CI [0.19, 0.58]) | "Indistinguishable from the router, clearly ahead of the extractive baseline - both on a 3B model" |
| "The agent is grounded / doesn't hallucinate" | Only 0-8% of the free local model's answers have a valid citation and no flagged claim (vs the extractive baseline's 96-100%); it sometimes cites a source that was never retrieved, or paraphrases from training-data familiarity instead of the retrieved passage - caught as warnings, not prevented | "Citation and numeric validators catch it and flag it; they do not yet block it, and citation hygiene is currently poor (0-8%) with this free model" |
| "94% accuracy" (bare) | It is a templated-question result; natural phrasing gave 0.69 | "0.94 on templated held-out questions, 0.69 on natural rewrites" |
| "Production-ready" / "scales to..." | One process, laptop, no LLM calls in the load test; Docker was verified to build and serve one user, not under load or at multi-worker scale | "Load-tested on one worker (no Docker); Docker verified to build/serve correctly, not load-tested; scaling path documented" |
| "Retrieval recall of 0.90" | That is dev; the held-out figure is 0.57 (n=15, wide interval) | Quote both, and the reason |
| "Human-verified gold set" | `gold_v1` is auto-derived; `gold_v2_draft` labels are unverified | "Programmatically derived, correct by construction for numbers" |
| "Chunk overlap of 15% was tuned" | On BM25, no overlap scored as well or better (difference not significant) | "Chose 15% by convention; the ablation did not separate it from 0%" |
| Any dense-model ablation claim for chunking | Only BM25 was re-run; re-embedding costs ~35 min per config | "BM25-only" |

---

## 4. Interview talking points (each is a story with a number)

1. **"Numbers should come from tools."** Give the paired result (0.94 vs 0.21, +0.73 [0.59, 0.88]) *and*
   volunteer the natural-phrasing drop to 0.69. Interviewers reward the person who attacks their own result.
2. **Fiscal-calendar and XBRL traps.** SEC `fy` labels describe the *filing*, not the period; restatements
   duplicate facts; Q4 is not reported (derive FY − 9M); 52/53-week years break naive duration checks.
   Walk one example end to end. [`docs/DATA.md` §4]
3. **A test that could not fail.** The identity check once reported 0 violations because it joined zero
   rows. The fix (report how many periods were checked) generalises: *a check must say how much it checked*.
4. **Generalisation gap.** 0.90 dev vs 0.57 test; every miss was right filing, wrong section; tuning to
   the test failures would have "fixed" it and taught to the test. What you did instead and why.
5. **Advice guardrail hole.** Templated questions could not have found it; natural phrasing did. Then the
   honest part: the fix is in-sample.
6. **Architecture as code.** import-linter contracts, and that the first draft found a real package cycle
   (`RetrievalFilters` moved to `core`). Also that you proved the contracts fail when violated.
7. **Reranker rejected on evidence.** Better nDCG, no recall gain, 16× latency → opt-in default.
8. **Load test finding.** Throughput peaks at concurrency 4 then *falls*; say it is a hypothesis
   (serialised DuckDB critical section + one process) that you did not profile.
9. **Getting the agent running with zero budget.** When a paid API key wasn't available, you didn't
   leave the agent untested - you added a second `LLMClient` behind the existing seam (ADR-0006's
   seam existing was itself a design choice paying off) for a free local model via Ollama, found and
   fixed two real tool-robustness bugs from it (a stringified array argument, a ratio requested
   through the wrong tool), and reported the honest, *mixed* result: significantly below the router
   on templated questions, tied on natural phrasing, ahead of the extractive baseline on both - and
   separately, that citation hygiene (0-8%) is far worse than the accuracy numbers alone suggest.
   That is a stronger story than a suspiciously clean number would have been, and volunteering the
   citation-hygiene gap without being asked is exactly the kind of thing worth doing out loud.

---

## 5. You must be able to explain and modify this code

AI assistance built much of this repository. That is fine to say plainly — but the project only helps you
if *you* can explain it without the code open and change it live. Work through these until each takes
under five minutes:

* Trace one numeric question through `ToolRouterAgent` → `get_financial_metric` → `FactStore`
  (`agent/router.py`, `agent/tools.py`, `ingestion/xbrl/store.py`) and say where the number's provenance is recorded.
* Explain RRF with k=60 on paper, then point at it in `retrieval/hybrid.py` and say why it needs no score normalisation.
* Explain the "containing fiscal year" rule (`core/fiscal.py`) using a January-year-end company.
* Explain how the bootstrap CI and the paired bootstrap differ and why the paired one is used for comparisons (`evaluation/stats.py`).
* Change something live: add a canonical metric in `ingestion/xbrl/concepts.py` and watch the tests and the coverage report react; or add an advice phrasing to the guardrail with a matching false-positive test.
* Say what would break first at 10× the corpus (embedded Qdrant limit, single-process DuckDB, re-embedding time).

**Questions to expect, honest short answers**

* *"Did you run it with Claude?"* No. The agent is built and unit-tested against a scripted model; I have not
  run it live, so I make no LLM accuracy claim. The comparison I measured uses a deterministic router.
* *"How do you know the gold labels are right?"* Numeric ones are derived from the same XBRL store the tools
  read (checked against 14 published figures); retrieval labels are section-level and template-derived; a
  human-verified set (`gold_v2`) is the stated next step.
* *"Why not fine-tune / use a bigger embedding model?"* Not measured (A5 not run). Say so; do not guess.
* *"What is the weakest part?"* Retrieval on generic questions (0.57 held-out), the router's dependence on
  phrasing (0.69 natural), and no live LLM evaluation.

---

## 6. Which parts to show for which role

| Role | Lead with | Open |
|---|---|---|
| Data analyst | Data-quality checks, coverage matrix, EDA notebooks | `docs/coverage.md`, notebooks `01`, `02` |
| Financial analyst | Ratios, DuPont, peer table, fiscal alignment, XBRL semantics | notebook `03`, `analytics/`, `docs/DATA.md` |
| Data scientist | Ablations, paired statistics, error analysis, the natural-phrasing probe | `reports/RESULTS.md`, notebook `04`, `docs/ERROR_ANALYSIS.md` |
| AI/ML engineer | Hybrid retrieval, tool layer, guardrails, validators, the agent design and its offline test strategy | `retrieval/`, `generation/`, `agent/`, ADRs 0002/0003/0009 |
| Backend / MLOps | API, load test, architecture contracts, CI | `api/`, `reports/load_test.md`, `pyproject.toml` |

---

## 7. What is left (and who has to do it)

Nothing here can be done without your accounts, machine or judgement:

1. **Run `--llm claude` once with an API key** (`ANTHROPIC_API_KEY`), then re-run the same evaluations
   already run for free (`finsight eval run --system agent --llm claude ...`) and add the numbers
   alongside the free-local-model ones already in the README - do not replace them, the comparison
   between a 3B free model and Claude is itself a finding. Until then those cells say "not run" — leave
   them that way.
2. **Push to GitHub** and confirm CI is green; replace the `your-username` placeholders.
3. **Human-verify `gold_v2`** (`data/eval/gold_v2_draft.jsonl`): this is what turns the probes into a clean holdout. Procedure and worksheet: `docs/GOLD_V2_REVIEW_CHECKLIST.md` / `reports/gold_v2_review_worksheet.md` - prepared, not completed; no label has been approved.
4. ~~Build the Docker image (`docker compose up`) on a machine with Docker; fix whatever breaks~~
   **Done**: built and run live end to end (three healthy containers, UI opened in a browser, a
   real query answered through host Ollama, container→host reachability confirmed) once Docker
   Desktop became available - two real bugs found and fixed along the way (a hardcoded host-port
   collision with an unrelated local project, and a Qdrant client/server version mismatch),
   ERROR_ANALYSIS.md §3h. `make docker-smoke` reruns the same check.
5. **Record a 60-second demo** (screen capture of `finsight ui`).
6. ~~Wire a provider choice through the API/UI~~ **Done** (ADR-0012): `finsight serve --llm ollama`
   (or `FINSIGHT_LLM_PROVIDER=ollama`) serves the agent from the free local model with no API key;
   verified live end to end (server, `/readyz`, `/v1/query`, and the Streamlit Ask page).
