# `holdout_v1_draft.jsonl` — a genuinely fresh holdout, and how to keep it that way

**Do not run `finsight ask`, `finsight eval run`, or any agent/router invocation against this file
before its labels are reviewed.** Every value in it was computed directly from the XBRL fact store
and the indexed chunk list - the same read-only tooling `scripts/verify_gold_v2_draft.py` uses -
never by asking the system under test what it thinks the answer is. See "How the labels were
produced" below for exactly what was and wasn't run to build this file.

## Why a new file, not another pass over `gold_v2_draft`

`docs/DATASET_INTEGRITY.md` documents why `gold_v1` and `gold_v2_draft` can no longer produce a
first "does this generalize" measurement: `gold_v2_draft` is fully in-sample (used to find and fix
the advice-guardrail hole, the ratio-phrasing gap, and the citation-attribution threshold itself),
and `gold_v1 test`'s retrieval failures have been read even though its scoring stayed mechanical.
Re-running either file after a fix only ever answers "did the fix work on the data it was built
from" - never "does it work on data it wasn't."

## Selection method: avoid wording *and* answers already used to build recent fixes

For every question below, both of the following had to hold before it was included:

1. **No (company, metric, fiscal year) triple already asked in `gold_v1` or `gold_v2_draft`
   in a numeric/ratio/trend/comparison question.** Checked by reading every existing numeric
   row's ticker/metric/year against the candidate before adding it (e.g. `gold_v2_draft` already
   asks Apple's FY2023 gross margin and FY2024 revenue, so this file asks Apple's FY2022
   shareholders' equity and FY2024 asset turnover instead - a different metric, a different year,
   or both, for every row).
2. **No qualitative question on the same topic already used to develop a fix.** `gold_v2_draft`'s
   Apple question is about supply chain, Walmart about e-commerce, JNJ about talc litigation,
   Microsoft about AI risk, Nvidia about cybersecurity, JPMorgan about interest-rate risk, Exxon
   about climate regulation, Alphabet about antitrust, Coca-Cola about competitors, Amazon about
   facility locations, Tesla about litigation, P&G about cost pressures - every one of those was
   read line-by-line during the citation-attribution audit (`ERROR_ANALYSIS.md` §3g) or the
   guardrail/router fixes (§3b, §3e). This file asks about **different topics on some of the same
   companies** (Microsoft's cybersecurity practices instead of AI risk; Nvidia's export-control and
   supply-chain exposure instead of cybersecurity; Alphabet's AI risk instead of antitrust;
   Coca-Cola's health/regulatory risk instead of competitors) plus one company not previously asked
   any qualitative question at all in either file (JPMorgan's credit risk - deliberately **not**
   interest-rate risk, and deliberately scoped to Item 1A rather than Item 7A/15, to sidestep the
   JPM stub-Item problem documented in `ERROR_ANALYSIS.md` §1 and `reports/gold_v2_draft_review.md`
   §3 rather than reproduce it).
3. **Rephrased, not reused, even where the underlying fact is new.** No sentence here is a close
   paraphrase of an existing gold question; each was written independently for this file.

**What this file deliberately does NOT attempt:** full 12-company coverage (it reuses several
companies on new topics rather than finding a 13th), or matching `gold_v2_draft`'s exact type
distribution. "Small and genuinely fresh" was prioritized over "comprehensive."

## Composition (20 rows)

| Type | Count | Rows |
|---|---|---|
| numeric | 8 | `hv1-num-001` .. `008` — one company each: AAPL, AMZN, TSLA, XOM, JNJ, PG, WMT, KO |
| computed_metric | 2 | `hv1-ratio-009` (TSLA debt-to-equity), `hv1-ratio-010` (AAPL asset turnover) |
| trend | 1 | `hv1-trend-011` (TSLA revenue growth, FY2021→FY2023) |
| comparison | 1 | `hv1-cmp-012` (AAPL vs. MSFT gross margin, FY2024) |
| qualitative | 5 | `hv1-txt-013` .. `017` — MSFT cybersecurity, NVDA export controls/supply chain, GOOGL AI risk, KO health/regulatory risk, JPM credit risk |
| out_of_scope | 3 | `hv1-abs-018` .. `020` — an advice question naming an out-of-universe company (PepsiCo) alongside a covered one (Coca-Cola), a real-time-price question, a pre-2021 (out-of-coverage) year question |

## How the labels were produced (and what was, and wasn't, run)

**Run, read-only, no LLM, no cost:**
* `FactStore.get_fact(ticker, metric, year)` directly against `data/processed/facts.duckdb`, for
  every numeric row - the same call `get_financial_metric` makes internally, but invoked directly
  against the store, not through the agent or router.
* `analytics/ratios.py::compute_ratio` for every ratio/trend/comparison row, from those raw facts -
  the same formula the `compute_ratio`/`compare_companies` tools use, called directly.
* `read_chunks()` over `chunks.parquet` to count indexed chunks at each qualitative row's labelled
  `(ticker, fiscal_year, item)` and confirm the claimed topic's vocabulary (export controls,
  credit risk, artificial intelligence, health/obesity language) actually appears there - a
  keyword search over already-indexed text, not a retrieval-quality check.
* `configs/universe.yaml`'s `fiscal_years` range, to confirm the two out-of-coverage-year
  abstention rows are genuinely outside `[2021, 2025]`.

**Not run, and must not be run before label review:**
* `finsight ask` / `ResearchAgent` / the tool router, on any question in this file.
* `finsight eval run` against `holdout_v1_draft.jsonl`.
* Any comparison of what the agent *would* answer to what the label says.

## Independent checking, once labels are reviewed

The same two-layer approach `gold_v2_draft` already documents (`GOLD_V2_REVIEW_CHECKLIST.md`)
applies here, with one addition specific to this file's purpose:

1. **Numeric/ratio rows**: open the filing at the accession the fact-store lookup names (every row
   above resolved to a real accession during construction; re-run
   `scripts/verify_gold_v2_draft.py`-style lookups, or `finsight filings <ticker>`, to get it) and
   confirm the figure against the actual statement, exactly as the existing checklist directs.
2. **Qualitative rows**: read the labelled section and judge whether it substantively answers the
   question, exactly as the existing checklist directs.
3. **New for this file - before running anything**: confirm none of these 20 questions
   is, on a fresh read, actually a close paraphrase of something already used to build a fix (the
   selection method above was applied carefully, but a second pair of eyes is the actual check that
   makes "genuinely fresh" a claim rather than an intention).
4. **Only after (1)-(3) are done and any bad labels fixed**: this file may be run once, as the
   holdout it was designed to be, using `finsight eval run --gold data/eval/holdout_v1_draft.jsonl`.
   Report that first run's result plainly, labelled as the first genuinely out-of-sample number
   this project has produced - whatever it shows, including a worse one than `gold_v2_draft`'s
   in-sample numbers. Do not tune anything based on that first run and then re-run it; a second run
   after a fix built from its failures would make it exactly as in-sample as the files it was meant
   to replace, at which point it needs its own successor.
