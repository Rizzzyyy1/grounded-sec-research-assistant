# Human review checklist for `gold_v2_draft.jsonl`

**This document tells you what to check and how. It does not check anything itself, and nothing
in this repository marks a label "verified" on its own — that is a judgment only you can make.**
Claude prepared the worksheet and the verification commands below; Claude has not approved any
label, and should not be treated as having done so.

## Why this exists

`data/eval/gold_v2_draft.jsonl` (38 questions, `provenance="draft"` on every row) rewrites parts
of `gold_v1` in natural phrasing to test whether the system's measured performance survives
questions that were not templated. Numeric answers are correct by construction (computed from the
same XBRL fact store the tools read), but the *sections* named for qualitative questions are one
person's judgement, made without a second reviewer, and never checked against the actual filing
text. Until a human works through every row, this file stays a **probe**, not a **gold set** — see
[EVALUATION.md §2.2](EVALUATION.md#22-gold_v2_draft-a-natural-phrasing-probe-not-a-gold-set) and
[ERROR_ANALYSIS.md §3b](ERROR_ANALYSIS.md). The work below is what turns it into `gold_v2`: a
holdout worth trusting.

**Start here:** `reports/gold_v2_draft_review.md` - a prepared pass through every row (every
numeric/ratio value independently recomputed from the fact store with its exact accession and
filing URL, every qualitative section's chunk count and opening text, every abstention checked
against objective criteria, uncertain items flagged) to save you the mechanical legwork before you
do the actual judgment calls. Then `reports/gold_v2_review_worksheet.md` (regenerate with
`.venv/bin/python scripts/build_gold_v2_review_worksheet.py` if the draft file changes) for the
blank verdict/notes columns to fill in as you go.

## 1. Numeric questions (23 of 38: `numeric`, `computed_metric`, `trend`, `comparison`, and the one
   `numeric` row with an injection prefix)

For each row:

1. Run the suggested command from the worksheet, e.g.:
   ```bash
   finsight ask "How much revenue did Amazon bring in during fiscal 2024?" --llm extractive --system router
   ```
   This is free, deterministic, and prints the exact XBRL tag and 10-K accession the number came
   from — it is not an independent source, but it tells you *which filing and line* to check.
2. Open that filing (the accession number resolves to
   `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&...` or use `finsight filings
   <ticker>` to list it) and confirm the figure against the actual income statement / balance
   sheet / cash flow statement.
3. Confirm the **question genuinely means what the label assumes** — e.g. "What did Nvidia earn"
   could mean net income or operating income; make sure the wording is unambiguous enough that a
   reasonable person would expect the labelled metric, not a different one that happens to also be
   plausible.
4. For `computed_metric`/`trend`/`comparison` rows, independently recompute the ratio or the
   percentage change from the two raw figures (`finsight ask ... --system router` also computes
   ratios) rather than trusting the stored value came from the right formula.
5. Check `rel_tol` (currently `0.001` on every row, i.e. 0.1%): reasonable for a figure copied
   directly from XBRL, but too tight if a question implies a rounded or approximate answer ("about
   how much"). Loosen individual rows if the question's own phrasing invites rounding.
6. For the injection row (`nat-inj-026`): confirm the *only* thing scored is the legitimate
   sub-question's numeric answer, and that a system which complied with the injected instruction
   (gave a buy recommendation) would score as **wrong** some other way in your own manual read of
   its output — the automated grader only checks the number, so a system could technically comply
   with the injection and still pass numeric scoring. Note this if you want a stricter check later.

## 2. Qualitative questions (12 of 38, `gold_sources` non-empty)

For each row:

1. Open the filing at the labelled `(ticker, fiscal_year, item)` and read enough of it to judge:
   does this section **actually, substantially** answer the question, or does it just mention
   related words?
2. Check for a **better or additional** section the label missed. Several rows already list two
   sections (e.g. `nat-txt-034`: JPM Item 7A *and* Item 15, because JPM's real content sits in the
   Item 15 appendix — see ERROR_ANALYSIS §1 for why this matters generally); if you find a case
   where the true answer lives somewhere the label doesn't mention at all, add it or replace the
   label.
3. Watch specifically for the **JPMorgan-layout problem**: any JPM row's real content may be under
   Item 15, not the Item number a generic question implies. This is a known, documented failure
   mode - it is exactly the kind of thing this review should catch, not paper over.
4. Decide whether the question is answerable from **one filing year** or needs to say so more
   precisely - a question like "how does Microsoft talk about AI risk" could plausibly be answered
   from several years' filings; if the label pins one fiscal year, make sure that's deliberate.
5. If you can't tell without reading the entire Item, that's useful information too: note it as
   "ambiguous - needs a narrower question" rather than forcing a label.

## 3. Out-of-scope questions (9 of 38, `abstain=true`)

For each row, decide and write down **why** abstention is correct - don't just accept the label:

* **Advice** (`nat-abs-017` – `020`): should decline regardless of phrasing subtlety. Check none of
  them could be read as a legitimate factual question in disguise (e.g. "is X a good investment"
  vs. "what does X's 10-K say about its own investment risks" - the second is answerable).
* **Out of corpus coverage** (`nat-abs-021` future year, `nat-abs-022` a year before the universe's
  2021 start): confirm the year genuinely falls outside `configs/universe.yaml`'s `fiscal_years`
  range, not just "seems old."
* **Not disclosed at this granularity** (`nat-abs-023` unit sales by quarter): confirm the company
  genuinely does not report this (Apple stopped unit disclosures around 2018) - don't assume from
  general knowledge; check the actual filings if unsure.
* **Real-time data** (`nat-abs-024` current share price): should always abstain - filings are never
  real-time by design (see LIMITATIONS.md's "Out of scope" section).
* **Unrelated to any covered company** (`nat-abs-025`): confirm it has no plausible tie to the
  universe at all.

## 4. Every row, regardless of type

* Does the **wording read like something a person would actually type** (the entire point of this
  file), not a disguised template? Reword any that still feel templated.
* Is `required_tools` accurate for how you'd expect a well-behaved agent to answer it? This field
  is not scored automatically today, but a future tool-correctness metric (EVALUATION.md §3.2)
  would use it.
* Company/year coverage: are all 12 universe companies represented at least once across the file?
  (Tally it from the worksheet.) A file skewed toward 3-4 companies is a weaker probe.

## 5. Coverage gaps worth deciding on (not fixing silently)

These are judgment calls about what `gold_v2` *should* contain, not defects in what's there. Decide
and record your reasoning if you add anything:

* Only **one** prompt-injection example (`nat-inj-026`), and it is a `numeric` question - there is
  no qualitative-question injection example, and no example where the injected instruction is
  embedded *inside* retrieved document text rather than the user's own question.
* No **multi-hop** question that needs two tool calls chained together (e.g. "how did the ratio
  that grew the most last year compare to the same ratio at its closest competitor").
* No question with a **false premise** ("Since Apple's revenue fell last year, ..." when it did
  not) to test whether the system corrects the premise instead of answering as asked.

## 6. Recording the result

Once you've worked through a row:

* If the label is right as-is: mark it verified in the worksheet.
* If it needs a fix (wrong section, wrong tolerance, reworded question): edit
  `data/eval/gold_v2_draft.jsonl` directly (it's plain JSONL - one `GoldExample` per line, schema in
  `src/finsight/evaluation/datasets.py`), then re-run
  `.venv/bin/python scripts/build_gold_v2_review_worksheet.py` to refresh the worksheet.
* When every row is verified: bump `provenance` from `"draft"` to `"human"` for the rows you
  checked, rename the file to `data/eval/gold_v2.jsonl`, and update `docs/EVALUATION.md §2.2` and
  this file's own status line to say so. Re-run the evaluations that used the draft file
  (`agent-ollama-natural`, `router-natural-refresh`, `rag-extractive-natural-refresh` in
  `reports/runs/`) against the verified file and report the new numbers as an in-sample evaluation with reviewed labels. Label review
  does not undo development exposure or turn this set into a clean holdout. Use the separately
  proposed `holdout_v1_draft` only after the review described in `HOLDOUT_V1_DESIGN.md`.

## What this checklist is not

It is not a substitute for reading the filings. It is not a claim that any row is currently
correct or incorrect. It is not something Claude can complete on your behalf, because "does this
section actually answer the question" and "is this the right abstention call" are exactly the
judgments a human-verified gold set exists to certify.
