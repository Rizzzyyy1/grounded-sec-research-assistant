# Benchmark review decisions (2026-09-23)

These are evidence-based editorial changes to the **in-sample** `gold_v2_draft` probe. They do
not certify all 38 labels, create a new holdout, or make historical scores comparable to scores
on the revised questions. `data/eval/holdout_v1_draft.jsonl` remains unrun and unapproved.

| Row | Decision and evidence | Status |
|---|---|---|
| `nat-num-008` | Reworded to **net earnings attributable to P&G**. P&G's FY2026 10-K comparative FY2024 column reports $14.879bn attributable to P&G and $14.974bn consolidated net earnings. [Filing](https://www.sec.gov/Archives/edgar/data/80424/000008042426000103/pg-20260630.htm). | Wording corrected; label still needs independent reviewer approval. |
| `nat-ratio-009` | Reworded to **net profit margin** because the expected formula is FY2024 net income / revenue; generic "profitability percentage" was ambiguous. [Microsoft filing](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm). | Wording corrected. |
| `nat-ratio-011` | Reworded to **return on year-end shareholders' equity**. The two inputs are from accessions `0000034088-26-000045` and `0000034088-25-000010`. This is not average-equity ROE. | Definition clarified; both input periods still need reviewer confirmation. |
| `nat-txt-028`, `034`, `036` | Removed broad `gold_sources` from retrieval scoring pending a precise Note 15, Market Risk Management, or Note 19 passage respectively. Stub Items and whole appendices do not provide claim-specific relevance labels. | Answer questions retained; retrieval labels pending. |
| `nat-txt-031` | Removed Exxon Item 1 because no specific climate-regulation passage was identified there. Item 1A remains a draft candidate. | Further passage review pending. |
| `nat-txt-038` | Reworded from "biggest cost pressures" to disclosed operational and supply-chain risks. The reviewed P&G risk passage lists potential disruptions and adverse effects but does not rank cost pressures. | Wording corrected; Item 7/1A labels still pending review. |

The SEC source documents for previously uncatalogued selected facts are [Nvidia accession
`0001045810-26-000021`](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm),
[Walmart accession `0000104169-26-000055`](https://www.sec.gov/Archives/edgar/data/104169/000010416926000055/wmt-20260131.htm),
[P&G accession `0000080424-26-000103`](https://www.sec.gov/Archives/edgar/data/80424/000008042426000103/pg-20260630.htm),
and [Microsoft accession `0001193125-26-323660`](https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/msft-20260630.htm).
Ingestion now looks up **exact selected-fact accessions** in SEC submissions and adds their
metadata to the filing catalogue. It reports accessions that SEC submissions cannot resolve; it
never fabricates a URL. The local fact store is not tracked, so users must rerun ingestion to
populate these catalogue rows in their own store.

## Citation judgments from the small manual sample

* P&G: the cited passage supports a narrow potential adverse-impact statement. It does not
  support the historical question's "biggest cost pressures" ranking; the bullets need their
  own claim-level evidence.
* Tesla: S4–S6 support the four specific litigation bullets; S1–S3 support none of them.
  A valid URL and topical similarity do not make a citation claim-specific.

These judgments apply to the reviewed answers only. The audit sample is too small for a
population-level citation-precision estimate, and the production attribution code was not tuned
to these known examples.

## Holdout still awaiting review

Before the first run, an independent reviewer should verify all 20 labels against actual SEC
filings, confirm the five qualitative passages substantively answer their questions, and decide
whether `hv1-ratio-009` means long-term debt-to-equity and whether `hv1-ratio-010` explicitly uses
average assets. Freeze the approved file and scoring rules before running any system on it.
