# FinSight — Error Analysis

Two kinds of error matter: **system errors** (where the assistant is wrong or weak, measured on the
gold set) and **engineering errors** (bugs found while building it, each with a regression test).
Both are written up here because the second kind is how the first kind stays honest.

## 1. Retrieval errors (section level)

Default configuration (hybrid, rerank off), all 44 retrieval questions from `gold_v1`:

| Outcome | dev | test |
|---|---|---|
| Hit (a gold-section chunk in the top 8) | 26 | 9 |
| Right filing, **wrong section** | 3 | 6 |
| Wrong filing / no results | 0 | 0 |

**Every miss is "right filing, wrong section".** Query analysis found the correct company and fiscal
year in 44/44 cases and the metadata pre-filters did their job (they are worth +0.20 recall@8 on test:
0.367 without → 0.567 with). The failures are about *which Item* inside the filing:

| Cause | Examples | Nature |
|---|---|---|
| **Filing layout** — JPMorgan files its real MD&A, market-risk and legal content in an appended annual report under **Item 15**; its Items 3, 7, 7A are short cross-reference stubs (87% of JPM's text is under Item 15) | JPM legal proceedings FY2023, JPM market risk FY2025 | The retriever finds the *actual content*; the gold label says a stub. A **label artifact**, but also a real limitation for any Item-based filter |
| **Genuine ambiguity** — generic phrasing matches MD&A overview prose | "Describe Walmart's / Tesla's business" → Item 7; "main risk factors Walmart discloses" → Item 7; "new risks NVIDIA added" → Item 7 | MD&A legitimately discusses the business and its risks; single-Item gold is too strict |
| **Vocabulary overlap** across Items | J&J legal proceedings → Items 7/8 (litigation notes); Exxon market risk → Item 16 | Litigation and market-risk disclosures are repeated in the notes |

**What I did not do:** add Item-keyword filtering or boosting. It would fix these templated questions —
because the templates *name* the Item — and would look like progress while teaching to the test; a wrong
hint would also silently exclude the right passage for real queries. **Disclosure:** I looked at the test
failures for this analysis, so the test split is no longer a clean holdout. The recall@8 figure of 0.567
was recorded *before* the analysis (it is unchanged by it), but any further tuning must be judged on a new
split (`gold_v2`, with multi-source labels and human verification).

**Recommendations, in order of value:** (1) multi-source gold labels (any of several acceptable Items);
(2) a per-company section-mapping override for filings like JPM's; (3) a query-intent → Item *prior* used
as a soft signal and evaluated on a fresh split; (4) show the ablation on natural, human-written questions.

## 2. End-to-end errors (tool router, test split)

Router accuracy on the test split is 0.941 (32/34). Both misses are the same class — **answering an
unanswerable question**:

| Question | What happened |
|---|---|
| "What is Walmart's current share price today?" | The extractive fallback quoted a share-*repurchase* passage (shared words: "share") |
| "What did Exxon announce at its most recent press conference last week?" | Quoted a controls-and-procedures sentence ("last fiscal quarter") |

The RAG-extractive baseline fails the same way on dev (P&G "current share price", Walmart/Apple "how many
employees will hire next year"). This is the *dangerous* direction of abstention error (a false answer
rather than a false refusal). It is a property of a system with **no relevance floor and no ability to
judge sufficiency** — exactly what an LLM's `INSUFFICIENT_EVIDENCE` behaviour, or a calibrated retrieval
score threshold, is meant to provide. Not fixed in the baseline on purpose: it is the evidence that the
generative layer has something to add.

## 3. Engineering errors found while building (each has a regression test)

| # | Bug | How it was found | Fix / test |
|---|---|---|---|
| 1 | Mid-year quarters labelled with the *previous* fiscal year (snapped to the nearest year end) | Parametrised tests on Apple's Q1 | "Containing fiscal year" rule; `test_mid_year_periods_belong_to_the_open_fiscal_year` |
| 2 | Leap-day fiscal year end (`02-29`) rejected by the validator | Test of clamping logic | Validate against a leap year |
| 3 | **XOM resolved to a new holding-company CIK with zero 10-Ks; ingestion reported success** | Counting 55 filings instead of 60 | CIK pinning + zero-filings-is-a-failure (ADR-0010); `test_zero_filings_is_a_failure_not_silent_success` |
| 4 | `ingest --facts-only` wiped download paths/hashes (`INSERT OR REPLACE`), so `process` saw no filings | `process` returned 0 chunks | Merge-on-conflict upsert; `test_reregistering_a_filing_keeps_its_download_metadata` |
| 5 | **The accounting-identity check "passed" by joining nothing** (a metric never got defined; my own scripted patch silently matched no text) | Suspiciously clean result; reading the file | Report *periods checked*; `test_names_referenced_by_derivations_and_quality_checks_exist` |
| 6 | Parent equity ≠ total equity ≠ + mezzanine: 48 identity "violations" (Tesla, Exxon) | Real data | Three equity metrics; violations 48 → 0 over 278 periods |
| 7 | Hidden `ix:header` / script text leaked into paragraphs | Test with a hidden node in a leaf `<div>` | Drop non-visible nodes up front |
| 8 | `<br>` ignored: "a\<br\>b" → "ab" | Parser test | `<br>` becomes whitespace |
| 9 | Chunk = overlap tail + next paragraph could exceed the token budget | **Hypothesis** counter-example | Drop the overlap when it would break the budget |
| 10 | "Item 7 *of this report* describes…" matched as a heading | Heading tests | Titles must start with a capital/bracket |
| 11 | Walmart & Amazon headings are one-row **tables**; 10 filings had no Items | Detection rate 50/60 | Recognise single-row table headings → 60/60 |
| 12 | A citation placed *after* the full stop ("…2024. [S2]") flagged the claim as uncited | Pipeline tests on real-format text | Sentence splitter never breaks before a label |
| 13 | "fiscal **2024**" × 10⁶ read as $2.024 B and matched any ~$2 B figure; trailing commas hid years | Numeric-matching tests | Exclude years at extraction; never end a number on a separator |
| 14 | Accession numbers and "10-K" scanned as figures → phantom "unverified figure 24" | Router on real data | Strip identifiers before scanning |
| 15 | DuckDB connection used by two threads (lock released between two queries) | Parallel tool-call test | One critical section; 96-call concurrency regression test |
| 16 | Agent flagged tool-sourced figures as "uncited" though the prompt says they need no bracket | Scripted-agent test | Tool evidence counts as grounding |
| 17 | Extractive quoting repeated a sentence duplicated by chunk overlap | Real Apple 10-K output | De-duplicate quoted sentences |
| 18 | Advice detector missed "Is Amazon a good investment?" | Test-set expansion | Broader pattern + false-positive guards |
| 19 | BM25 tokenizer split "10-K" into "10" and "k" | Tokenizer tests | Form-name token rule |
| 20 | Notebook claims asserted, not computed ("leverage drives the highest ROEs" — false for NVIDIA) | Reading the executed output against the claim | Findings rewritten to match the data |
| 21 | Advice guardrail matched only the templated phrasing: all 4 naturally phrased advice requests ("Is now a good time to load up on Nvidia shares?") reached the extractive fallback | Natural-phrasing probe (`gold_v2_draft`) | Pattern broadened; 7 natural-phrasing tests plus 9 false-positive guards ("How many shares did Apple repurchase"). Re-run is in-sample |
| 22 | `indexing` and `retrieval` imported each other (`RetrievalFilters` lived in `retrieval`) | First draft of the import-linter contracts | Value object moved to `core`; contracts verified to fail on injected violations |
| 23 | Notebook 04 counted all 9 retrieval misses as "found at rank 2-8" (`if NaN:` is truthy in pandas) and printed "0 right-filing misses" | Reading the executed output against the evidence table beside it | `pd.isna`, plus an assertion that the bucket total equals the metric it explains |

## 3b. Natural-phrasing probe (router and RAG baseline on `gold_v2_draft`, 38 questions)

The templated `gold_v1` said the router scored 0.94. On differently worded questions it scores 0.69 (the
RAG baseline 0.27), a drop that only this probe could reveal. Of the 38 questions the rule-based grader
scored 26 (18 correct, 8 incorrect) and left 12 open-text questions unscored. The 8 incorrect answers:

| Failures | Cause | Status |
|---|---|---|
| 4 (advice) | Guardrail regex too narrow | Fixed (row 21); 0 of these 4 fail after the fix |
| 2 ("How many iPhones did Apple sell last quarter?", "What is Exxon's share price at this moment?") | Live-data or unreported-quantity questions: with no relevance floor the extractive fallback quotes unrelated text | **Not fixed** - a retrieval-score threshold or an LLM's abstention is the intended answer |
| 2 ("How profitable was Microsoft ... as a percentage of its revenue?", "What fraction of Apple's sales was left as gross profit?") | Ratio asked in words that name neither "margin" nor a ratio: the router falls back to plain revenue | **Not fixed** on purpose: fixing it means adding these phrasings to the rules, which would make the probe in-sample. Needs a fresh set |

Numbers are from `reports/runs/*-router-natural` (before the guardrail fix) and
`*-router-natural-postfix` (after).

## 4. Design changes forced by evidence

* **Reranker is opt-in** (ablation A1: better ordering, no recall gain, ~16× latency) — ADR-0002 amended.
* **Contextual header kept** (ablation A4: +0.31 BM25 recall@8, CI [+0.155, +0.483]).
* **Embedded Qdrant is for development**: it warns above 20,000 points, which is why the compose stack
  uses the server (`FINSIGHT_QDRANT_URL`).
