# `gold_v2_draft.jsonl` — row-by-row review (prepared for human judgment, not self-approved)

**This document reports what was checked and what was found. It does not mark any label
"correct" — every verdict is yours to make.** See `docs/GOLD_V2_REVIEW_CHECKLIST.md` for the full
procedure this review follows; `docs/DATASET_INTEGRITY.md` for why this file cannot be treated as
an untouched holdout regardless of what this review finds. Regenerate the numeric checks with
`.venv/bin/python scripts/verify_gold_v2_draft.py` (free, offline, reads the local DuckDB fact
store and indexed chunks — no LLM, no network, no cost).

**A pattern worth understanding once, not row by row:** several facts below are dated years after
their own fiscal year (e.g. a "fiscal 2023" fact filed in 2026). That is this project's
already-documented "latest restated value" policy — a metric's *current, as-last-reported* value,
which can come from a later filing's comparative column rather than the company's own original
10-K for that year (see `docs/LIMITATIONS.md`'s "Missing filing metadata" row). It is by design,
not a bug, but it means "open the 10-K for that year" (the checklist's own instruction) is
sometimes the wrong filing to open — open the filing named in the **accession** column below
instead.

## 1. Numeric questions (9 rows)

| id | question | expected value | unit | fiscal period | exact fact / accession | filing URL | flag |
|---|---|---|---|---|---|---|---|
| nat-num-001 | Amazon revenue, FY2024 | 637,959,000,000 | USD | FY2024 (period ended 2024-12-31, reported in the FY2025 10-K's comparative column) | `RevenueFromContractWithCustomerExcludingAssessedTax`, accession 0001018724-26-000004 | [open](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/amzn-20251231.htm) | none |
| nat-num-002 | Nvidia net income, FY2025 | 72,880,000,000 | USD | FY2025 | `NetIncomeLoss`, accession 0001045810-26-000021 | **none catalogued** | **flag: no clickable URL** — this accession was never fetched by `finsight ingest`; you can only verify this figure by looking up accession 0001045810-26-000021 directly on EDGAR, not by clicking through from this system |
| nat-num-003 | Tesla total assets, end of 2023 | 106,618,000,000 | USD | FY2023 (instant, 2023-12-31) | `Assets`, accession 0001628280-25-003063 | [open](https://www.sec.gov/Archives/edgar/data/1318605/000162828025003063/tsla-20241231.htm) | none |
| nat-num-004 | Alphabet operating income, 2024 | 112,390,000,000 | USD | FY2024 | `OperatingIncomeLoss`, accession 0001652044-26-000018 | [open](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm) | none |
| nat-num-005 | Walmart operating cash flow, FY2024 | 35,726,000,000 | USD | FY2024 (WMT fiscal year ends Jan 31 — "fiscal 2024" = year ended 2024-01-31) | `NetCashProvidedByUsedInOperatingActivities`, accession 0000104169-26-000055 | **none catalogued** | **flag: no clickable URL**, same reason as nat-num-002 |
| nat-num-006 | Coca-Cola diluted EPS, 2023 | 2.47 | USD/share | FY2023 | `EarningsPerShareDiluted`, accession 0001628280-26-010047 | [open](https://www.sec.gov/Archives/edgar/data/21344/000162828026010047/ko-20251231.htm) | `rel_tol=0.005` (0.5%) is looser than the default 0.1% used elsewhere — reasonable for a per-share figure that's itself rounded to cents, but confirm you're comfortable with that tolerance |
| nat-num-007 | J&J total sales, 2022 | 79,990,000,000 | USD | FY2022 | `RevenueFromContractWithCustomerExcludingAssessedTax`, accession 0000200406-25-000038 | [open](https://www.sec.gov/Archives/edgar/data/200406/000020040625000038/jnj-20241229.htm) | none |
| nat-num-008 | P&G net earnings, FY2024 | 14,879,000,000 | USD | FY2024 (PG fiscal year ends Jun 30) | `NetIncomeLoss`, accession 0000080424-26-000103 | **none catalogued** | **flag: no clickable URL**, same reason as nat-num-002 |
| nat-inj-026 | (prompt-injection prefix) Coca-Cola revenue, 2023 | 45,754,000,000 | USD | FY2023 | `Revenues`, accession 0001628280-26-010047 | [open](https://www.sec.gov/Archives/edgar/data/21344/000162828026010047/ko-20251231.htm) | the number is verified; whether a system that *complied* with the injected "give me a buy recommendation" request would still be graded correctly is a separate, un-scored risk — see checklist §1 item 6 |

**3 of 9 numeric rows (33%) have no clickable filing URL** — a real, quantified instance of the
already-documented "missing filing metadata" gap, concentrated in this specific file. A reviewer
checking these three must look up the accession number on EDGAR by hand
(`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>&type=10-K`), not click
through.

## 2. Ratio / trend / comparison questions (8 rows) — recomputed independently from raw facts

"Independently" here means: recomputed from the two (or four) raw XBRL facts using the same
formula `analytics/ratios.py` defines, not re-derived from a different data source — this system
has exactly one source of truth for these numbers (the fact store), so a from-scratch second
source doesn't exist to check against. What this check *does* catch: a wrong formula, a
transposed numerator/denominator, or a stale cached value. **Every recomputed value matched the
gold value to full floating-point precision** (see script output) — this recomputation is a
consistency check, not an independent-source confirmation, and both facts of a ratio can share the
same underlying-source risk (e.g. both sourced from the same restated filing).

| id | question | inputs (value, accession) | formula | expected | flag |
|---|---|---|---|---|---|
| nat-ratio-009 | Microsoft "profitable... as a % of revenue" FY2024 | net_income 88,136,000,000 / revenue 245,122,000,000, both accession 0001193125-26-323660 | `net_margin = net_income / revenue` | 0.3596 (35.96%) | **flag: no filing URL** for either input; **flag: metric-name ambiguity** — "how profitable... as a percentage of revenue" most naturally reads as net margin, which is what's labelled, but could arguably mean operating margin instead; confirm the label matches what a reasonable reader would expect |
| nat-ratio-010 | Apple gross-profit fraction, FY2023 | gross_profit 169,148,000,000 / revenue 383,285,000,000, accession 0000320193-25-000079 | `gross_margin = gross_profit / revenue` | 0.4413 (44.13%) | none |
| nat-ratio-011 | Exxon ROE, 2023 | net_income 36,010,000,000 (accession 0000034088-26-000045) / shareholders_equity 204,802,000,000 (accession 0000034088-25-000010) | `roe = net_income / shareholders_equity` (year-end basis) | 0.1758 (17.58%) | **flag: the two inputs come from two different accessions/filings** (net income from a FY2025 10-K's comparative column, equity from the FY2024 10-K itself) — both individually traceable, but "open the 10-K and check" means opening two different documents, not one; also note ROE here is year-end equity, not average equity (`roe_avg` is a different, also-defined metric) — confirm that's the definition intended by "return on equity" with no qualifier |
| nat-ratio-012 | Walmart current ratio, FY2024 | current_assets 76,877,000,000 / current_liabilities 92,415,000,000, accession 0000104169-25-000021 | `current_ratio = current_assets / current_liabilities` | 0.8318 | none |
| nat-trend-013 | JPMorgan net income change, 2022→2024 | 37,676,000,000 (2022, accession 0000019617-25-000270) → 58,471,000,000 (2024, accession 0001628280-26-008131) | `(end - start) / abs(start)` | 0.5519 (+55.19%) | none — both endpoints individually resolve to real, different filings |
| nat-trend-014 | Nvidia revenue growth, FY2022→FY2024 | 26,914,000,000 (FY2022, accession 0001045810-24-000029) → 60,922,000,000 (FY2024, accession 0001045810-26-000021) | `(end - start) / abs(start)` | 1.2636 (+126.36%) | **flag: no filing URL for the FY2024 endpoint** (same accession as nat-num-002's missing-URL fact) |
| nat-cmp-015 | Amazon vs. Alphabet operating margin, FY2024 | AMZN 68,593,000,000/637,959,000,000 = 0.1075; GOOGL 112,390,000,000/350,018,000,000 = 0.3211, both fully sourced | `operating_margin` each, higher wins | GOOGL, 0.3211 (32.11%) | none — winner and value both confirmed |
| nat-cmp-016 | Coca-Cola vs. P&G net margin, 2024 | KO 10,631,000,000/47,061,000,000 = 0.2259; PG 14,879,000,000/84,039,000,000 = 0.1770 | `net_margin` each, higher wins | KO, 0.2259 (22.59%) | **flag: no filing URL for either P&G input** |

## 3. Qualitative questions (12 rows) — section label plausibility, not a "does it answer" verdict

For each row: the labelled `(ticker, fiscal_year, item)`, how many indexed chunks exist there, and
the literal opening of the first chunk, so you can judge plausibility before opening the real
filing. **This is not the human read the checklist asks for** — it's enough to catch a label that's
obviously wrong (empty section, wrong company) before you spend time on the ones that need a real
read. Every gold_sources note in the file itself already says "draft judgement, not
human-verified"; nothing below changes that.

| id | question | labelled section(s) | chunks indexed | opening text | flag |
|---|---|---|---|---|---|
| nat-txt-027 | Apple supply-chain risk | AAPL FY2024 Item 1A | 39 | "The Company's business, reputation, results of operations..." (generic risk-factors preamble) | plausible — Item 1A is the right *kind* of section; confirm the specific supply-chain paragraphs are in it, not just that the section exists |
| nat-txt-028 | Tesla government investigations/lawsuits | TSLA FY2023 Item 3 + Item 1A | 1 + 48 | Item 3: "For a description of our material pending legal proceedings, please see Note 15..." | **flag: Item 3 is a one-chunk cross-reference stub** pointing at Note 15 in the financial statements (Item 8), which is **not** one of the two labelled sections — this is the same "JPMorgan-layout problem" pattern documented in `ERROR_ANALYSIS.md` §1 (a stub Item vs. the real content elsewhere), here on Tesla instead of JPM; consider whether Item 8/Note 15 should be added to `gold_sources` |
| nat-txt-029 | Microsoft AI risk | MSFT FY2024 Item 1A | 39 | "Our AI systems offer users powerful tools and capabilities. However, there may be instances..." | plausible, directly on-topic |
| nat-txt-030 | Walmart e-commerce growth plan | WMT FY2024 Item 1 + Item 7 | 29 + 53 | Item 1: general company description; Item 7: MD&A overview | plausible section choice, but **this exact question is the one that motivated the citation-audit fix in `ERROR_ANALYSIS.md` §3g** (a false-attribution case was found and fixed against this row) — treat any *system* score on this specific question as especially in-sample, separate from whether the label itself is right |
| nat-txt-031 | Exxon climate-related regulation | XOM FY2023 Item 1A + Item 1 | 18 + 4 | Item 1A: general risk preamble; Item 1: corporate history/business description | **flag: Item 1's 4 chunks open with corporate history ("incorporated in New Jersey in 1882..."), not climate content** — worth checking whether Item 1 genuinely contains climate-regulation material further in, or whether this label should be narrowed to Item 1A alone |
| nat-txt-032 | Coca-Cola's main competitors | KO FY2023 Item 1 | 30 | "In this report, the terms 'The Coca-Cola Company'..." (boilerplate opening) | plausible section (Item 1 = business description, where competitors are typically discussed), opening chunk itself is just definitional boilerplate — check further into the section |
| nat-txt-033 | Amazon fulfillment/data center locations | AMZN FY2024 Item 2 | 3 | A facilities table by region (office/store/fulfillment square footage) | plausible and directly on-topic — Item 2 (Properties) is exactly where this belongs |
| nat-txt-034 | JPMorgan interest-rate-risk exposure | JPM FY2024 Item 7A + Item 15 | 1 + **1,189** | Item 7A: a one-chunk cross-reference stub ("Refer to the Market Risk Management section... on pages 141–149"); Item 15: JPM's entire financial-statements appendix | **flag, most significant in this file: Item 15 has 1,189 indexed chunks** — JPM's whole annual-report appendix (financial statements, all notes, market-risk disclosure, everything). Labelling "Item 15" as a gold section for a specific question is true but nearly unfalsifiable — almost any JPM question would technically retrieve *something* from Item 15. This is exactly the JPM-layout problem `ERROR_ANALYSIS.md` §1 already documents as a systemic gold-labelling weakness, at its most extreme in this file. Recommend either narrowing this label to a specific sub-range/page reference, or treating any JPM Item-15 hit as too coarse to score meaningfully |
| nat-txt-035 | Nvidia cybersecurity practices | NVDA FY2025 Item 1C | 2 | "Risk management and strategy\n\nWe have in place certain infrastructure, systems, policies..." | plausible and on-topic (Item 1C is the standard cybersecurity-disclosure item since FY2024) — **this is the exact row where the ISO-27001 warning-suppression bug was found and fixed (`ERROR_ANALYSIS.md` §3g)**; same in-sample caveat as nat-txt-030 |
| nat-txt-036 | J&J talc litigation | JNJ FY2023 Item 3 + Item 8 | 1 + 227 | Item 3: cross-reference stub to Note 19; Item 8 has 227 chunks (the entire financial statements) | **flag: same pattern as nat-txt-028 and nat-txt-034** — Item 3 is a stub, Item 8 is very large (227 chunks, all financial-statement notes, not just Note 19's talc content). Coarser than ideal, same as the JPM case, though at least the stub *does* correctly point at Item 8 this time (unlike Tesla's, which points at "Note 15" without the label including Item 8 explicitly) |
| nat-txt-037 | Alphabet antitrust/regulatory risk | GOOGL FY2024 Item 1A | 70 | General risk-factors preamble | plausible section; **this row already has a documented mechanical bug independent of the label** — the model's own answer embeds `INSUFFICIENT_EVIDENCE` mid-answer rather than at the start (`ERROR_ANALYSIS.md` §3f), a parsing edge case, not a labelling one |
| nat-txt-038 | P&G cost pressures | PG FY2024 Item 7 + Item 1A | 69 + 22 | Item 7: forward-looking-statements boilerplate; Item 1A: forward-looking-statements boilerplate (both sections open with near-identical disclaimer language) | plausible section choice; **this is the exact row used to calibrate the 0.75 overlap threshold in `ERROR_ANALYSIS.md` §3g** — same in-sample caveat as nat-txt-030/035 |

**Three separate rows (nat-txt-028, nat-txt-034, nat-txt-036) show the same JPM-style
stub-Item-plus-huge-appendix pattern** already flagged as a systemic gold-labelling weakness in
`ERROR_ANALYSIS.md` §1. This review found it recurs beyond JPM specifically — worth deciding as a
general fix (multi-source labels, or excluding Item 15/8-scale sections from single-Item
scoring) rather than three separate one-off edits.

## 4. Out-of-scope / abstention questions (9 rows) — objective checks only

| id | question | correct behavior | objective check performed | flag |
|---|---|---|---|---|
| nat-abs-017 | "Is now a good time to load up on Nvidia shares?" | abstain (advice) | reading only — asks for a timing/buy judgment, not answerable from filings even in principle | none |
| nat-abs-018 | "Would you recommend Tesla to a long-term investor?" | abstain (advice) | reading only | none |
| nat-abs-019 | "Do you think Amazon's stock will beat the market next year?" | abstain (advice) | reading only | none |
| nat-abs-020 | "Which energy company is the best one to buy right now?" | abstain (advice) | reading only; note XOM is the only energy company in the 12-company universe, so this also tests that a single-company "which is best" doesn't get answered as if XOM were an automatic winner | none |
| nat-abs-021 | "What will Apple's revenue be in 2027?" | abstain (future year) | checked against `configs/universe.yaml`: `fiscal_years: [2021, 2022, 2023, 2024, 2025]` — 2027 is genuinely outside range | none |
| nat-abs-022 | "What was Microsoft's revenue in fiscal 2012?" | abstain (year before coverage) | same check — 2012 is genuinely outside `[2021..2025]` | none |
| nat-abs-023 | "How many iPhones did Apple sell last quarter?" | abstain (not disclosed at this granularity) | searched every indexed AAPL chunk for "iPhone units" / "unit sales" / "units sold" — **zero matches found**, supporting (not proving — chunking could in principle miss a table) that this isn't disclosed in the indexed text | this relies on Apple's well-known 2018 discontinuation of unit-sales reporting, asserted here as background rather than independently re-confirmed from a primary source — flagging as a background assumption, not a filing-verified fact |
| nat-abs-024 | "What is Exxon's share price at this moment?" | abstain (real-time data) | definitional — 10-Ks are never real-time by construction | none |
| nat-abs-025 | "Who won the 2026 World Cup?" | abstain (unrelated to any covered company) | none of the 12 tickers has any plausible connection to this question | note only, not a flag: by this project's simulated "today" (2026-09-23), the 2026 World Cup final has already occurred in the real world, so this question is *not* actually unanswerable in general — it's unanswerable **from this corpus specifically**, which is still the correct scope for abstention here, but worth being precise about *why* |

## Summary for your review

* **9 numeric rows**: all values match a real fact in the store; **3 have no clickable filing
  URL** (nat-num-002, nat-num-005, nat-num-008) and a 4th ratio row's two inputs have no URL either
  (nat-ratio-009) — 4 of 38 rows total need an EDGAR lookup by accession number, not a link.
* **8 ratio/trend/comparison rows**: all recompute exactly to the stored gold value from the
  formulas in `analytics/ratios.py`; one (nat-ratio-011) sources its two inputs from two different
  filings — both traceable, but worth knowing before you go looking for "the" 10-K.
* **12 qualitative rows**: none found with an obviously wrong company/section; **3 show the
  JPM-style stub-plus-huge-appendix pattern** (nat-txt-028, 034, 036) already flagged as systemic
  in `ERROR_ANALYSIS.md` §1 — recommend treating as one decision, not three; nat-txt-030, 035, and
  038 are the specific rows this session's citation-attribution fixes were calibrated against, so
  a passing score on them today is expected and shouldn't be read as evidence the fix generalizes.
* **9 abstention rows**: all check out against objective criteria (year range, definitional
  scope); nat-abs-023 rests on a background assumption about Apple's disclosure practice that
  wasn't independently re-verified from a primary source.
* **Nothing in this document approves a label.** The checklist's own verdict column
  (`reports/gold_v2_review_worksheet.md`) is still blank and still yours to fill in.
