# FinSight — Data Sources, Schemas and Known Traps

## 1. Sources

| Source | Endpoint | Auth | Limits |
|---|---|---|---|
| Ticker → CIK map | `https://www.sec.gov/files/company_tickers.json` | none | shared 10 req/s cap |
| Filing index | `https://data.sec.gov/submissions/CIK##########.json` | none | idem |
| Structured facts | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | none | idem |
| Filing document | `https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/<primary_doc>` | none | idem |
| Prices (optional) | `yfinance` | none | best-effort; not a system of record |

**Fair-access rules** (https://www.sec.gov/os/accessing-edgar-data): send a descriptive
`User-Agent` including a contact email (`FINSIGHT_SEC__USER_AGENT`), stay ≤ 10 requests/second
(we cap at 8), and cache. Requests without a UA are rejected; abusive clients are throttled.
FinSight enforces all three in one place (`ingestion/edgar/`).

All data is public domain / public record. No scraping of paywalled or licensed content.

## 2. Corpus scope

`configs/universe.yaml` — 12 companies × FY2021–FY2025 10-Ks (60 filings), optionally + 10-Qs.
The selection is deliberately adversarial for the pipeline:

| Property | Where | Why it matters |
|---|---|---|
| Fiscal year ends Jan / Jun / Sep / Dec | NVDA, WMT / MSFT, PG / AAPL / rest | Naïve "fiscal_year = calendar year" logic breaks |
| Banks (no COGS / gross profit) | JPM | Forces metric fallbacks and "not applicable" handling |
| Very different vocabularies | Energy, pharma, retail, tech | Tests retrieval beyond one sector's jargon |
| Restatements / recasts | e.g. segment realignments | Tests "latest filed value wins" logic |

## 3. Canonical metrics (XBRL)

`ingestion/xbrl/concepts.py` maps company-specific `us-gaap` tags to canonical metrics with
**ordered fallbacks**. Initial set:

| Canonical metric | Statement | Type | Primary tag(s) (first that exists wins) |
|---|---|---|---|
| `revenue` | Income | duration | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet` |
| `cost_of_revenue` | Income | duration | `CostOfRevenue`, `CostOfGoodsAndServicesSold` |
| `gross_profit` | Income | duration | `GrossProfit` (else `revenue − cost_of_revenue`; `n/a` for banks) |
| `operating_income` | Income | duration | `OperatingIncomeLoss` |
| `net_income` | Income | duration | `NetIncomeLoss`, `ProfitLoss` |
| `eps_diluted` | Income | duration | `EarningsPerShareDiluted` |
| `total_assets` | Balance | instant | `Assets` |
| `total_liabilities` | Balance | instant | `Liabilities` (else `Assets − StockholdersEquity`) |
| `shareholders_equity` | Balance | instant | `StockholdersEquity` |
| `cash_and_equivalents` | Balance | instant | `CashAndCashEquivalentsAtCarryingValue` |
| `long_term_debt` | Balance | instant | `LongTermDebtNoncurrent`, `LongTermDebt` |
| `operating_cash_flow` | Cash flow | duration | `NetCashProvidedByUsedInOperatingActivities` |
| `capex` | Cash flow | duration | `PaymentsToAcquirePropertyPlantAndEquipment` |
| `free_cash_flow` | derived | duration | `operating_cash_flow − capex` |

Derived metrics are computed in `analytics/`, never stored as if reported. Every derived value
returns its **inputs and formula** so it can be audited in the answer.

## 4. Known traps in SEC data (and how we handle them)

These are the details that separate a demo from a system a financial analyst would trust.

1. **`fy`/`fp` in companyfacts describe the *filing*, not the *period*.** A FY2024 10-K also
   contains FY2022 and FY2023 comparatives, all tagged `fy=2024`. We **derive fiscal year from
   the period end date and the company's fiscal year end**, and use `frame`/`form` only as
   cross-checks.
2. **Restatements produce duplicate facts** for the same period from different filings. Policy:
   the value from the **latest `filed`** date wins; earlier values are retained in a history
   table for "as originally reported" queries.
3. **Duration vs instant.** Income/cash-flow items span a period; balance-sheet items are a point
   in time. Mixing them (e.g. ROE with a duration numerator and an instant denominator without
   averaging) is a classic error; the ratio layer refuses invalid combinations.
4. **Quarterly facts are cumulative.** A 10-Q reports YTD durations (3, 6, 9 months). Q4 is *not*
   reported at all; we derive `Q4 = FY − 9M YTD`, flagged `derived=True`.
5. **Units and scale.** Facts come in `USD`, `USD/shares`, `shares`, `pure`. HTML tables say
   "in millions" in a caption. Both are normalised; the unit travels with every value.
6. **52/53-week years.** Period lengths vary (364 vs 371 days); duration checks use tolerance.
7. **Tag drift.** Companies change tags between years (e.g. `SalesRevenueNet` →
   `Revenues`). The resolver tries fallbacks in order *per period*, and coverage is reported.
8. **Table-of-contents false positives.** "Item 1A" appears in the TOC before the real heading;
   the section splitter picks the occurrence followed by substantial text.
9. **iXBRL noise.** Inline XBRL filings embed hidden `ix:header` blocks and tagged spans;
   parsing keeps visible text only.
10. **Item renumbering.** Item 1C (Cybersecurity) exists only from FY2023; Item 6 is "[Reserved]"
    from FY2021. The section map is versioned by filing date.

11. **Successor registrants (holding-company reorganisations).** In 2026 ExxonMobil reorganised
    into a holding company: the `XOM` ticker moved to **ExxonMobil Holdings Corp** (CIK
    0002115436, no 10-K history) while every annual report up to FY2025 stayed under the old CIK
    (0000034088). Resolving the ticker to a CIK silently returned an entity with zero 10-Ks. The
    universe therefore supports `cik:` pinning, and the pipeline treats *zero filings for a
    universe member* as a **failure**, never a success.
12. **Equity is not one number.** `StockholdersEquity` excludes non-controlling interests, and
    *redeemable* NCI sits outside both liabilities and equity ("mezzanine"). We keep parent equity
    (ROE denominator), total equity, and mezzanine equity as separate metrics so the accounting
    identity reconciles (Tesla's 2019-2021 balance sheets need all three).
13. **The latest restated value wins.** FY2024 revenue in `facts` comes from the FY2025 10-K when
    it was re-reported there; the as-originally-reported value is in `fact_versions`.

## 5. Coverage report (Phase 1 deliverable)

`finsight coverage --write docs/coverage.md` builds a matrix over *(company × canonical metric ×
fiscal year)* recording whether a value was **reported**, **derived** by us, **n/a** (the metric
does not exist for the sector), **not reported** (declared in `configs/universe.yaml` with a
reason), or **missing** (a bug: applicable, unexplained, not found).

Measured on the 12-company universe ([full report](coverage.md)):

* **94.4 % raw availability** (value present ÷ every cell except sector-n/a), and
* **0 unexplained gaps** - every absent value is either n/a or has a written reason;
* a declared gap that later gets data is reported as *stale*, so explanations cannot rot.

## 6. Data quality checks (automated)

* Accounting identity: `Assets = Liabilities + Total equity (+ mezzanine)` within 1 %: **0 violations
  across 278 periods checked**. The report states how many periods were checked, so an empty join
  can never masquerade as a clean pass (an early version did exactly that).
* Cross-source: revenue from XBRL vs. the figure parsed from the filing's income-statement table
  agree within rounding (spot-checked on a sample; failures listed).
* Period sanity: duration facts cover ~1 year (FY) or ~1 quarter (Q) within tolerance.
* Monotonic fiscal years, no duplicate `(ticker, metric, period_end, unit)` after de-duplication.
* Section detection rate per filing (fraction of expected Items found) — regression-tested.
