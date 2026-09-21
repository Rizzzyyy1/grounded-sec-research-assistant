# FinSight — Limitations, Risks and Intended Use

Written in the style of a model card. It is updated as evaluation produces evidence; statements
about performance appear here only once they are measured.

## Intended use
Research assistance for analysts: locating, quoting and computing from **public SEC filings** of
the covered universe, with citations that can be checked.

## Out of scope / not intended
* **Investment advice**, recommendations, price targets or trading signals. The assistant is
  built to decline them.
* Companies or fiscal years outside `configs/universe.yaml`; non-US filers; scanned documents.
* Real-time or intraday information. Filings lag events by weeks to months.
* Legal, tax or accounting opinions.

## Known limitations
| Area | Limitation | Mitigation / status |
|---|---|---|
| Coverage | 12 companies × 5 years; not a market-wide tool | Universe is configuration; scaling is a config + compute change |
| Data | XBRL tags vary and get restated; some metrics do not exist for some issuers (e.g. banks have no gross profit) | Coverage matrix; explicit "not applicable" responses |
| Retrieval | Tables and footnotes are harder to retrieve than prose; Item filters mislead for JPM-style layouts | Tables chunked separately; measured per chunk type; documented in ERROR_ANALYSIS |
| Generation | LLMs can still misread a passage even with correct retrieval | Citation + numeric validators; abstention; faithfulness tracked |
| Evaluation | Gold set is small (~120); per-type results are noisy; LLM judge has bias | Reported with CIs; judge calibrated against human labels; frozen test split |
| Scale | One process: the API saturates at ~6-7 RAG queries/s and its cheap endpoints peak at ~80 req/s at concurrency 4, then degrade ([load test](../reports/load_test.md)). Embedded Qdrant is single-process | Multiple workers need the Qdrant *server* (`FINSIGHT_QDRANT_URL`, compose file) and a per-process DuckDB read connection; not built or measured |
| Time | Index reflects the last ingestion; later filings and amendments (10-K/A) may be missing | Manifest shows corpus date; answers state the filing they used |
| Phrasing | The tool router's rules were built against templated wording: 0.94 on templated test questions, 0.69 on 38 natural rewrites (before the guardrail fix). Ratios asked in words that avoid the metric name ("what fraction of sales was left as gross profit") fall back to plain revenue | Measured, not fixed: fixing it against the probe set would make the probe in-sample. The LLM agent is the intended answer and is unrun |
| Unanswerable | Without a relevance floor the extractive fallback answers live-data questions ("share price at this moment") by quoting unrelated text | Documented in ERROR_ANALYSIS 3b; an LLM's abstention or a retrieval-score threshold is the remedy |
| Language | English only | — |

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| User over-trusts an answer | Every claim cited to an openable passage; unverified numbers flagged; disclaimer on all outputs |
| Hallucinated figure | Numbers only from tools or quoted passages; consistency check; numeric accuracy is the headline metric |
| Prompt injection via document text | Retrieved text wrapped as data; adversarial tests in the gold set |
| Misuse as advice engine | Scope guardrail; out-of-scope slice in evaluation |
| Data-provider terms | Only SEC public data used; fair-access policy enforced in code |

## Ethical and legal notes
Public-record data only; no personal data is collected. Outputs are informational and **not
investment advice**. The author is not a registered investment adviser.

## Evidence (all from `reports/`, see [RESULTS](../reports/RESULTS.md) and [ERROR_ANALYSIS](ERROR_ANALYSIS.md))

* **Retrieval generalises less well than the dev number:** recall@8 0.90 (dev, n=29) vs **0.57 (held-out
  test companies, n=15, CI [0.33, 0.80])**. All misses are right-filing / wrong-Item.
* **Filing layout varies:** JPMorgan places its MD&A and financial statements under Item 15 (87% of its
  text); Item-based filters therefore under-serve bank-style filings.
* **Over-answering:** the extractive baseline and the router's text fallback answer "current share price
  today"-style questions with an irrelevant passage. Only an LLM (or a calibrated relevance floor) can
  abstain properly; that is **not yet measured** because the Claude agent has not been run live.
* **The gold set is templated and automatically derived.** Scores are an upper bound on natural phrasing.
  The router's rules were developed against the same phrasing, so its 0.94 is optimistic.
* **The test split has been inspected** during error analysis and is no longer a clean holdout.
* **Not validated live:** Claude agent, LLM judge, refusal-fallback request shape, Docker images.
