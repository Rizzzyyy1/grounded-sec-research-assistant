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
| Phrasing | The tool router's rules were built against templated wording: 0.94 on templated test questions, 0.69 on 38 natural rewrites (before the guardrail fix). Ratios asked in words that avoid the metric name ("what fraction of sales was left as gross profit") fall back to plain revenue - and the LLM agent has the identical weakness for the identical reason (it never recognises the question needs a ratio tool at all), confirmed on the same question (ERROR_ANALYSIS 3e) | Measured, not fixed: fixing it against the probe set would make the probe in-sample. The LLM agent, now run against a free local model, scores statistically indistinguishably from the router on this same probe (0.885 vs 0.846, ERROR_ANALYSIS 3e) - `--llm claude` remains unmeasured |
| Unanswerable | Without a relevance floor the extractive fallback answers live-data questions ("share price at this moment") by quoting unrelated text | Documented in ERROR_ANALYSIS 3b; an LLM's abstention or a retrieval-score threshold is the remedy |
| Local-model quality | `llama3.2:3b` (the free `--llm ollama` default) still sometimes cites a source_id no tool ever produced, paraphrases a qualitative answer from training-data familiarity instead of the retrieved passages, or narrates a fake tool call as plain text instead of issuing one - observed on different questions on different runs, i.e. intermittent, not tied to one question. On a comparative question where only one side is citable, it can also hedge into an abstention while still (correctly, without fabrication) stating both real numbers - a safer failure than before, but still scored incorrect by a strict grader (ERROR_ANALYSIS 3d, `nat-cmp-016`) | Documented, not patched around (ERROR_ANALYSIS 3c/3d); an invalid citation is never shown to the user as if valid (`generation/citations.py::repair_citations`) and citation/numeric validators catch the rest as warnings; a bigger local model (`FINSIGHT_OLLAMA__MODEL`) or `--llm claude` are the two ways to reduce this, neither measured here |
| No growth/trend tool | A `trend` question ("by what percentage did X change") has no dedicated tool, so the model computes the percentage itself from two `get_financial_metric` calls - outside ADR-0003's "numbers come from tools" guarantee, and sometimes arithmetically wrong (observed: a real 55.2% computed as "54%") | Quantified in a natural-probe run: 2 of 28 non-abstained answers (ERROR_ANALYSIS 3e's citation-hygiene breakdown). A `compute_growth`-style tool mirroring `compute_ratio`'s formula+citation shape would close this; not built here |
| Missing filing metadata | A fact's "latest restated value" can trace to a filing accession `finsight ingest` never catalogued (e.g. a later 10-K reporting an earlier year as a comparative column) - 8 of 60 (ticker, year) revenue facts checked (13.3%, concentrated in the most recent 1-2 fiscal years for 4 of 12 companies) have no catalogued filing and so cannot be cited at all | Never papered over: `agent/tools.py::_register_fact` returns no citation label rather than guessing a URL, and the answer states the figure plainly, uncited (ERROR_ANALYSIS 3d). Extending the filing catalogue to cover every accession `fact_versions` references is the natural fix; not done here - it needs a live EDGAR fetch per missing accession |
| Reproducibility | A local model's default sampling gave a different tool call for an identical question on consecutive runs | `generation/ollama.py` sets `temperature=0, seed=0`; confirmed deterministic, though still not bit-exact across every backend/batch size |
| API / UI concurrency | `finsight serve` handles one `/v1/query` request at a time per worker, synchronously; a slow local-model multi-step answer (observed p50 ~10s, p95 ~40s per LLM call, up to `max_agent_steps`=8 calls) can occupy a worker for minutes in the worst case, and `/v1/query/stream` still computes the full answer before sending any event (no live token/tool streaming) | Acceptable for a single-user local demo; not load-tested with `--llm ollama` (the load test in `reports/load_test.md` used no LLM). Real streaming is future work |
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
  today"-style questions with an irrelevant passage. The LLM agent, run for the first time against a free
  local model (`--llm ollama`), does abstain correctly on both such questions in the smoke test
  (ERROR_ANALYSIS 3c) - a small positive result, though not yet a systematic measurement across the gold set.
* **The gold set is templated and automatically derived.** Scores are an upper bound on natural phrasing.
  The router's rules were developed against the same phrasing, so its 0.94 is optimistic. The LLM agent
  (free local model) scores statistically indistinguishably from the router on the same natural-phrasing
  probe (paired diff -0.038, CI crosses zero) - it does not (yet, at this model size) close the gap either.
* **The test split has been inspected** during error analysis and is no longer a clean holdout.
* **Not validated live:** the Claude agent specifically (`--llm claude`, no API key available), the LLM
  judge, the refusal-fallback request shape, Docker images. The agent *has* been run live against a free
  local model (`--llm ollama`, ADR-0011) - see [RESULTS](../reports/RESULTS.md) and ERROR_ANALYSIS 3c -
  which is evidence about the architecture, not about Claude's quality.
